import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.market import AVAILABLE_SOURCES, fetch_snapshot


class MarketTests(unittest.TestCase):
    def test_stale_latest_time_falls_back_to_local_clock_for_windowing(self):
        source = AVAILABLE_SOURCES["minsheng"]
        tz = ZoneInfo("Asia/Shanghai")
        observed_now = datetime(2026, 5, 7, 9, 12, 0, tzinfo=tz)
        local_points = [
            (datetime(2026, 5, 7, 8, 20, 0, tzinfo=tz), 1029.92),
            (datetime(2026, 5, 7, 8, 50, 0, tzinfo=tz), 1029.92),
        ]
        stale_latest_time = int(datetime(2026, 5, 7, 1, 59, 59, tzinfo=tz).timestamp() * 1000)

        with patch("app.market.datetime") as mocked_datetime, patch("app.market._request_json") as request_json:
            mocked_datetime.now.return_value = observed_now
            mocked_datetime.fromtimestamp.side_effect = datetime.fromtimestamp
            mocked_datetime.strptime.side_effect = datetime.strptime
            request_json.return_value = {
                "resultData": {
                    "datas": {
                        "price": "1034.88",
                        "time": stale_latest_time,
                    }
                }
            }

            snapshot = fetch_snapshot(
                source=source,
                threshold_delta=2.0,
                threshold_pct=0.2,
                near_extreme_pct=0.1,
                timezone="Asia/Shanghai",
                local_points=local_points,
            )

        self.assertEqual(snapshot.run_time, observed_now)
        self.assertEqual(snapshot.low_price, 1029.92)
        self.assertEqual(snapshot.high_price, 1034.88)
        self.assertTrue(snapshot.triggered)


if __name__ == "__main__":
    unittest.main()
