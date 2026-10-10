import tempfile
import unittest
from pathlib import Path

from app.db import Database, verify_password
from app.models import AppConfig


class AuthIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.tmp.name) / "gold.db"))
        self.db.init_db("admin", "admin-password", require_admin=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_password_is_hashed_and_session_resolves_active_user(self):
        user = self.db.get_user_by_username("admin")
        self.assertIsNotNone(user)
        self.assertNotEqual(user["password_hash"], "admin-password")
        self.assertTrue(verify_password("admin-password", user["password_hash"]))
        token, csrf = self.db.create_session(user["id"])
        session = self.db.get_session(token)
        self.assertEqual(session["user_id"], user["id"])
        self.assertEqual(session["csrf_token"], csrf)

    def test_each_user_has_independent_config(self):
        first = self.db.get_user_by_username("admin")
        second = self.db.create_user("second", "second-password")
        cfg = self.db.get_stored_config(first["id"])
        cfg.alert.threshold_delta = 9
        self.db.save_config(cfg, first["id"])
        self.assertEqual(self.db.get_config(first["id"]).alert.threshold_delta, 9)
        self.assertEqual(self.db.get_config(second["id"]).alert.threshold_delta, AppConfig().alert.threshold_delta)


if __name__ == "__main__":
    unittest.main()
