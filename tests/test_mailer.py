import unittest

from app.mailer import _build_alert_html, _build_alert_subject
from app.models import AppConfig


class MailerTests(unittest.TestCase):
    def setUp(self):
        self.cfg = AppConfig()
        self.payload = {
            "run_time": "2026-04-24T09:01:05+08:00",
            "current_price": 1034.63,
            "high_price": 1038.23,
            "low_price": 1034.73,
            "delta": 3.5,
            "pct": 0.3383,
            "badge": "近低点",
        }

    def test_alert_subject_contains_price_window(self):
        subject = _build_alert_subject(self.cfg, self.payload)
        self.assertEqual(subject, "🟢【浙商】金价 1034.63元/克 | 近1h低价")

    def test_alert_subject_uses_high_price_label(self):
        payload = {
            **self.payload,
            "current_price": 1038.0,
            "badge": "近高点",
        }
        subject = _build_alert_subject(self.cfg, payload)
        self.assertEqual(subject, "🔴【浙商】金价 1038.00元/克 | 近1h高价")

    def test_alert_subject_uses_source_name(self):
        subject = _build_alert_subject(self.cfg, {**self.payload, "source_name": "工银"})
        self.assertEqual(subject, "🟢【工银】金价 1034.63元/克 | 近1h低价")

    def test_alert_html_uses_mobile_friendly_sizes(self):
        html = _build_alert_html(self.cfg, self.payload)
        self.assertIn("font-size:42px", html)
        self.assertNotIn("font-size:82px", html)
        self.assertNotIn("font-size:52px", html)
        self.assertIn("background:#e7f2eb", html)
        self.assertIn("告警时间：2026-04-24 09:01:05", html)
        self.assertNotIn("告警时间：2026-04-24T09:01:05+08:00", html)
        self.assertIn("查看行情", html)


if __name__ == "__main__":
    unittest.main()
