from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import ValidationError

from .db import Database, verify_password
from .logic import ensure_valid_recipients, parse_recipients
from .mailer import send_test_email
from .market import AVAILABLE_SOURCES
from .models import AppConfig, ConfigUpdate, LoginRequest, PasswordResetRequest, TestEmailRequest, UserCreateRequest, UserStatusRequest
from .monitor import MonitorService

DB_PATH = os.getenv("GOLD_MONITOR_DB_PATH", "data/gold_monitor.db")
STATIC_INDEX = Path(__file__).resolve().parent.parent / "static" / "index.html"
db = Database(DB_PATH)
scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
services: dict[int, MonitorService] = {}
active_poll_intervals: dict[int, int] = {}


class ConnectionManager:
    def __init__(self) -> None:
        self.by_user: dict[int, set[WebSocket]] = {}

    @property
    def active_connections(self) -> set[WebSocket]:
        return self.by_user.setdefault(1, set())

    async def connect(self, websocket: WebSocket, user_id: int = 1) -> None:
        await websocket.accept()
        self.by_user.setdefault(user_id, set()).add(websocket)

    def disconnect(self, websocket: WebSocket, user_id: int) -> None:
        self.by_user.get(user_id, set()).discard(websocket)

    async def broadcast(self, user_id: int | dict, payload: dict | None = None) -> None:
        if payload is None:
            payload = user_id  # type: ignore[assignment]
            user_id = 1
        disconnected: list[WebSocket] = []
        for websocket in list(self.by_user.get(user_id, set())):  # type: ignore[arg-type]
            try:
                await websocket.send_json(payload)
            except Exception:  # noqa: BLE001
                disconnected.append(websocket)
        for websocket in disconnected:
            self.disconnect(websocket, user_id)


connections = ConnectionManager()


def _merge_config(current: AppConfig, update: ConfigUpdate) -> AppConfig:
    data = current.model_dump()
    if update.smtp is not None:
        smtp_update = update.smtp.model_dump(exclude_unset=True)
        if "recipients" in smtp_update and smtp_update["recipients"] is not None:
            smtp_update["recipients"] = ensure_valid_recipients(smtp_update["recipients"])
        if smtp_update.get("password") in (None, ""):
            smtp_update.pop("password", None)
        data["smtp"].update(smtp_update)
    if update.alert is not None:
        data["alert"].update(update.alert.model_dump(exclude_unset=True))
    if update.enabled_sources is not None:
        selected = [code for code in update.enabled_sources if code in AVAILABLE_SOURCES]
        data["enabled_sources"] = selected or ["zheshang"]
    if update.enabled is not None:
        data["enabled"] = update.enabled
    return AppConfig.model_validate(data)


def _public_config(cfg: AppConfig) -> dict:
    data = cfg.model_dump()
    data["smtp"]["has_password"] = bool(cfg.smtp.password)
    data["smtp"]["password"] = ""
    data["available_sources"] = [source.model_dump() for source in AVAILABLE_SOURCES.values()]
    return data


def _token_from_cookie(session: str | None) -> dict:
    item = db.get_session(session) if session else None
    if not item:
        raise HTTPException(status_code=401, detail="Login required")
    return item


async def current_user(session: Optional[str] = Cookie(default=None, alias="gold_monitor_session")) -> dict:
    return _token_from_cookie(session)


async def admin_user(user: dict = Depends(current_user)) -> dict:
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Administrator access required")
    return user


def _require_csrf(request: Request, user: dict) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    if request.headers.get("X-CSRF-Token") != user["csrf_token"]:
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


def _status_payload(user_id: int) -> dict:
    cfg = db.get_config(user_id)
    latest_run = db.get_latest_run_log(user_id)
    alerts = db.get_recent_alerts(1, user_id)
    job = scheduler.get_job(f"monitor_job_{user_id}")
    service = services.get(user_id)
    return {
        "enabled": cfg.enabled, "timezone": cfg.timezone, "product_sku": cfg.product_sku,
        "enabled_sources": cfg.enabled_sources, "available_sources": [s.model_dump() for s in AVAILABLE_SOURCES.values()],
        "poll_interval_seconds": cfg.alert.poll_interval_seconds,
        "fast_poll_interval_seconds": cfg.alert.fast_poll_interval_seconds,
        "active_poll_interval_seconds": active_poll_intervals.get(user_id, cfg.alert.poll_interval_seconds),
        "scheduler_running": scheduler.running, "next_run_time": job.next_run_time.isoformat() if job and job.next_run_time else None,
        "last_run": latest_run, "last_result": service.last_result if service else {}, "last_alert": alerts[0] if alerts else None,
    }


