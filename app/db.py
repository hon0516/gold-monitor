from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .logic import alert_direction
from .models import AppConfig


@dataclass
class Database:
    path: str
    timezone: str = "Asia/Shanghai"

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def now_iso(self) -> str:
        return datetime.now(ZoneInfo(self.timezone)).isoformat()

    def init_db(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    config_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS alert_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    triggered_at TEXT NOT NULL,
                    current_price REAL NOT NULL,
                    high_price REAL NOT NULL,
                    low_price REAL NOT NULL,
                    delta REAL NOT NULL,
                    pct REAL NOT NULL,
                    badge TEXT NOT NULL,
                    mail_sent INTEGER NOT NULL,
                    mail_error TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS run_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS price_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_code TEXT NOT NULL,
                    sampled_at TEXT NOT NULL,
                    price REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_price_samples_source_time
                ON price_samples(source_code, sampled_at);
                """
            )

            row = conn.execute("SELECT config_json FROM settings WHERE id=1").fetchone()
            if row is None:
                default_cfg = AppConfig().model_dump()
                conn.execute(
                    "INSERT INTO settings(id, config_json, updated_at) VALUES(1, ?, ?)",
                    (json.dumps(default_cfg, ensure_ascii=False), self.now_iso()),
                )

    def get_stored_config(self) -> AppConfig:
        with self.connect() as conn:
            row = conn.execute("SELECT config_json FROM settings WHERE id=1").fetchone()
            if row is None:
                cfg = AppConfig()
                self.save_config(cfg)
                return cfg
            return AppConfig.model_validate(json.loads(row["config_json"]))

    def get_config(self) -> AppConfig:
        cfg = self.get_stored_config()
        cfg.smtp = cfg.smtp.with_env_defaults()
        return cfg

    def save_config(self, cfg: AppConfig) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE settings SET config_json=?, updated_at=? WHERE id=1",
                (json.dumps(cfg.model_dump(), ensure_ascii=False), self.now_iso()),
            )

    def add_alert_event(self, payload: dict[str, Any], mail_sent: bool, mail_error: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO alert_events(
                    triggered_at,current_price,high_price,low_price,delta,pct,badge,
                    mail_sent,mail_error,payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.now_iso(),
                    float(payload.get("current_price", 0)),
                    float(payload.get("high_price", 0)),
                    float(payload.get("low_price", 0)),
                    float(payload.get("delta", 0)),
                    float(payload.get("pct", 0)),
                    str(payload.get("badge", "区间内")),
                    1 if mail_sent else 0,
                    mail_error,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )

    def add_run_log(self, status: str, message: str, details: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO run_logs(run_at, status, message, details_json) VALUES (?, ?, ?, ?)",
                (
                    self.now_iso(),
                    status,
                    message,
                    json.dumps(details, ensure_ascii=False),
                ),
            )

    def add_price_sample(self, source_code: str, sampled_at: str, price: float) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO price_samples(source_code, sampled_at, price) VALUES (?, ?, ?)",
                (source_code, sampled_at, price),
            )

    def get_price_samples(self, source_code: str, since: datetime) -> list[tuple[datetime, float]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT sampled_at, price FROM price_samples
                WHERE source_code=? AND sampled_at >= ?
                ORDER BY sampled_at ASC
                """,
                (source_code, since.isoformat()),
            ).fetchall()
        samples: list[tuple[datetime, float]] = []
        for row in rows:
            try:
                samples.append((datetime.fromisoformat(row["sampled_at"]), float(row["price"])))
            except (TypeError, ValueError):
                continue
        return samples

    def get_recent_alerts(self, limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 200))
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM alert_events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_latest_run_log(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM run_logs ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def get_last_sent_alert_time(self) -> datetime | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT triggered_at FROM alert_events WHERE mail_sent=1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return datetime.fromisoformat(row["triggered_at"])

    def get_last_sent_alert_by_direction(self, direction: str, source_code: str = "") -> dict[str, Any] | None:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM alert_events
                WHERE mail_sent=1
                ORDER BY id DESC
                """
            ).fetchall()
        for row in rows:
            item = dict(row)
            payload = self._payload(item)
            if source_code and payload.get("source_code") != source_code:
                continue
            item_direction = alert_direction(
                current=float(item.get("current_price", 0)),
                high=float(item.get("high_price", 0)),
                low=float(item.get("low_price", 0)),
                badge=str(item.get("badge", "区间内")),
            )
            if item_direction == direction:
                return item
        return None

    def get_last_alert_event_by_direction(self, direction: str, source_code: str = "") -> dict[str, Any] | None:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM alert_events
                ORDER BY id DESC
                """
            ).fetchall()
        for row in rows:
            item = dict(row)
            payload = self._payload(item)
            if source_code and payload.get("source_code") != source_code:
                continue
            item_direction = alert_direction(
                current=float(item.get("current_price", 0)),
                high=float(item.get("high_price", 0)),
                low=float(item.get("low_price", 0)),
                badge=str(item.get("badge", "区间内")),
            )
            if item_direction == direction:
                return item
        return None

    def _payload(self, item: dict[str, Any]) -> dict[str, Any]:
        try:
            return json.loads(item.get("payload_json") or "{}")
        except json.JSONDecodeError:
            return {}

    def cleanup_old_records(self, retention_days: int) -> dict[str, Any]:
        cutoff = datetime.now(ZoneInfo(self.timezone)) - timedelta(days=retention_days)
        cutoff_iso = cutoff.isoformat()
        with self.connect() as conn:
            conn.isolation_level = None
            alert_deleted = conn.execute(
                "DELETE FROM alert_events WHERE triggered_at < ?",
                (cutoff_iso,),
            ).rowcount
            log_deleted = conn.execute(
                "DELETE FROM run_logs WHERE run_at < ?",
                (cutoff_iso,),
            ).rowcount
            sample_deleted = conn.execute(
                "DELETE FROM price_samples WHERE sampled_at < ?",
                (cutoff_iso,),
            ).rowcount
            conn.execute("VACUUM")
        return {
            "retention_days": retention_days,
            "cutoff": cutoff_iso,
            "alert_events_deleted": alert_deleted,
            "run_logs_deleted": log_deleted,
            "price_samples_deleted": sample_deleted,
        }
