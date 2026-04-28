import asyncio
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.db import Database
from app.market import MarketSnapshot
from app.models import AppConfig
from app.monitor import MonitorService


def run(coro):
    return asyncio.run(coro)


def snapshot(
    *,
    current=103.0,
    high=103.0,
    low=100.0,
    delta=3.0,
    pct=3.0,
    badge="近高点",
    triggered=True,
):
    return MarketSnapshot(
        source_code="zheshang",
        source_name="浙商",
        product_sku="1961543816",
        order_source="swj_zsjcj_0102",
        current_price=current,
        high_price=high,
        low_price=low,
        delta=delta,
        pct=pct,
        badge=badge,
        sampled_points=2,
        run_time=datetime.now(ZoneInfo("Asia/Shanghai")),
        triggered=triggered,
    )


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.tmp.name) / "gold.db"))
        self.db.init_db()
        self.service = MonitorService(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def save_config(self, cfg: AppConfig) -> None:
        self.db.save_config(cfg)

    def test_disabled_monitor_skips_scheduled_run(self):
        cfg = self.db.get_stored_config()
        cfg.enabled = False
        self.save_config(cfg)

        with patch("app.monitor.fetch_snapshot") as fetch:
            result = run(self.service.run_check(manual=False))

        fetch.assert_not_called()
        self.assertEqual(result.error, "monitor disabled")
        self.assertEqual(self.db.get_latest_run_log()["status"], "SKIPPED")

    def test_manual_run_ignores_disabled_switch(self):
        cfg = self.db.get_stored_config()
        cfg.enabled = False
        cfg.smtp.recipients = []
        self.save_config(cfg)

        with patch("app.monitor.fetch_snapshot", return_value=snapshot()):
            result = run(self.service.run_check(manual=True))

        self.assertTrue(result.triggered)
        self.assertEqual(len(self.db.get_recent_alerts(20)), 1)

    def test_trigger_without_recipients_records_skip_reason(self):
        cfg = self.db.get_stored_config()
        cfg.smtp.recipients = []
        self.save_config(cfg)

        with patch("app.monitor.fetch_snapshot", return_value=snapshot()):
            result = run(self.service.run_check(manual=True))

        alert = self.db.get_recent_alerts(1)[0]
        self.assertTrue(result.triggered)
        self.assertEqual(alert["mail_sent"], 0)
        self.assertEqual(alert["mail_error"], "no recipients configured")

    def test_trigger_without_smtp_records_skip_reason(self):
        cfg = self.db.get_stored_config()
        cfg.smtp.recipients = ["a@example.com"]
        cfg.smtp.host = ""
        cfg.smtp.sender_email = ""
        self.save_config(cfg)

        with patch("app.monitor.fetch_snapshot", return_value=snapshot()):
            run(self.service.run_check(manual=True))

        alert = self.db.get_recent_alerts(1)[0]
        self.assertEqual(alert["mail_sent"], 0)
        self.assertEqual(alert["mail_error"], "smtp not configured")

    def test_configured_smtp_sends_alert(self):
        cfg = self.db.get_stored_config()
        cfg.smtp.recipients = ["a@example.com"]
        cfg.smtp.host = "smtp.example.com"
        cfg.smtp.sender_email = "sender@example.com"
        self.save_config(cfg)

        with patch("app.monitor.fetch_snapshot", return_value=snapshot()), patch("app.monitor.send_alert_email") as send:
            run(self.service.run_check(manual=True))

        send.assert_called_once()
        alert = self.db.get_recent_alerts(1)[0]
        self.assertEqual(alert["mail_sent"], 1)
        self.assertEqual(alert["mail_error"], "")

    def test_cooldown_skips_duplicate_without_recording_immediate_duplicate(self):
        cfg = self.db.get_stored_config()
        cfg.smtp.recipients = ["a@example.com"]
        cfg.smtp.host = "smtp.example.com"
        cfg.smtp.sender_email = "sender@example.com"
        cfg.alert.cooldown_minutes = 60
        cfg.alert.extreme_breakthrough_delta = 2.0
        self.save_config(cfg)

        with patch("app.monitor.fetch_snapshot", return_value=snapshot(high=103.0)), patch(
            "app.monitor.send_alert_email"
        ):
            run(self.service.run_check(manual=True))

        with patch("app.monitor.fetch_snapshot", return_value=snapshot(high=103.5)), patch(
            "app.monitor.send_alert_email"
        ) as send:
            run(self.service.run_check(manual=True))

        send.assert_not_called()
        self.assertEqual(len(self.db.get_recent_alerts(20)), 1)

    def test_cooldown_skips_opposite_direction_without_new_extreme(self):
        cfg = self.db.get_stored_config()
        cfg.smtp.recipients = ["a@example.com"]
        cfg.smtp.host = "smtp.example.com"
        cfg.smtp.sender_email = "sender@example.com"
        cfg.alert.cooldown_minutes = 60
        cfg.alert.extreme_breakthrough_delta = 2.0
        self.save_config(cfg)

        with patch(
            "app.monitor.fetch_snapshot",
            return_value=snapshot(current=102.0, high=104.0, low=100.0, badge="区间内"),
        ), patch("app.monitor.send_alert_email"):
            run(self.service.run_check(manual=True))

        with patch(
            "app.monitor.fetch_snapshot",
            return_value=snapshot(current=101.0, high=104.0, low=100.0, badge="区间内"),
        ), patch("app.monitor.send_alert_email") as send:
            run(self.service.run_check(manual=True))

        send.assert_not_called()
        self.assertEqual(len(self.db.get_recent_alerts(20)), 1)

    def test_extreme_breakthrough_bypasses_cooldown(self):
        cfg = self.db.get_stored_config()
        cfg.smtp.recipients = ["a@example.com"]
        cfg.smtp.host = "smtp.example.com"
        cfg.smtp.sender_email = "sender@example.com"
        cfg.alert.cooldown_minutes = 60
        cfg.alert.extreme_breakthrough_delta = 2.0
        self.save_config(cfg)

        with patch("app.monitor.fetch_snapshot", return_value=snapshot(high=103.0)), patch(
            "app.monitor.send_alert_email"
        ):
            run(self.service.run_check(manual=True))

        with patch("app.monitor.fetch_snapshot", return_value=snapshot(current=106.0, high=106.0)), patch(
            "app.monitor.send_alert_email"
        ) as send:
            run(self.service.run_check(manual=True))

        send.assert_called_once()
        self.assertEqual(len(self.db.get_recent_alerts(20)), 2)

    def test_non_triggered_near_threshold_accelerates_without_alert(self):
        with patch(
            "app.monitor.fetch_snapshot",
            return_value=snapshot(current=102.1, high=102.1, low=100, delta=2.1, pct=2.1, triggered=False),
        ):
            result = run(self.service.run_check(manual=True))

        self.assertFalse(result.triggered)
        self.assertTrue(result.near_threshold)
        self.assertEqual(len(self.db.get_recent_alerts(20)), 0)

    def test_source_fetch_error_is_reported_in_result_and_log(self):
        with patch("app.monitor.fetch_snapshot", side_effect=RuntimeError("market down")):
            result = run(self.service.run_check(manual=True))

        self.assertIn("market down", result.error)
        self.assertEqual(self.db.get_latest_run_log()["status"], "ERROR")


if __name__ == "__main__":
    unittest.main()