async def _broadcast_snapshot(user_id: int) -> None:
    await connections.broadcast(user_id, {"type": "snapshot", "status": _status_payload(user_id), "alerts": db.get_recent_alerts(200, user_id)})


def _next_poll(cfg: AppConfig, result: dict) -> int:
    return cfg.alert.fast_poll_interval_seconds if result.get("triggered") or result.get("near_threshold") else cfg.alert.poll_interval_seconds


def _ensure_service(user_id: int) -> MonitorService:
    if user_id not in services:
        services[user_id] = MonitorService(db, user_id)
    return services[user_id]


def _schedule_user(user_id: int, seconds: int | None = None) -> None:
    cfg = db.get_config(user_id)
    seconds = seconds or cfg.alert.poll_interval_seconds
    active_poll_intervals[user_id] = seconds
    job_id = f"monitor_job_{user_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    scheduler.add_job(_run_scheduled_check, "interval", seconds=seconds, id=job_id, args=[user_id], max_instances=1, coalesce=True, misfire_grace_time=30)


def _remove_user_job(user_id: int) -> None:
    job = scheduler.get_job(f"monitor_job_{user_id}")
    if job:
        scheduler.remove_job(job.id)
    active_poll_intervals.pop(user_id, None)


async def _run_scheduled_check(user_id: int) -> None:
    result = await _ensure_service(user_id).run_check(manual=False)
    _schedule_user(user_id, _next_poll(db.get_config(user_id), result.model_dump()))
    await _broadcast_snapshot(user_id)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db(os.getenv("ADMIN_USERNAME"), os.getenv("ADMIN_PASSWORD"), require_admin=True)
    if not scheduler.running:
        scheduler.start()
    for user in db.list_users():
        if user["is_active"] and db.get_config(user["id"]).enabled:
            _schedule_user(user["id"])
    scheduler.add_job(_run_cleanup_job, "cron", hour=3, minute=0, id="cleanup_job", max_instances=1, coalesce=True, misfire_grace_time=3600)
    _run_cleanup_job()
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)


def _run_cleanup_job() -> None:
    for user in db.list_users():
        cfg = db.get_config(user["id"])
        db.cleanup_old_records(cfg.alert.retention_days, user["id"])


