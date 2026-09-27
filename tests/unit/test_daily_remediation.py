"""DG-01 regressions: no-op writes, invalidation boundaries and field evidence."""

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest

from aspool.change_observation import daily_changes, empty_cost, row_version
from aspool.daily_storage import merge_daily
from aspool.enrichment import _publish, _recompute_dates
from aspool.free_stockdb import _write_daily, import_daily
from aspool.limit_events import initialize_limits
from aspool.security_facts import initialize_facts
from aspool.store import catalog, daily_path, initialize


def bar(day, amount=1000.0):
    return dict(
        symbol="000001",
        trade_date=day,
        open=10.0,
        high=10.0,
        low=10.0,
        close=10.0,
        volume=100.0,
        amount=amount,
        pre_close=10.0,
        is_st=False,
    )


@pytest.fixture
def pool(tmp_path):
    initialize(tmp_path)
    initialize_facts(tmp_path)
    initialize_limits(tmp_path)
    days = [date(2026, 5, 4) + timedelta(days=i) for i in range(10)]
    merge_daily(tmp_path, "SZ", "000001", [bar(day) for day in days], "test")
    with catalog(tmp_path) as conn:
        conn.execute("CREATE TABLE universe (symbol VARCHAR, asset_type VARCHAR, active BOOLEAN)")
        conn.executemany(
            "INSERT INTO daily_limit_publication VALUES (?, 'test', now())",
            [(day,) for day in days],
        )
        conn.executemany(
            "INSERT INTO security_calendar VALUES (?, true, 'test')", [(day,) for day in days]
        )
    return tmp_path, days


def stale(root):
    with catalog(root) as conn:
        return conn.execute("SELECT * FROM daily_limit_staleness ORDER BY trade_date").fetchall()


def test_daily_delta_and_noop_preserve_file_coverage_and_staleness(pool):
    root, days = pool
    changes, metrics = [], empty_cost()
    assert (
        merge_daily(
            root, "SZ", "000001", [bar(days[-1], 1200)], "test", changes=changes, metrics=metrics
        )
        == 1
    )
    assert changes[0]["fields"] == ["amount"]
    assert changes[0]["before"] == {"amount": 1000.0}
    assert changes[0]["after"] == {"amount": 1200.0}
    assert changes[0]["input_row_version"] != changes[0]["output_row_version"]
    assert metrics["changed_rows"] == 1
    assert metrics["rows_rewritten"] == len(days)
    assert metrics["stale_date_marks"] == 1
    path = daily_path(root, "SZ", "000001")
    before = path.read_bytes(), path.stat().st_mtime_ns, stale(root)
    with catalog(root) as conn:
        coverage = conn.execute("SELECT * FROM coverage").fetchall()
    changes, metrics = [], empty_cost()
    assert (
        merge_daily(
            root, "SZ", "000001", [bar(days[-1], 1200)], "test", changes=changes, metrics=metrics
        )
        == 0
    )
    assert changes == []
    assert metrics["rows_read"] == len(days)
    assert metrics["files_rewritten"] == metrics["stale_date_marks"] == 0
    assert (path.read_bytes(), path.stat().st_mtime_ns, stale(root)) == before
    with catalog(root) as conn:
        assert conn.execute("SELECT * FROM coverage").fetchall() == coverage


def test_row_versions_ignore_null_representation_and_numeric_storage_type():
    assert row_version({"close": 10, "is_st": False, "extra": None}) == row_version(
        {"close": 10.0, "is_st": False, "extra": float("nan")}
    )
    assert row_version({"is_st": False}) != row_version({"is_st": None})
    assert row_version({"value": 2**53}) != row_version({"value": 2**53 + 1})
    assert (
        daily_changes(
            {date(2026, 1, 1): {"trade_date": date(2026, 1, 1), "x": None}},
            [{"trade_date": date(2026, 1, 1), "x": pd.NA}],
            "000001.SZ",
            "test",
            "test",
        )
        == []
    )


def test_enrichment_noop_and_actual_change_boundary(pool):
    root, days = pool
    first = _publish(root, [("000001", "SZ", None)], days, days[0], days[-1])
    assert "error" not in first[0]
    assert first[0]["changed_rows"] > 0
    with catalog(root) as conn:
        conn.execute("DELETE FROM daily_limit_staleness")
    path = daily_path(root, "SZ", "000001")
    before = path.read_bytes(), path.stat().st_mtime_ns
    replay = _publish(root, [("000001", "SZ", None)], days, days[0], days[-1])
    assert replay[0]["changed_rows"] == 0
    assert replay[0]["change_report"] is None
    assert replay[0]["cost"]["files_rewritten"] == 0
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert stale(root) == []
    assert _recompute_dates(root, days[0], days[-1], replay) == []
    # Simulate a correction that only the enrichment pass should repair.
    rows = pq.ParquetFile(path).read().to_pylist()
    rows[-1]["pct_chg"] = 99.0
    _write_daily(root, "SZ", "000001", rows)
    result = _publish(root, [("000001", "SZ", None)], days, days[0], days[-1])
    assert result[0]["changed_rows"] == 1
    assert [row[0] for row in stale(root)] == [days[-1]]
    changes = json.loads(Path(result[0]["change_report"]).read_text())["changes"]
    assert changes[0]["fields"] == ["pct_chg"]
    assert _recompute_dates(root, days[0], days[-1], result) == [days[-1]]


