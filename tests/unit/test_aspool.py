import unittest
from datetime import date, datetime

from aspool.free_stockdb import _normalize
from aspool.tdx_online import _merge_rows


class DataSemanticsTest(unittest.TestCase):
    def test_normalize_accepts_online_and_history_dates(self):
        rows = _normalize([{"date": 20260916}, {"trade_date": "2026-09-15"}], "000001")
        self.assertEqual(
            [row["trade_date"] for row in rows], [date(2026, 9, 15), date(2026, 9, 16)]
        )

    def test_merge_retains_historical_fields_for_online_tail(self):
        rows = _merge_rows(
            [{"trade_date": date(2026, 9, 16), "name": "平安银行", "close": 11.7}],
            [{"trade_date": date(2026, 9, 16), "close": 11.8, "name": None}],
            "trade_date",
        )
        self.assertEqual(
            rows, [{"trade_date": date(2026, 9, 16), "name": "平安银行", "close": 11.8}]
        )

    def test_merge_supports_minute_timestamps(self):
        stamp = datetime(2026, 9, 16, 15)
        self.assertEqual(
            _merge_rows(
                [{"timestamp": stamp, "close": 1}], [{"timestamp": stamp, "volume": 2}], "timestamp"
            ),
            [{"timestamp": stamp, "close": 1, "volume": 2}],
        )


if __name__ == "__main__":
    unittest.main()