app = FastAPI(title="积存金监测", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[], allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"], allow_headers=["Content-Type", "X-CSRF-Token"], allow_credentials=True)


@app.get("/")
async def index() -> FileResponse:
    if not STATIC_INDEX.exists():
        raise HTTPException(status_code=404, detail="UI file not found")
    return FileResponse(STATIC_INDEX)


@app.post("/api/auth/login")
async def login(payload: LoginRequest, response: Response) -> dict:
    user = db.get_user_by_username(payload.username)
    if not user or not user["is_active"] or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token, csrf = db.create_session(user["id"])
    secure = os.getenv("GOLD_MONITOR_COOKIE_SECURE", "0") == "1"
    response.set_cookie("gold_monitor_session", token, httponly=True, secure=secure, samesite="strict", max_age=7 * 86400)
    response.set_cookie("gold_monitor_csrf", csrf, httponly=False, secure=secure, samesite="strict", max_age=7 * 86400)
    return {"user": {"id": user["id"], "username": user["username"], "is_admin": bool(user["is_admin"])}, "csrf_token": csrf}


@app.post("/api/auth/logout")
async def logout(response: Response, request: Request, user: dict = Depends(current_user)) -> dict:
    _require_csrf(request, user)
    token = request.cookies.get("gold_monitor_session")
    if token:
        db.delete_session(token)
    response.delete_cookie("gold_monitor_session")
    response.delete_cookie("gold_monitor_csrf")
    return {"ok": True}


@app.get("/api/auth/me")
async def me(user: dict = Depends(current_user)) -> dict:
    return {"id": user["user_id"], "username": user["username"], "is_admin": bool(user["is_admin"]), "csrf_token": user["csrf_token"]}


@app.get("/api/config")
async def get_config(user: dict = Depends(current_user)) -> dict:
    return _public_config(db.get_config(user["user_id"]))


@app.put("/api/config")
async def update_config(update: ConfigUpdate, request: Request, user: dict = Depends(current_user)) -> dict:
    _require_csrf(request, user)
    user_id = user["user_id"]
    try:
        new_cfg = _merge_config(db.get_stored_config(user_id), update)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.save_config(new_cfg, user_id)
    if new_cfg.enabled:
        _schedule_user(user_id, new_cfg.alert.poll_interval_seconds)
    else:
        _remove_user_job(user_id)
    db.add_run_log("INFO", "Config updated", {"enabled_sources": new_cfg.enabled_sources}, user_id)
    await _broadcast_snapshot(user_id)
    return _public_config(new_cfg)


@app.post("/api/actions/test-email")
async def test_email(payload: TestEmailRequest, request: Request, user: dict = Depends(current_user)) -> dict:
    _require_csrf(request, user)
    cfg = db.get_config(user["user_id"])
    recipients = payload.recipients or cfg.smtp.recipients
    try:
        recipients = ensure_valid_recipients(recipients)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not recipients:
        raise HTTPException(status_code=400, detail="No recipients configured")
    if not cfg.smtp.host or not cfg.smtp.sender_email:
        raise HTTPException(status_code=400, detail="SMTP is not fully configured")
    try:
        send_test_email(cfg, recipients=recipients)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Failed to send test email: {exc}") from exc
    return {"ok": True, "message": "Test email sent", "recipients": recipients}


@app.post("/api/actions/run-check")
async def run_check(request: Request, user: dict = Depends(current_user)) -> dict:
    _require_csrf(request, user)
    result = await _ensure_service(user["user_id"]).run_check(manual=True)
    await _broadcast_snapshot(user["user_id"])
    return result.model_dump()


@app.get("/api/status")
async def get_status(user: dict = Depends(current_user)) -> dict:
    return _status_payload(user["user_id"])


@app.get("/api/alerts")
async def get_alerts(limit: int = Query(default=20, ge=1, le=200), user: dict = Depends(current_user)) -> dict:
    return {"items": db.get_recent_alerts(limit, user["user_id"])}


@app.websocket("/ws/updates")
async def websocket_updates(websocket: WebSocket) -> None:
    session = db.get_session(websocket.cookies.get("gold_monitor_session", ""))
    if not session:
        await websocket.close(code=1008)
        return
    user_id = session["user_id"]
    await connections.connect(websocket, user_id)
    try:
        await websocket.send_json({"type": "snapshot", "status": _status_payload(user_id), "alerts": db.get_recent_alerts(200, user_id)})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        connections.disconnect(websocket, user_id)


@app.post("/api/utils/parse-recipients")
async def parse_recipients_api(body: dict, user: dict = Depends(current_user)) -> dict:
    raw = body.get("value", "")
    try:
        return {"recipients": ensure_valid_recipients(parse_recipients(raw))}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/admin/users")
async def admin_users(user: dict = Depends(admin_user)) -> dict:
    return {"items": db.list_users()}


@app.post("/api/admin/users")
async def admin_create_user(payload: UserCreateRequest, user: dict = Depends(admin_user)) -> dict:
    try:
        return db.create_user(payload.username, payload.password, payload.is_admin)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="用户名已存在或用户创建失败") from exc


@app.patch("/api/admin/users/{user_id}/status")
async def admin_set_status(user_id: int, payload: UserStatusRequest, request: Request, user: dict = Depends(admin_user)) -> dict:
    _require_csrf(request, user)
    if user_id == user["user_id"] and not payload.is_active:
        raise HTTPException(status_code=400, detail="不能禁用当前管理员")
    if not db.get_user(user_id):
        raise HTTPException(status_code=404, detail="用户不存在")
    db.set_user_active(user_id, payload.is_active)
    if payload.is_active:
        _schedule_user(user_id)
    else:
        _remove_user_job(user_id)
    return db.get_user(user_id) or {}


@app.post("/api/admin/users/{user_id}/reset-password")
async def admin_reset_password(user_id: int, payload: PasswordResetRequest, request: Request, user: dict = Depends(admin_user)) -> dict:
    _require_csrf(request, user)
    if not db.get_user(user_id):
        raise HTTPException(status_code=404, detail="用户不存在")
    db.reset_password(user_id, payload.password)
    return {"ok": True}