def test_recompute_keeps_existing_stale_and_unpublished_dates(pool):
    root, days = pool
    with catalog(root) as conn:
        conn.execute("INSERT INTO daily_limit_staleness VALUES (?, 'retry', now())", [days[2]])
        conn.execute("DELETE FROM daily_limit_publication WHERE trade_date=?", [days[-1]])
    assert _recompute_dates(root, days[0], days[-1], []) == [days[2], days[-1]]


def test_failed_enrichment_replace_retains_stale_and_original_file(pool, monkeypatch):
    root, days = pool
    path = daily_path(root, "SZ", "000001")
    original = path.read_bytes()
    real_replace = type(path).replace

    def fail_replace(self, target):
        if self.suffix == ".part":
            raise OSError("injected replace failure")
        return real_replace(self, target)

    monkeypatch.setattr(type(path), "replace", fail_replace)
    results = _publish(root, [("000001", "SZ", None)], days, days[0], days[-1])
    assert "injected" in results[0]["error"]
    assert results[0]["change_report"] is None
    assert path.read_bytes() == original
    assert stale(root)
    assert list(path.parent.glob("*.part")) == []


@pytest.mark.parametrize("incremental", [False, True])
def test_free_stockdb_replay_and_empty_response_keep_history(pool, monkeypatch, incremental):
    root, days = pool
    incoming = [bar(days[-1], 1500.0)]

    class Source:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def symbols(self):
            return ["000001"]

        def daily_bars_many(self, *args):
            yield "000001", incoming

    monkeypatch.setattr("aspool.free_stockdb.FreeStockDb", Source)
    assert import_daily(root, root / "source", incremental).rows == 1
    path = daily_path(root, "SZ", "000001")
    before = path.read_bytes(), path.stat().st_mtime_ns, stale(root)
    assert len(pq.ParquetFile(path).read()) == len(days)
    assert import_daily(root, root / "source", incremental).rows == 0
    incoming.clear()
    assert import_daily(root, root / "source", incremental).rows == 0
    assert (path.read_bytes(), path.stat().st_mtime_ns, stale(root)) == before


def test_offline_import_replay_uses_delta_merge(pool, monkeypatch):
    from types import SimpleNamespace

    from aspool.tdx_online import update_daily_offline

    root, days = pool
    item = SimpleNamespace(
        year=days[-1].year,
        month=days[-1].month,
        day=days[-1].day,
        open=10.0,
        high=10.0,
        low=10.0,
        close=10.0,
        vol=1.0,
        amount=1400.0,
    )
    monkeypatch.setattr("tdxman.offline.find_daily_bar_file", lambda *args: "unused")
    monkeypatch.setattr("tdxman.offline.read_daily_bars", lambda *args: [item])
    assert update_daily_offline(root, limit=1) == (1, 1)
    path = daily_path(root, "SZ", "000001")
    before = path.read_bytes(), path.stat().st_mtime_ns, stale(root)
    assert update_daily_offline(root, limit=1) == (1, 0)
    assert (path.read_bytes(), path.stat().st_mtime_ns, stale(root)) == before


def test_etf_offline_change_does_not_invalidate_stock_publications(pool):
    root, days = pool
    assert (
        merge_daily(
            root,
            "SZ",
            "159915",
            [bar(days[-1]) | {"symbol": "159915", "asset_type": "etf"}],
            "tdxman:etf:offline",
        )
        == 1
    )
    assert stale(root) == []


def test_existing_derived_value_is_invalidated_when_input_changes():
    from aspool.tdx_online import _merge_rows

    day = date(2026, 5, 4)
    rows = _merge_rows(
        [{"trade_date": day, "close": 10.0, "pct_chg": 1.0}],
        [{"trade_date": day, "close": 11.0}],
        "trade_date",
    )
    assert rows[0]["pct_chg"] is None
    assert rows[0]["pct_chg_source"] == "unknown:inputs_changed"
    assert "float_mv" not in rows[0]


def test_lifecycle_fill_is_idempotent_and_still_invalidates(pool):
    root, days = pool
    source = {
        "finance": {"code": "000001", "ipo_date": "19910403"},
        "events": [],
        "fetched_date": days[-1].isoformat(),
    }
    results = _publish(root, [("000001", "SZ", source)], days, days[0], days[-1])
    assert "error" not in results[0]
    assert results[0]["lifecycle_changed"] is True
    assert stale(root)
    with catalog(root) as conn:
        before = conn.execute("SELECT * FROM security_lifecycle").fetchall()
        conn.execute("DELETE FROM daily_limit_staleness")
    results = _publish(root, [("000001", "SZ", source)], days, days[0], days[-1])
    assert "error" not in results[0]
    assert results[0]["lifecycle_changed"] is False
    assert results[0]["changed_rows"] == 0
    with catalog(root) as conn:
        assert conn.execute("SELECT * FROM security_lifecycle").fetchall() == before
    assert stale(root) == []
