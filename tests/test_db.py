import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.db import Database
from app.models import AppConfig


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.tmp.name) / "gold.db"))
        self.db.init_db()

    def tearDown(self):
        self.tmp.cleanup()

    def test_init_creates_default_config(self):
        cfg = self.db.get_stored_config()
        self.assertIsInstance(cfg, AppConfig)
        self.assertEqual(cfg.enabled_sources, ["zheshang"])

    def test_save_and_load_config(self):
        cfg = self.db.get_stored_config()
        cfg.enabled = False
        cfg.enabled_sources = ["icbc"]
        self.db.save_config(cfg)

        saved = self.db.get_stored_config()
        self.assertFalse(saved.enabled)
        self.assertEqual(saved.enabled_sources, ["icbc"])

    def test_recent_alerts_are_descending_and_limit_is_clamped(self):
        for idx in range(3):
            self.db.add_alert_event(
                {
                    "source_code": "zheshang",
                    "current_price": 100 + idx,
                    "high_price": 101 + idx,
                    "low_price": 99 + idx,
                    "delta": 2,
                    "pct": 1,
                    "badge": "近高点",
                },
                mail_sent=idx == 1,
                mail_error="",
            )

        alerts = self.db.get_recent_alerts(2)
        self.assertEqual(len(alerts), 2)
        self.assertGreater(alerts[0]["id"], alerts[1]["id"])
        self.assertEqual(len(self.db.get_recent_alerts(0)), 1)

    def test_price_samples_filter_by_source_and_skip_bad_rows(self):
        tz = ZoneInfo("Asia/Shanghai")
        now = datetime.now(tz)
        self.db.add_price_sample("zheshang", now.isoformat(), 101.5)
        self.db.add_price_sample("icbc", now.isoformat(), 102.5)
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO price_samples(source_code, sampled_at, price) VALUES (?, ?, ?)",
                ("zheshang", "bad-time", 103.5),
            )

        samples = self.db.get_price_samples("zheshang", now - timedelta(minutes=1))
        self.assertEqual(samples, [(now, 101.5)])

    def test_last_alert_lookup_filters_by_source_and_direction(self):
        self.db.add_alert_event(
            {
                "source_code": "zheshang",
                "current_price": 100,
                "high_price": 103,
                "low_price": 99,
                "delta": 4,
                "pct": 4,
                "badge": "近高点",
            },
            mail_sent=True,
            mail_error="",
        )
        self.db.add_alert_event(
            {
                "source_code": "icbc",
                "current_price": 95,
                "high_price": 103,
                "low_price": 95,
                "delta": 8,
                "pct": 8,
                "badge": "近低点",
            },
            mail_sent=True,
            mail_error="",
        )

        self.assertIsNotNone(self.db.get_last_sent_alert_by_direction("high", "zheshang"))
        self.assertIsNone(self.db.get_last_sent_alert_by_direction("low", "zheshang"))
        self.assertIsNotNone(self.db.get_last_sent_alert_by_source("zheshang"))
        self.assertEqual(
            json.loads(self.db.get_last_sent_alert_by_source("zheshang")["payload_json"])["source_code"],
            "zheshang",
        )

    def test_cleanup_old_records(self):
        old = (datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(days=10)).isoformat()
        self.db.add_alert_event(
            {
                "source_code": "zheshang",
                "current_price": 100,
                "high_price": 101,
                "low_price": 99,
                "delta": 2,
                "pct": 2,
                "badge": "近高点",
            },
            mail_sent=False,
            mail_error="",
        )
        with self.db.connect() as conn:
            conn.execute("UPDATE alert_events SET triggered_at=?", (old,))
            conn.execute(
                "INSERT INTO run_logs(run_at, status, message, details_json) VALUES (?, ?, ?, ?)",
                (old, "OK", "old", json.dumps({})),
            )
            conn.execute(
                "INSERT INTO price_samples(source_code, sampled_at, price) VALUES (?, ?, ?)",
                ("zheshang", old, 100),
            )

        result = self.db.cleanup_old_records(retention_days=7)
        self.assertEqual(result["alert_events_deleted"], 1)
        self.assertEqual(result["run_logs_deleted"], 1)
        self.assertEqual(result["price_samples_deleted"], 1)


if __name__ == "__main__":
    unittest.main()
