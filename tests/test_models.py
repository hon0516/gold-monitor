import os
import unittest

from pydantic import ValidationError

from app.models import AlertSettings, AppConfig, SMTPSettings


class ModelTests(unittest.TestCase):
    def test_alert_settings_reject_negative_threshold(self):
        with self.assertRaises(ValidationError):
            AlertSettings(threshold_delta=-0.01)

    def test_alert_settings_reject_non_positive_intervals(self):
        with self.assertRaises(ValidationError):
            AlertSettings(poll_interval_seconds=0)
        with self.assertRaises(ValidationError):
            AlertSettings(retention_days=0)

    def test_fast_poll_interval_must_not_exceed_normal_interval(self):
        with self.assertRaises(ValidationError):
            AlertSettings(poll_interval_seconds=5, fast_poll_interval_seconds=10)

    def test_smtp_rejects_invalid_port_and_recipients(self):
        with self.assertRaises(ValidationError):
            SMTPSettings(port=70000)
        with self.assertRaises(ValidationError):
            SMTPSettings(recipients=["bad-email"])

    def test_smtp_env_defaults_fill_empty_profile(self):
        old_env = os.environ.copy()
        try:
            os.environ["GOLD_MONITOR_SMTP_HOST"] = "smtp.example.com"
            os.environ["GOLD_MONITOR_SMTP_PORT"] = "465"
            os.environ["GOLD_MONITOR_SMTP_SECURITY"] = "ssl"
            os.environ["GOLD_MONITOR_SMTP_USERNAME"] = "bot"
            os.environ["GOLD_MONITOR_SMTP_PASSWORD"] = "secret"
            os.environ["GOLD_MONITOR_SMTP_SENDER_EMAIL"] = "bot@example.com"

            smtp = SMTPSettings().with_env_defaults()
            self.assertEqual(smtp.host, "smtp.example.com")
            self.assertEqual(smtp.port, 465)
            self.assertEqual(smtp.security, "ssl")
            self.assertEqual(smtp.username, "bot")
            self.assertEqual(smtp.password, "secret")
            self.assertEqual(smtp.sender_email, "bot@example.com")
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_default_app_config_is_enabled_for_zheshang(self):
        cfg = AppConfig()
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.enabled_sources, ["zheshang"])


if __name__ == "__main__":
    unittest.main()
