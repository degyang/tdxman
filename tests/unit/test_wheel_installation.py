"""Dedicated installed-wheel acceptance; run with the fresh venv's python -I.

Uses unittest only and synthetic temporary SQLite databases. No source checkout
imports, configured pool roots, network calls or real-data maintenance.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from importlib import metadata, resources
from pathlib import Path
from unittest.mock import patch

import aspool
import tdxman
from aspool import DataPool
from aspool.ex_domain import load_ex_categories
from aspool.sqlite_stock_store import stock_connection


class WheelInstallationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="wheel-install-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        with stock_connection(self.root, create=True, read_only=False) as conn:
            for number in range(1, 4):
                symbol = f"{number:06d}.SZ"
                for day in ("2026-09-23", "2026-09-24"):
                    conn.execute(
                        "INSERT INTO daily_bars(symbol,trade_date,open,high,low,close,"
                        "volume,amount,updated_at) VALUES (?,?,10,11,10,11,100,1000,1)",
                        (symbol, day),
                    )
                    conn.execute(
                        "INSERT INTO daily_features(symbol,trade_date,calc_status,"
                        "limit_status,pre_close,is_st,limit_up_price,limit_down_price,"
                        "touch_limit_up,close_limit_up,touch_limit_down,close_limit_down,"
                        "consecutive_up,prior_consecutive_up,streak_known,updated_at) "
                        "VALUES (?,?,'TRADED','KNOWN',10,0,11,9,1,1,0,0,1,0,1,1)",
                        (symbol, day),
                    )
            conn.execute(
                "INSERT INTO market_daily_summary(frequency,period_key,scope,period_start,"
                "period_end,as_of,session_count,trading_count,updated_at) "
                "VALUES ('D','2026-09-24','all_stocks','2026-09-24','2026-09-24',"
                "'2026-09-24',1,3,1)"
            )
            conn.commit()
        self.pool = DataPool(self.root)

    def test_installed_distribution_and_runtime_version(self):
        self.assertEqual(metadata.version("tdxman"), "1.1.4")
        self.assertEqual(tdxman.__version__, "1.1.4")
        for module in (tdxman, aspool):
            self.assertTrue(Path(module.__file__).resolve().is_relative_to(Path(sys.prefix)))

    def test_bundled_defaults_do_not_require_repository_settings(self):
        bundled = resources.files("aspool").joinpath("resources", "ex_assets.json")
        expected = json.loads(bundled.read_text(encoding="utf-8"))["categories"]
        with patch("aspool.ex_domain.DEFAULT_CONFIG", self.root / "absent-settings.json"):
            self.assertEqual(load_ex_categories(), expected)
        self.assertTrue(any(row["code"] == "ETF" for row in expected))
        self.assertTrue(resources.files("aspool").joinpath("stocks_schema.sql").is_file())

    def test_explicit_configuration_and_validation(self):
        config = self.root / "custom-ex.json"
        config.write_text(json.dumps({"domain": "ex", "categories": []}), encoding="utf-8")
        self.assertEqual(load_ex_categories(config), [])
        config.write_text('{"domain":"bad"}', encoding="utf-8")
        with self.assertRaises(ValueError):
            load_ex_categories(config)
        with self.assertRaises(FileNotFoundError):
            load_ex_categories(self.root / "missing.json")

    def test_describe_from_installed_package(self):
        described = self.pool.describe()
        self.assertEqual(described["contract_version"], 3)
        self.assertEqual(described["backend"], "sqlite")
        self.assertTrue(described["capabilities"]["market_summary"])
        self.assertTrue(described["capabilities"]["read_snapshot"])
        self.assertEqual(described["ex_categories"], load_ex_categories())

    def test_sqlite_daily_summary_projection(self):
        summary = self.pool.read_market_daily(
            start="2026-09-24", end="2026-09-24", fields=["period_key", "trading_count"]
        )
        self.assertEqual(
            summary.to_dict("records"), [{"period_key": "2026-09-24", "trading_count": 3}]
        )

    def test_snapshot_keeps_join_on_one_version(self):
        with self.pool.stock_snapshot() as reader:
            before = reader.read_daily(symbols="000001.SZ", lookback=1, fields=["amount"])
            with stock_connection(self.root, read_only=False) as writer:
                writer.execute("UPDATE daily_bars SET amount=2000 WHERE symbol='000001.SZ'")
                writer.commit()
            joined = reader.read_limit_events(
                trade_date="2026-09-24", symbols="000001.SZ", fields=["amount"]
            )
            self.assertEqual(before.amount.iloc[0], 1000)
            self.assertEqual(joined.amount.iloc[0], 1000)
        latest = self.pool.read_daily(symbols="000001.SZ", lookback=1, fields=["amount"])
        self.assertEqual(latest.amount.iloc[0], 2000)

    def test_paginated_events_without_loss_or_duplicates(self):
        with self.pool.iter_limit_events_with_amount(
            start="2026-09-23",
            end="2026-09-24",
            batch_days=1,
            max_rows=2,
            fields=["trade_date", "symbol", "amount"],
        ) as stream:
            frames = list(stream)
            self.assertTrue(stream.completed)
        self.assertTrue(stream.closed)
        self.assertEqual([len(frame) for frame in frames], [2, 1, 2, 1])
        keys = [
            tuple(row)
            for frame in frames
            for row in frame[["trade_date", "symbol"]].itertuples(index=False, name=None)
        ]
        self.assertEqual(len(keys), 6)
        self.assertEqual(len(set(keys)), 6)
        self.assertEqual(keys, sorted(keys))
        self.assertTrue(all((frame.amount == 1000).all() for frame in frames))

    def test_integrated_quality_metadata_and_calculation(self):
        from aspool.sqlite_market_summary import recompute_daily_summary

        self.assertTrue(Path(aspool.__file__).resolve().is_relative_to(Path(sys.prefix)))
        self.assertEqual(self.pool.describe()["contract_version"], 3)
        metadata = self.pool.describe_market_fields(fields=["promotion_quality_json"])
        self.assertEqual(metadata["promotion_quality_json"]["null_meaning"], "not_computed")
        before = self.pool.read_market_daily(start="2026-09-24", end="2026-09-24")
        self.assertIsNone(before.promotion_quality_json.iloc[0])
        with stock_connection(self.root, read_only=False) as writer:
            writer.execute(
                "UPDATE daily_features SET prior_consecutive_up=1,consecutive_up=2 "
                "WHERE trade_date='2026-09-24'"
            )
            recompute_daily_summary(writer, trade_date="2026-09-24")
            writer.commit()
        with self.pool.stock_snapshot() as reader:
            after = reader.read_market_daily(start="2026-09-24", end="2026-09-24")
        quality = json.loads(after.promotion_quality_json.iloc[0])
        self.assertEqual(quality["candidate_count"], 3)
        self.assertEqual(after.promotion_eligible_count.iloc[0], 3)
        self.assertEqual(after.promotion_ratio.iloc[0], 1)
        self.assertIn("UNKNOWN", json.loads(after.limit_reason_counts_json.iloc[0]))

    def test_installed_console_entrypoints(self):
        for args in (["--version"], ["version"]):
            result = subprocess.run(
                [str(Path(sys.prefix) / "bin/tdxman"), *args],
                cwd=self.root,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("1.1.4", result.stdout)
        categories = subprocess.run(
            [str(Path(sys.prefix) / "bin/aspool"), "ex", "categories"],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(json.loads(categories.stdout), load_ex_categories())


if __name__ == "__main__":
    unittest.main(verbosity=2)
