import importlib.util
import sqlite3
import sys
from pathlib import Path

import duckdb

SCRIPT = Path(__file__).parents[2] / "scripts/ops/repair_recent_data_gaps.py"
SPEC = importlib.util.spec_from_file_location("repair_recent_data_gaps", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

DAYS = ("2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18")


def _build_catalog(root: Path, stocks: list[tuple], indices: list[tuple]) -> None:
    with duckdb.connect(str(root / "catalog.duckdb")) as catalog:
        catalog.execute("CREATE TABLE security_calendar(trade_date DATE, is_open BOOLEAN)")
        catalog.execute(
            "CREATE TABLE securities(symbol VARCHAR, asset_type VARCHAR, "
            "listing_date DATE, delisting_date DATE, active BOOLEAN)"
        )
        catalog.executemany(
            "INSERT INTO security_calendar VALUES (?,?)", [(day, True) for day in DAYS]
        )
        catalog.executemany(
            "INSERT INTO securities VALUES (?,?,?,?,?)",
            [
                (sym, "stock", listing, delisting, active)
                for sym, listing, delisting, active in stocks
            ]
            + [(sym, "index", None, None, True) for sym in indices],
        )


def _build_features(root: Path, rows: list[tuple], table: str = "stock_daily_features") -> None:
    with sqlite3.connect(root / "features.sqlite") as conn:
        conn.execute(
            f"CREATE TABLE {table}(symbol TEXT, trade_date TEXT, "
            "calc_status TEXT, trading_status TEXT)"
        )
        conn.executemany(
            f"INSERT INTO {table} VALUES (?,?,?,?)",
            [(sym, day, "TRADED", "TRADED") for sym, day in rows],
        )
        conn.execute("CREATE TABLE market_regime_features(frequency TEXT, period_key TEXT)")
        conn.execute(
            "INSERT INTO market_regime_features VALUES ('D',?)",
            (max((day for _, day in rows), default=DAYS[-1]),),
        )


def _build_index_bars(root: Path, rows: list[tuple]) -> None:
    with sqlite3.connect(root / "indices.sqlite") as conn:
        conn.execute("CREATE TABLE daily_bars(symbol TEXT, trade_date TEXT)")
        conn.executemany("INSERT INTO daily_bars VALUES (?,?)", rows)


def _build_etf_bars(root: Path, days: tuple[str, ...]) -> None:
    with sqlite3.connect(root / "etfs.sqlite") as conn:
        conn.execute("CREATE TABLE daily_bars(symbol TEXT, trade_date TEXT)")
        conn.executemany("INSERT INTO daily_bars VALUES (?,?)", [("510000.SH", d) for d in days])


def test_repair_runs_only_affected_domains(tmp_path):
    gaps = [
        MODULE.Gap("stock", "feature gap", ("2026-09-28",)),
        MODULE.Gap("stock", "missing state", ("2026-09-29",)),
        MODULE.Gap("etf", "empty market date", ("2026-09-29",)),
    ]
    commands = MODULE.repair_commands(tmp_path / "data", gaps, count=30, workers=4)
    assert [command[2] for command in commands] == ["stock", "etf"]
    assert all("--count" in command and "30" in command for command in commands)


def test_gap_detector_ignores_per_stock_history_before_latest_session(tmp_path):
    """Historical per-stock completeness is intentionally outside this bounded check."""
    root = tmp_path / "data"
    root.mkdir()
    _build_catalog(
        root,
        stocks=[
            ("BASE.SH", "2020-01-01", None, True),
            ("NEW.SZ", None, None, True),
        ],
        indices=["000001.SH"],
    )
    _build_features(
        root,
        [("BASE.SH", day) for day in DAYS] + [("NEW.SZ", "2026-09-17"), ("NEW.SZ", "2026-09-18")],
    )
    _build_index_bars(root, [("000001.SH", day) for day in DAYS])
    _build_etf_bars(root, DAYS)

    assert MODULE.detect_recent_gaps(root, count=5) == []


def test_gap_detector_does_not_reconstruct_historical_delisted_universe(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    _build_catalog(
        root,
        stocks=[
            ("BASE.SH", "2020-01-01", None, True),
            ("GONE.SH", "2026-01-01", "2026-09-16", False),
        ],
        indices=["000001.SH"],
    )
    _build_features(root, [("BASE.SH", day) for day in DAYS])
    _build_index_bars(root, [("000001.SH", day) for day in DAYS])
    _build_etf_bars(root, DAYS)

    assert MODULE.detect_recent_gaps(root, count=5) == []


def test_gap_detector_ignores_single_stock_historical_break(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    _build_catalog(
        root,
        stocks=[
            ("A.SH", "2026-01-01", None, True),
            ("BASE.SH", "2020-01-01", None, True),
        ],
        indices=["000001.SH"],
    )
    _build_features(
        root,
        [("BASE.SH", day) for day in DAYS]
        + [("A.SH", "2026-09-15"), ("A.SH", "2026-09-17"), ("A.SH", "2026-09-18")],
    )
    _build_index_bars(root, [("000001.SH", day) for day in DAYS])
    _build_etf_bars(root, DAYS)

    assert MODULE.detect_recent_gaps(root, count=5) == []


def test_gap_detector_flags_whole_stock_session_absence(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    _build_catalog(root, stocks=[("A.SH", "2026-01-01", None, True)], indices=["000001.SH"])
    _build_features(root, [("A.SH", day) for day in DAYS if day != "2026-09-16"])
    _build_index_bars(root, [("000001.SH", day) for day in DAYS])
    _build_etf_bars(root, DAYS)

    gaps = MODULE.detect_recent_gaps(root, count=5)
    missing = next(gap for gap in gaps if gap.asset_type == "stock" and "整日缺失" in gap.reason)
    assert missing.dates == ("2026-09-16",)


def test_gap_detector_checks_full_catalog_on_latest_session(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    _build_catalog(root, stocks=[("A.SH", "2026-01-01", None, True)], indices=["000001.SH"])
    _build_features(root, [("A.SH", day) for day in DAYS[:-1]])  # latest day absent
    _build_index_bars(root, [("000001.SH", day) for day in DAYS])
    _build_etf_bars(root, DAYS)

    gaps = MODULE.detect_recent_gaps(root, count=5)
    missing = next(gap for gap in gaps if gap.asset_type == "stock" and "当前有效" in gap.reason)
    assert missing.dates == ("2026-09-18",)


def test_gap_detector_treats_missing_states_separately_from_coverage(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    _build_catalog(root, stocks=[("A.SH", "2026-01-01", None, True)], indices=["000001.SH"])
    _build_features(root, [("A.SH", day) for day in DAYS])
    with sqlite3.connect(root / "features.sqlite") as conn:
        conn.execute(
            "UPDATE stock_daily_features SET trading_status='MISSING' WHERE trade_date=?",
            ("2026-09-16",),
        )
    _build_index_bars(root, [("000001.SH", day) for day in DAYS])
    _build_etf_bars(root, DAYS)

    gaps = MODULE.detect_recent_gaps(root, count=5)
    reasons = [gap.reason for gap in gaps]
    assert any("MISSING 或 INVALID" in reason for reason in reasons)
    assert not any("当前有效" in reason or "整日缺失" in reason for reason in reasons)


def test_gap_detector_ignores_index_catalog_entries_without_history(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    _build_catalog(root, stocks=[], indices=["BOARD.SH", "000001.SH"])
    _build_features(root, [])
    _build_index_bars(root, [("000001.SH", day) for day in DAYS])
    _build_etf_bars(root, DAYS)

    assert MODULE.detect_recent_gaps(root, count=5) == []


def test_gap_detector_treats_sparse_index_values_as_no_value(tmp_path):
    """Missing index values are no value: sparse boards are never coverage gaps."""
    root = tmp_path / "data"
    root.mkdir()
    _build_catalog(root, stocks=[], indices=["BOARD.SH", "LAG.SH", "000001.SH"])
    _build_features(root, [])
    _build_index_bars(
        root,
        # BOARD.SH: sparse thematic board (e.g. 昨日ST连板) with internal absences.
        [("BOARD.SH", "2026-09-15"), ("BOARD.SH", "2026-09-18")]
        # LAG.SH: board whose values simply stop (e.g. 配股预案 without members).
        + [("LAG.SH", "2026-09-15")]
        + [("000001.SH", day) for day in DAYS],
    )
    _build_etf_bars(root, DAYS)

    assert MODULE.detect_recent_gaps(root, count=5) == []


def test_gap_detector_flags_missing_calendar_anchor(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    _build_catalog(root, stocks=[], indices=["000001.SH", "OTHER.SH"])
    _build_features(root, [])
    _build_index_bars(
        root,
        [("000001.SH", day) for day in DAYS if day != "2026-09-16"]
        + [("OTHER.SH", day) for day in DAYS],
    )
    _build_etf_bars(root, DAYS)

    gaps = MODULE.detect_recent_gaps(root, count=5)
    index_gaps = [gap for gap in gaps if gap.asset_type == "index"]
    assert [gap.reason for gap in index_gaps] == ["上证指数交易日历锚点缺失"]
    assert index_gaps[0].dates == ("2026-09-16",)
    assert index_gaps[0].sample == ("000001.SH",)


def test_gap_detector_regime_follows_feature_table_fallback(tmp_path):
    """Regime check must not hardcode features.sqlite (layout-1 pools have none)."""
    root = tmp_path / "data"
    root.mkdir()
    _build_catalog(root, stocks=[("A.SH", "2026-01-01", None, True)], indices=["000001.SH"])
    with sqlite3.connect(root / "stocks.sqlite") as conn:
        conn.execute(
            "CREATE TABLE daily_features(symbol TEXT, trade_date TEXT, "
            "calc_status TEXT, trading_status TEXT)"
        )
        conn.executemany(
            "INSERT INTO daily_features VALUES (?,?,?,?)",
            [(sym, day, "TRADED", "TRADED") for sym in ("A.SH",) for day in DAYS],
        )
        conn.execute("CREATE TABLE market_daily_summary(frequency TEXT, period_key TEXT)")
        conn.execute("INSERT INTO market_daily_summary VALUES ('D','2026-09-16')")
    _build_index_bars(root, [("000001.SH", day) for day in DAYS])
    _build_etf_bars(root, DAYS)

    gaps = MODULE.detect_recent_gaps(root, count=5)
    assert any("Regime" in gap.reason for gap in gaps)
