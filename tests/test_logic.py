import unittest

from app.logic import (
    alert_direction,
    compute_badge,
    compute_window_metrics,
    has_extreme_breakthrough,
    is_near_threshold,
    is_valid_email,
    parse_recipients,
    should_trigger,
    threshold_progress,
)


class LogicTests(unittest.TestCase):
    def test_parse_recipients(self):
        parsed = parse_recipients("a@test.com, b@test.com\nc@test.com;a@test.com")
        self.assertEqual(parsed, ["a@test.com", "b@test.com", "c@test.com"])

    def test_email_validation(self):
        self.assertTrue(is_valid_email("hello.world+1@test.com"))
        self.assertFalse(is_valid_email("bad@email"))

    def test_compute_window_metrics(self):
        metrics = compute_window_metrics([1036.0, 1038.5, 1040.0], 1037.5)
        self.assertEqual(metrics["high"], 1040.0)
        self.assertEqual(metrics["low"], 1036.0)
        self.assertEqual(metrics["delta"], 4.0)
        self.assertAlmostEqual(metrics["pct"], 0.3861, places=4)

    def test_badge(self):
        self.assertEqual(compute_badge(1036.05, 1040.0, 1036.0, 0.10), "近低点")
        self.assertEqual(compute_badge(1039.99, 1040.0, 1036.0, 0.10), "近高点")
        self.assertEqual(compute_badge(1038.0, 1040.0, 1036.0, 0.10), "区间内")

    def test_trigger(self):
        self.assertTrue(should_trigger(3.1, 0.1, threshold_delta=3.0, threshold_pct=0.3))
        self.assertTrue(should_trigger(1.0, 0.31, threshold_delta=3.0, threshold_pct=0.3))
        self.assertFalse(should_trigger(1.0, 0.2, threshold_delta=3.0, threshold_pct=0.3))

    def test_adaptive_threshold(self):
        self.assertAlmostEqual(
            threshold_progress(2.1, 0.1, threshold_delta=3.0, threshold_pct=0.3),
            0.7,
        )
        self.assertTrue(
            is_near_threshold(
                2.1,
                0.1,
                threshold_delta=3.0,
                threshold_pct=0.3,
                adaptive_threshold_ratio=0.7,
            )
        )
        self.assertFalse(
            is_near_threshold(
                1.0,
                0.1,
                threshold_delta=3.0,
                threshold_pct=0.3,
                adaptive_threshold_ratio=0.7,
            )
        )

    def test_alert_direction(self):
        self.assertEqual(alert_direction(1039.9, 1040.0, 1036.0, "近高点"), "high")
        self.assertEqual(alert_direction(1036.1, 1040.0, 1036.0, "近低点"), "low")
        self.assertEqual(alert_direction(1039.0, 1040.0, 1036.0, "区间内"), "high")
        self.assertEqual(alert_direction(1037.0, 1040.0, 1036.0, "区间内"), "low")

    def test_extreme_breakthrough(self):
        previous = {"high_price": 1040.0, "low_price": 1036.0}
        self.assertFalse(has_extreme_breakthrough("high", {"high_price": 1042.0}, previous))
        self.assertTrue(has_extreme_breakthrough("high", {"high_price": 1042.01}, previous))
        self.assertFalse(has_extreme_breakthrough("low", {"low_price": 1034.0}, previous))
        self.assertTrue(has_extreme_breakthrough("low", {"low_price": 1033.99}, previous))
        self.assertTrue(has_extreme_breakthrough("low", {"low_price": 1035.9}, None))


if __name__ == "__main__":
    unittest.main()
