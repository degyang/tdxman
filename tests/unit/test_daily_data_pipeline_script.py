import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import duckdb

SCRIPT = Path(__file__).parents[2] / "scripts/ops/run_daily_data_pipeline.py"
SPEC = importlib.util.spec_from_file_location("daily_data_pipeline", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_pipeline_keeps_data_dependencies_in_operational_order(tmp_path):
    stages = MODULE.pipeline_stages(tmp_path / "data", workers=4, fundamentals=True)
    assert [stage.name for stage in stages] == [
        "directory",
        "stock_update",
        "index_update",
        "etf_update",
        "fundamentals",
    ]
    assert "除权" in stages[1].purpose


def test_fundamentals_remains_opt_in_for_scheduled_market_runs(tmp_path):
    stages = MODULE.pipeline_stages(tmp_path / "data", workers=4, fundamentals=False)
    assert "fundamentals" not in [stage.name for stage in stages]


def test_audit_requires_all_contract_datasets_and_closed_stock_features(tmp_path):
    features = tmp_path / "features.sqlite"
    with sqlite3.connect(features) as conn:
        conn.execute(
            "CREATE TABLE stock_daily_features("
            "symbol TEXT,trade_date TEXT,calc_status TEXT,trading_status TEXT)"
        )
        conn.executemany(
            "INSERT INTO stock_daily_features VALUES (?,?,?,?)",
            [
                ("000001.SZ", "2026-09-29", "TRADED", "TRADED"),
                ("600000.SH", "2026-09-29", "NO_TRADE", "NO_TRADE"),
            ],
        )

    required = [
        "securities",
        "calendar",
        "fundamentals",
        "shareholder-counts",
        "stock-bars",
        "corporate-actions",
        "stock-factors",
        "stock-features",
        "market-summary",
        "index-bars",
        "etf-bars",
        "etf-factors",
    ]

    class Completed:
        def __init__(self, value):
            self.stdout = json.dumps(value)

    def fake_aspool(command, *, capture):
        if command[0] == "contract":
            return Completed([{"name": f"block-{i}"} for i in range(12)])
        rows = [{"dataset": dataset} for dataset in required]
        rows[0]["symbols"] = 2
        return Completed(rows)

    audit = MODULE.collect_audit(tmp_path, fake_aspool)
    assert audit["ready"] is True
    assert audit["stock_feature_closure"]["calc_status"] == {"TRADED": 1, "NO_TRADE": 1}


def test_feature_closure_uses_target_session_exact_symbols_and_factor_coverage(tmp_path):
    with duckdb.connect(str(tmp_path / "catalog.duckdb")) as catalog:
        catalog.execute(
            "CREATE TABLE security_calendar(trade_date DATE PRIMARY KEY,is_open BOOLEAN)"
        )
        catalog.execute("INSERT INTO security_calendar VALUES ('2026-09-29',true)")
        catalog.execute(
            "CREATE TABLE securities(symbol VARCHAR,asset_type VARCHAR,active BOOLEAN,"
            "listing_date DATE,delisting_date DATE)"
        )
        catalog.executemany(
            "INSERT INTO securities VALUES (?,'stock',true,'2020-01-01',NULL)",
            [("000001.SZ",), ("600000.SH",)],
        )
    with sqlite3.connect(tmp_path / "features.sqlite") as features:
        features.execute(
            "CREATE TABLE stock_daily_features("
            "symbol TEXT,trade_date TEXT,calc_status TEXT,trading_status TEXT)"
        )
        features.execute(
            "INSERT INTO stock_daily_features VALUES "
            "('000001.SZ','2026-09-29','TRADED','TRADING')"
        )
        features.execute(
            "CREATE TABLE market_regime_features(frequency TEXT,period_key TEXT)"
        )
        features.execute("INSERT INTO market_regime_features VALUES ('D','2026-09-29')")
    with sqlite3.connect(tmp_path / "adjustments.sqlite") as factors:
        factors.execute(
            "CREATE TABLE stock_adjustment_factors("
            "symbol TEXT,valid_from TEXT,valid_through TEXT)"
        )
        factors.execute(
            "INSERT INTO stock_adjustment_factors VALUES "
            "('000001.SZ','2020-01-01','2026-09-29')"
        )

    incomplete = MODULE._feature_closure(tmp_path, None)
    assert incomplete["closed"] is False
    assert incomplete["missing_symbol_sample"] == ["600000.SH"]

    with sqlite3.connect(tmp_path / "features.sqlite") as features:
        features.execute(
            "INSERT INTO stock_daily_features VALUES "
            "('600000.SH','2026-09-29','TRADED','TRADING')"
        )
    without_factor = MODULE._feature_closure(tmp_path, None)
    assert without_factor["closed"] is True
    assert without_factor["factor_ready"] is False
    with sqlite3.connect(tmp_path / "adjustments.sqlite") as factors:
        factors.execute(
            "INSERT INTO stock_adjustment_factors VALUES "
            "('600000.SH','2020-01-01','2026-09-29')"
        )
    assert MODULE._feature_closure(tmp_path, None)["closed"] is True
