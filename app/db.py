from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .logic import alert_direction
from .models import AppConfig


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 210_000)
    return f"pbkdf2_sha256$210000${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


@dataclass
class Database:
    path: str
    timezone: str = "Asia/Shanghai"

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def now_iso(self) -> str:
        return datetime.now(ZoneInfo(self.timezone)).isoformat()

    def init_db(self, admin_username: str | None = None, admin_password: str | None = None, require_admin: bool = False) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0,
                    is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    token_hash TEXT NOT NULL UNIQUE, csrf_token TEXT NOT NULL, expires_at TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS user_configs (
                    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    config_json TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (
                    id INTEGER PRIMARY KEY CHECK(id=1), config_json TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS alert_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL DEFAULT 1 REFERENCES users(id),
                    triggered_at TEXT NOT NULL, current_price REAL NOT NULL, high_price REAL NOT NULL, low_price REAL NOT NULL,
                    delta REAL NOT NULL, pct REAL NOT NULL, badge TEXT NOT NULL, mail_sent INTEGER NOT NULL,
                    mail_error TEXT NOT NULL, payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS run_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL DEFAULT 1 REFERENCES users(id),
                    run_at TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL, details_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS price_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL DEFAULT 1 REFERENCES users(id),
                    source_code TEXT NOT NULL, sampled_at TEXT NOT NULL, price REAL NOT NULL
                );
                """
            )
            for table in ("alert_events", "run_logs", "price_samples"):
                columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                if "user_id" not in columns:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_price_samples_user_source_time ON price_samples(user_id, source_code, sampled_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_alert_events_user_id ON alert_events(user_id, id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_run_logs_user_id ON run_logs(user_id, id)")
            legacy = conn.execute("SELECT config_json FROM settings WHERE id=1").fetchone()
            users = conn.execute("SELECT id FROM users ORDER BY id").fetchall()
            if not users:
                if require_admin and (not admin_username or not admin_password):
                    raise RuntimeError("首次启动必须设置 ADMIN_USERNAME 和 ADMIN_PASSWORD")
                admin_username = admin_username or "legacy-admin"
                admin_password = admin_password or secrets.token_urlsafe(24)
                now = self.now_iso()
                cur = conn.execute(
                    "INSERT INTO users(username,password_hash,is_admin,is_active,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                    (admin_username, hash_password(admin_password), 1, 1, now, now),
                )
                user_id = cur.lastrowid
                config_json = legacy["config_json"] if legacy else json.dumps(AppConfig().model_dump(), ensure_ascii=False)
                conn.execute("INSERT INTO user_configs(user_id,config_json,updated_at) VALUES(?,?,?)", (user_id, config_json, now))
                conn.execute("UPDATE alert_events SET user_id=? WHERE user_id=1", (user_id,))
                conn.execute("UPDATE run_logs SET user_id=? WHERE user_id=1", (user_id,))
                conn.execute("UPDATE price_samples SET user_id=? WHERE user_id=1", (user_id,))
            else:
                for row in users:
                    conn.execute("INSERT OR IGNORE INTO user_configs(user_id,config_json,updated_at) VALUES(?,?,?)", (row["id"], json.dumps(AppConfig().model_dump(), ensure_ascii=False), self.now_iso()))

    def create_user(self, username: str, password: str, is_admin: bool = False) -> dict[str, Any]:
        now = self.now_iso()
        with self.connect() as conn:
            cur = conn.execute("INSERT INTO users(username,password_hash,is_admin,is_active,created_at,updated_at) VALUES(?,?,?,?,?,?)", (username, hash_password(password), int(is_admin), 1, now, now))
            user_id = cur.lastrowid
            conn.execute("INSERT INTO user_configs(user_id,config_json,updated_at) VALUES(?,?,?)", (user_id, json.dumps(AppConfig().model_dump(), ensure_ascii=False), now))
        return self.get_user(user_id)  # type: ignore[arg-type]

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT id,username,is_admin,is_active,created_at,updated_at FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return dict(row) if row else None

    def list_users(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT id,username,is_admin,is_active,created_at,updated_at FROM users ORDER BY id").fetchall()
        return [dict(row) for row in rows]

    def set_user_active(self, user_id: int, active: bool) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE users SET is_active=?,updated_at=? WHERE id=?", (int(active), self.now_iso(), user_id))
            if not active:
                conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))

    def reset_password(self, user_id: int, password: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE users SET password_hash=?,updated_at=? WHERE id=?", (hash_password(password), self.now_iso(), user_id))
            conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))

    def create_session(self, user_id: int, days: int = 7) -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        now = self.now_iso()
        expires = (datetime.now(ZoneInfo(self.timezone)) + timedelta(days=days)).isoformat()
        with self.connect() as conn:
            conn.execute("INSERT INTO sessions(user_id,token_hash,csrf_token,expires_at,created_at) VALUES(?,?,?,?,?)", (user_id, hashlib.sha256(token.encode()).hexdigest(), csrf, expires, now))
        return token, csrf

    def get_session(self, token: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT s.*,u.username,u.is_admin,u.is_active FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?", (hashlib.sha256(token.encode()).hexdigest(), self.now_iso())).fetchone()
        return dict(row) if row and row["is_active"] else None

    def delete_session(self, token: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))

    def get_stored_config(self, user_id: int = 1) -> AppConfig:
        with self.connect() as conn:
            row = conn.execute("SELECT config_json FROM user_configs WHERE user_id=?", (user_id,)).fetchone()
        if row is None:
            cfg = AppConfig()
            self.save_config(cfg, user_id)
            return cfg
        return AppConfig.model_validate(json.loads(row["config_json"]))

    def get_config(self, user_id: int = 1) -> AppConfig:
        cfg = self.get_stored_config(user_id)
        cfg.smtp = cfg.smtp.with_env_defaults()
        return cfg

    def save_config(self, cfg: AppConfig, user_id: int = 1) -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO user_configs(user_id,config_json,updated_at) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET config_json=excluded.config_json,updated_at=excluded.updated_at", (user_id, json.dumps(cfg.model_dump(), ensure_ascii=False), self.now_iso()))

    def add_alert_event(self, payload: dict[str, Any], mail_sent: bool, mail_error: str, user_id: int = 1) -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO alert_events(user_id,triggered_at,current_price,high_price,low_price,delta,pct,badge,mail_sent,mail_error,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (user_id, self.now_iso(), float(payload.get("current_price", 0)), float(payload.get("high_price", 0)), float(payload.get("low_price", 0)), float(payload.get("delta", 0)), float(payload.get("pct", 0)), str(payload.get("badge", "区间内")), int(mail_sent), mail_error, json.dumps(payload, ensure_ascii=False)))

    def add_run_log(self, status: str, message: str, details: dict[str, Any], user_id: int = 1) -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO run_logs(user_id,run_at,status,message,details_json) VALUES(?,?,?,?,?)", (user_id, self.now_iso(), status, message, json.dumps(details, ensure_ascii=False)))

    def add_price_sample(self, source_code: str, sampled_at: str, price: float, user_id: int = 1) -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO price_samples(user_id,source_code,sampled_at,price) VALUES(?,?,?,?)", (user_id, source_code, sampled_at, price))

    def get_price_samples(self, source_code: str, since: datetime, user_id: int = 1) -> list[tuple[datetime, float]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT sampled_at,price FROM price_samples WHERE user_id=? AND source_code=? AND sampled_at>=? ORDER BY sampled_at ASC", (user_id, source_code, since.isoformat())).fetchall()
        samples: list[tuple[datetime, float]] = []
        for row in rows:
            try:
                samples.append((datetime.fromisoformat(row["sampled_at"]), float(row["price"])))
            except (TypeError, ValueError):
                continue
        return samples

    def get_recent_alerts(self, limit: int = 20, user_id: int = 1) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM alert_events WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, max(1, min(limit, 200)))).fetchall()
        return [dict(row) for row in rows]

    def get_latest_run_log(self, user_id: int = 1) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM run_logs WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
        return dict(row) if row else None

    def get_last_sent_alert_by_source(self, source_code: str = "", user_id: int = 1) -> dict[str, Any] | None:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM alert_events WHERE user_id=? AND mail_sent=1 ORDER BY id DESC", (user_id,)).fetchall()
        for row in rows:
            item = dict(row)
            if source_code and self._payload(item).get("source_code") != source_code:
                continue
            return item
        return None

    def get_last_sent_alert_by_direction(self, direction: str, source_code: str = "", user_id: int = 1) -> dict[str, Any] | None:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM alert_events WHERE user_id=? AND mail_sent=1 ORDER BY id DESC", (user_id,)).fetchall()
        for row in rows:
            item = dict(row)
            if source_code and self._payload(item).get("source_code") != source_code:
                continue
            if alert_direction(float(item["current_price"]), float(item["high_price"]), float(item["low_price"]), str(item["badge"])) == direction:
                return item
        return None

    def get_last_alert_event_by_direction(self, direction: str, source_code: str = "", user_id: int = 1) -> dict[str, Any] | None:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM alert_events WHERE user_id=? ORDER BY id DESC", (user_id,)).fetchall()
        for row in rows:
            item = dict(row)
            if source_code and self._payload(item).get("source_code") != source_code:
                continue
            if alert_direction(float(item["current_price"]), float(item["high_price"]), float(item["low_price"]), str(item["badge"])) == direction:
                return item
        return None

    def _payload(self, item: dict[str, Any]) -> dict[str, Any]:
        try:
            return json.loads(item.get("payload_json") or "{}")
        except json.JSONDecodeError:
            return {}

    def cleanup_old_records(self, retention_days: int, user_id: int | None = None) -> dict[str, Any]:
        cutoff = (datetime.now(ZoneInfo(self.timezone)) - timedelta(days=retention_days)).isoformat()
        clause = "" if user_id is None else " AND user_id=?"
        args: tuple[Any, ...] = () if user_id is None else (user_id,)
        with self.connect() as conn:
            counts = {}
            for table, column in (("alert_events", "triggered_at"), ("run_logs", "run_at"), ("price_samples", "sampled_at")):
                counts[f"{table}_deleted"] = conn.execute(f"DELETE FROM {table} WHERE {column} < ?{clause}", (cutoff, *args)).rowcount
        return {"retention_days": retention_days, "cutoff": cutoff, **counts}
