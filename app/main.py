from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import ValidationError

from .db import Database
from .logic import ensure_valid_recipients, parse_recipients
from .mailer import send_test_email
from .market import AVAILABLE_SOURCES
from .models import AppConfig, ConfigUpdate, TestEmailRequest
from .monitor import MonitorService

DB_PATH = "data/gold_monitor.db"
STATIC_INDEX = Path(__file__).resolve().parent.parent / "static" / "index.html"


db = Database(DB_PATH)
monitor_service = MonitorService(db)
scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
active_poll_interval_seconds = 30


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self.active_connections.discard(websocket)

    async def broadcast(self, payload: dict) -> None:
        disconnected: list[WebSocket] = []
        for websocket in list(self.active_connections):
            try:
                await websocket.send_json(payload)
            except Exception:  # noqa: BLE001
                disconnected.append(websocket)
        for websocket in disconnected:
            self.disconnect(websocket)


connections = ConnectionManager()


def _merge_config(current: AppConfig, update: ConfigUpdate) -> AppConfig:
    data = current.model_dump()

    if update.smtp is not None:
        smtp_update = update.smtp.model_dump(exclude_unset=True)
        if "recipients" in smtp_update and smtp_update["recipients"] is not None:
            smtp_update["recipients"] = ensure_valid_recipients(smtp_update["recipients"])

        if "password" in smtp_update and (smtp_update["password"] is None or smtp_update["password"] == ""):
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


def _status_payload() -> dict:
    cfg = db.get_config()
    latest_run = db.get_latest_run_log()
    alerts = db.get_recent_alerts(1)
    job = scheduler.get_job("monitor_job")

    return {
        "enabled": cfg.enabled,
        "timezone": cfg.timezone,
        "product_sku": cfg.product_sku,
        "enabled_sources": cfg.enabled_sources,
        "available_sources": [source.model_dump() for source in AVAILABLE_SOURCES.values()],
        "poll_interval_seconds": cfg.alert.poll_interval_seconds,
        "fast_poll_interval_seconds": cfg.alert.fast_poll_interval_seconds,
        "active_poll_interval_seconds": active_poll_interval_seconds,
        "scheduler_running": scheduler.running,
        "next_run_time": job.next_run_time.isoformat() if job and job.next_run_time else None,
        "last_run": latest_run,
        "last_result": monitor_service.last_result,
        "last_alert": alerts[0] if alerts else None,
    }


def _snapshot_payload() -> dict:
    return {
        "type": "snapshot",
        "status": _status_payload(),
        "alerts": db.get_recent_alerts(200),
    }


async def _broadcast_snapshot() -> None:
    await connections.broadcast(_snapshot_payload())


def _next_poll_interval_seconds(cfg: AppConfig, result: dict) -> int:
    if result.get("triggered") or result.get("near_threshold"):
        return cfg.alert.fast_poll_interval_seconds
    return cfg.alert.poll_interval_seconds


def _schedule_monitor_job(poll_interval_seconds: int) -> None:
    global active_poll_interval_seconds
    active_poll_interval_seconds = poll_interval_seconds
    if scheduler.get_job("monitor_job"):
        scheduler.remove_job("monitor_job")

    scheduler.add_job(
        _run_scheduled_check,
        "interval",
        seconds=poll_interval_seconds,
        id="monitor_job",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30,
    )


def _schedule_cleanup_job() -> None:
    if scheduler.get_job("cleanup_job"):
        scheduler.remove_job("cleanup_job")

    scheduler.add_job(
        _run_cleanup_job,
        "cron",
        hour=3,
        minute=0,
        id="cleanup_job",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )


def _reschedule_monitor_job(poll_interval_seconds: int) -> None:
    global active_poll_interval_seconds
    active_poll_interval_seconds = poll_interval_seconds
    if not scheduler.get_job("monitor_job"):
        _schedule_monitor_job(poll_interval_seconds)
        return
    scheduler.reschedule_job("monitor_job", trigger="interval", seconds=poll_interval_seconds)


async def _run_scheduled_check() -> None:
    result = await monitor_service.run_check(manual=False)
    cfg = db.get_config()
    _reschedule_monitor_job(_next_poll_interval_seconds(cfg, result.model_dump()))
    await _broadcast_snapshot()


def _run_cleanup_job() -> None:
    cfg = db.get_config()
    result = db.cleanup_old_records(cfg.alert.retention_days)
    db.add_run_log("INFO", "Cleanup completed", result)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    cfg = db.get_config()
    if not scheduler.running:
        scheduler.start()
    _schedule_monitor_job(cfg.alert.poll_interval_seconds)
    _schedule_cleanup_job()
    _run_cleanup_job()
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(title="积存金监测", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def index() -> FileResponse:
    if not STATIC_INDEX.exists():
        raise HTTPException(status_code=404, detail="UI file not found")
    return FileResponse(STATIC_INDEX)


@app.get("/api/config")
async def get_config() -> dict:
    cfg = db.get_config()
    return _public_config(cfg)


@app.put("/api/config")
async def update_config(update: ConfigUpdate) -> dict:
    cfg = db.get_stored_config()
    try:
        new_cfg = _merge_config(cfg, update)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.save_config(new_cfg)
    _schedule_monitor_job(new_cfg.alert.poll_interval_seconds)
    db.add_run_log(
        "INFO",
        "Config updated",
        {
            "poll_interval_seconds": new_cfg.alert.poll_interval_seconds,
            "fast_poll_interval_seconds": new_cfg.alert.fast_poll_interval_seconds,
            "retention_days": new_cfg.alert.retention_days,
            "enabled_sources": new_cfg.enabled_sources,
        },
    )
    updated = _public_config(db.get_config())
    await _broadcast_snapshot()
    return updated


@app.post("/api/actions/test-email")
async def test_email(payload: TestEmailRequest) -> dict:
    cfg = db.get_config()

    recipients = payload.recipients if payload.recipients else cfg.smtp.recipients
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
async def run_check() -> dict:
    result = await monitor_service.run_check(manual=True)
    await _broadcast_snapshot()
    return result.model_dump()


@app.get("/api/status")
async def get_status() -> dict:
    return _status_payload()


@app.get("/api/alerts")
async def get_alerts(limit: int = Query(default=20, ge=1, le=200)) -> dict:
    return {"items": db.get_recent_alerts(limit)}


@app.websocket("/ws/updates")
async def websocket_updates(websocket: WebSocket) -> None:
    await connections.connect(websocket)
    try:
        await websocket.send_json(_snapshot_payload())
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        connections.disconnect(websocket)


@app.post("/api/utils/parse-recipients")
async def parse_recipients_api(body: dict) -> dict:
    # tiny helper for the UI: accepts raw textarea text.
    raw = body.get("value", "")
    try:
        recipients = ensure_valid_recipients(parse_recipients(raw))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"recipients": recipients}
