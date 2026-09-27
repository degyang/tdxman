"""DG-01 durable commit, category replay and process-crash acceptance."""

import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pyarrow.parquet as pq
import pytest

from aspool import DataPool
from aspool.api_contract import DataPoolError
from aspool.baostock_source import _publish as bao_publish
from aspool.baostock_source import _publish_calendar
from aspool.change_protocol import catalog_rows, pending, recover
from aspool.daily_storage import merge_daily
from aspool.enrichment import _publish as enrich_publish
from aspool.free_stockdb import import_daily
from aspool.fundamentals import _publish_quote_rows, _snapshot_rows, _write
from aspool.index_pool import _ensure_index_catalog, save_index
from aspool.limit_events import initialize_limits
from aspool.pool import pool_lock
from aspool.security_facts import initialize_facts
from aspool.store import catalog, daily_path, initialize
from aspool.tdx_online import _publish_stock_rows, update_daily_offline
from aspool.universe import _publish_universe, universe_is_stale

DAY = date(2026, 5, 4)
ITEM = dict(market="SH", code="000001", name="上证")


def bar(amount=1000.0):
    return dict(
        symbol="000001",
        trade_date=DAY,
        open=10.0,
        high=10.0,
        low=10.0,
        close=10.0,
        volume=100.0,
        amount=amount,
        pre_close=10.0,
        is_st=False,
    )


def index_bar(amount=1000.0):
    return dict(
        date=DAY,
        open=10.0,
        high=10.0,
        low=10.0,
        close=10.0,
        vol=100.0,
        amount=amount,
        up_count=5,
        down_count=6,
    )


def snapshot(shares=2):
    return _snapshot_rows(
        pd.DataFrame(
            [
                dict(
                    code="000001",
                    name="甲",
                    total_shares=shares,
                    float_shares=1,
                    close=10,
                    pe_ttm=10,
                )
            ]
        ),
        datetime.now(),
    )


@pytest.fixture
def pool(tmp_path):
    initialize(tmp_path)
    initialize_facts(tmp_path)
    initialize_limits(tmp_path)
    _ensure_index_catalog(tmp_path)
    merge_daily(tmp_path, "SZ", "000001", [bar()], "test")
    save_index(tmp_path, ITEM, [index_bar()], "test")
    _write(tmp_path, snapshot())
    _publish_universe(tmp_path, [dict(symbol="000001", market="SZ", name="甲")])
    with catalog(tmp_path) as conn:
        conn.execute("INSERT INTO daily_limit_publication VALUES (?, 'test', now())", [DAY])
        conn.execute("DELETE FROM daily_limit_staleness")
    return tmp_path


def state(root):
    with catalog(root) as conn:
        return dict(
            revisions=conn.execute("SELECT * FROM business_revisions ORDER BY 1").fetchall(),
            stale=conn.execute("SELECT * FROM daily_limit_staleness ORDER BY 1").fetchall(),
            coverage=conn.execute("SELECT * FROM coverage ORDER BY 1").fetchall(),
            index=conn.execute("SELECT * FROM index_coverage ORDER BY 1,2").fetchall(),
        )


def counts(root):
    with catalog(root) as conn:
        return conn.execute("SELECT count(*), sum(changed_rows) FROM business_changes").fetchone()


def invoke(category, root, monkeypatch):
    if category == "merge":
        return merge_daily(root, "SZ", "000001", [bar(1234)], "test")
    if category == "direct":
        from aspool.free_stockdb import _write_daily

        return _write_daily(root, "SZ", "000001", [bar(1234)])
    if category == "online":
        return _publish_stock_rows(root, [("000001", [bar(1234)])])
    if category == "enrichment":
        return enrich_publish(root, [("000001", "SZ", None)], [DAY], DAY, DAY)
    if category == "quote":
        return _publish_quote_rows(root, [("000001", bar(1234), snapshot()[0])])
    if category == "snapshot":
        return _write(root, snapshot(3))
    if category == "index":
        return save_index(root, ITEM, [index_bar(1234)], "offline")
    if category == "offline":
        item = SimpleNamespace(
            year=DAY.year,
            month=DAY.month,
            day=DAY.day,
            open=10,
            high=10,
            low=10,
            close=10,
            vol=1,
            amount=1234,
        )
        monkeypatch.setattr("tdxman.offline.find_daily_bar_file", lambda *args: "unused")
        monkeypatch.setattr("tdxman.offline.read_daily_bars", lambda *args: [item])
        return update_daily_offline(root, limit=1)
    if category == "free-stockdb":

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
                yield "000001", [dict(bar(1234), trade_date="20260504")]

        monkeypatch.setattr("aspool.free_stockdb.FreeStockDb", Source)
        return import_daily(root, root / "input", False)
    if category == "calendar":
        return _publish_calendar(
            root, pd.DataFrame([dict(date=DAY, is_open=True, source="baostock")])
        )
    if category == "lifecycle":
        return catalog_rows(
            root,
            "security_lifecycle",
            ["symbol"],
            [
                dict(
                    symbol="000001.SZ",
                    listing_date=date(2020, 1, 1),
                    source="baostock",
                    fetched_at=datetime.now(),
                )
            ],
            source="baostock",
            reason="test_lifecycle",
            ignore=("fetched_at",),
            stale_start=date.min,
        )
    if category == "facts":
        return catalog_rows(
            root,
            "security_daily_facts",
            ["symbol", "trade_date"],
            [
                dict(
                    symbol="000001.SZ",
                    trade_date=DAY,
                    pre_close=10.0,
                    is_st=False,
                    trading_status="TRADING",
                    source="baostock",
                    fetched_at=datetime.now(),
                )
            ],
            source="baostock",
            reason="test_facts",
            ignore=("fetched_at",),
            stale_start=DAY,
        )
    if category == "universe":
        return _publish_universe(root, [dict(symbol="000001", market="SZ", name="乙")])
    raise AssertionError(category)


CATEGORIES = [
    "merge",
    "direct",
    "online",
    "enrichment",
    "quote",
    "snapshot",
    "index",
    "offline",
    "free-stockdb",
    "calendar",
    "lifecycle",
    "facts",
    "universe",
]


@pytest.mark.parametrize("category", CATEGORIES)
def test_every_entry_replay_has_zero_business_change(pool, monkeypatch, category):
    invoke(category, pool, monkeypatch)
    first, ledger = state(pool), counts(pool)
    files = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in (pool / "lake").rglob("*.parquet")}
    invoke(category, pool, monkeypatch)
    assert state(pool) == first
    assert counts(pool) == ledger
    assert all((p.stat().st_mtime_ns, p.read_bytes()) == value for p, value in files.items())
    with catalog(pool) as conn:
        metrics = json.loads(
            conn.execute(
                "SELECT metrics FROM maintenance_runs ORDER BY rowid DESC LIMIT 1"
            ).fetchone()[0]
        )
        assert metrics["changed_row_events"] == metrics["changed_field_events"] == 0
        assert metrics["downstream_cache_evictions"] is None
        assert metrics["peak_rss_process_high_water_bytes"] > 0
        assert metrics["elapsed_seconds"] >= metrics["lock_wait_seconds"] >= 0


PHASES = [
    "after_prepared",
    "before_replace",
    "after_replace",
    "after_coverage",
    "before_catalog_commit",
    "after_catalog_commit",
    "after_applied",
    "after_redo_release",
]


@pytest.mark.parametrize("phase", PHASES)
def test_all_file_crash_windows_roll_forward_exactly_once(pool, monkeypatch, phase):
    import aspool.change_protocol as protocol

    old_count = counts(pool)[0]

    def fail(at):
        if at == phase:
            raise OSError("injected crash seam")

    monkeypatch.setattr(protocol, "_fault", fail)
    with pytest.raises(OSError, match="crash seam"):
        invoke("merge", pool, monkeypatch)
    assert pending(pool)
    with pytest.raises(DataPoolError, match="recover"):
        DataPool(pool).read_research_daily(symbols="000001.SZ")
    with catalog(pool) as conn:
        metrics = json.loads(
            conn.execute(
                "SELECT metrics FROM maintenance_runs ORDER BY rowid DESC LIMIT 1"
            ).fetchone()[0]
        )
        if phase not in {"before_replace", "after_prepared"}:
            assert metrics["ranges"]["physical_replace"]["row_visits"] == 1
    monkeypatch.setattr(protocol, "_fault", lambda at: None)
    with pool_lock(pool, write=True):
        recover(pool)
        assert not pending(pool)
    assert counts(pool)[0] == old_count + 1
    public = DataPool(pool).read_research_daily(symbols="000001.SZ")
    assert public.iloc[0].amount == 1234
    with catalog(pool) as conn:
        assert conn.execute("SELECT row_count FROM coverage").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM daily_limit_staleness").fetchone()[0] == 1
    before = state(pool), counts(pool), daily_path(pool, "SZ", "000001").stat().st_mtime_ns
    invoke("merge", pool, monkeypatch)
    assert (
        state(pool),
        counts(pool),
        daily_path(pool, "SZ", "000001").stat().st_mtime_ns,
    ) == before
    assert not list((pool / "change-state/applied").rglob("*.parquet"))


@pytest.mark.parametrize("category", CATEGORIES)
def test_each_entry_fault_is_detectable_and_retry_matches_clean_reference(
    pool, tmp_path, monkeypatch, category
):
    import shutil

    import aspool.change_protocol as protocol

    reference = tmp_path / "reference"
    # This deliberately small laboratory pool has three one-row files.
    shutil.copytree(pool, reference, ignore=shutil.ignore_patterns("reference"))
    invoke(category, reference, monkeypatch)
    phase = (
        "before_catalog_rows_commit"
        if category in {"calendar", "lifecycle", "facts", "universe"}
        else "after_replace"
    )

    def fail(at):
        if at == phase:
            raise OSError("entry failure")

    monkeypatch.setattr(protocol, "_fault", fail)
    try:
        result = invoke(category, pool, monkeypatch)
    except OSError:
        result = None
    if result is not None:
        # Online/enrichment/quote preserve failures in their existing return contract.
        assert category in {"online", "enrichment", "quote"}
        assert "entry failure" in str(result)
    monkeypatch.setattr(protocol, "_fault", lambda at: None)
    if pending(pool):
        with pool_lock(pool, write=True):
            recover(pool)
    invoke(category, pool, monkeypatch)
    for relative in (
        "lake/bars/daily/market=SZ/symbol=000001/bars.parquet",
        "lake/fundamentals/snapshots.parquet",
        "lake/indices/daily/market=SH/symbol=000001/bars.parquet",
    ):
        actual = (
            pq.ParquetFile(pool / relative)
            .read()
            .to_pandas()
            .drop(columns=["refreshed_at"], errors="ignore")
        )
        expected = (
            pq.ParquetFile(reference / relative)
            .read()
            .to_pandas()
            .drop(columns=["refreshed_at"], errors="ignore")
        )
        pd.testing.assert_frame_equal(actual, expected, check_exact=True)
    with catalog(pool) as conn, catalog(reference) as other:
        for table in (
            "security_calendar",
            "security_lifecycle",
            "security_daily_facts",
            "universe",
        ):
            a = (
                conn.execute(f"SELECT * FROM {table} ORDER BY 1")
                .fetchdf()
                .drop(columns=["fetched_at", "first_seen", "last_seen"], errors="ignore")
            )
            b = (
                other.execute(f"SELECT * FROM {table} ORDER BY 1")
                .fetchdf()
                .drop(columns=["fetched_at", "first_seen", "last_seen"], errors="ignore")
            )
            pd.testing.assert_frame_equal(a, b)
        assert (
            conn.execute(
                "SELECT symbol,market,start_date,end_date,row_count FROM coverage"
            ).fetchall()
            == other.execute(
                "SELECT symbol,market,start_date,end_date,row_count FROM coverage"
            ).fetchall()
        )
    assert not pending(pool)


@pytest.mark.parametrize("phase", PHASES)
def test_real_process_exit_and_next_writer_recovery(pool, monkeypatch, phase):
    code = """
import os
from pathlib import Path
from datetime import date
from aspool.pool import pool_lock
from aspool.daily_storage import merge_daily
import aspool.change_protocol as p
p._fault=lambda phase: os._exit(73) if phase == PHASE else None
with pool_lock(ROOT, write=True):
    merge_daily(ROOT, 'SZ', '000001', [dict(symbol='000001', trade_date=date(2026,5,4),
      open=10., high=10., low=10., close=10., volume=100., amount=1234.,
      pre_close=10., is_st=False)], 'test')
"""
    code = f'ROOT=__import__("pathlib").Path({str(pool)!r})\nPHASE={phase!r}\n' + code
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).parents[2],
        env=dict(os.environ, XMTDX_LIVE="0"),
        timeout=30,
    )
    assert result.returncode == 73
    with pool_lock(pool, write=True):
        recover(pool)
    assert not pending(pool)
    assert DataPool(pool).read_research_daily(symbols="000001.SZ").iloc[0].amount == 1234
    before = state(pool), counts(pool)
    invoke("merge", pool, monkeypatch)
    assert (state(pool), counts(pool)) == before


@pytest.mark.parametrize("damage", ["missing_redo", "corrupt_redo", "missing_evidence"])
def test_corrupt_preparation_is_rejected_without_false_applied(pool, monkeypatch, damage):
    import aspool.change_protocol as protocol

    def fail(at):
        if at == "after_prepared":
            raise OSError("stop")

    monkeypatch.setattr(protocol, "_fault", fail)
    with pytest.raises(OSError):
        invoke("merge", pool, monkeypatch)
    directory = next((pool / "change-state/pending").iterdir())
    if damage == "missing_redo":
        (directory / "new.parquet").unlink()
    elif damage == "corrupt_redo":
        (directory / "new.parquet").write_bytes(b"broken")
    else:
        (directory / "rows.jsonl").unlink()
    before = counts(pool)
    monkeypatch.setattr(protocol, "_fault", lambda at: None)
    with pytest.raises(RuntimeError, match="missing|corrupt"):
        recover(pool)
    assert pending(pool) and counts(pool) == before


def test_baostock_real_publisher_noop_empty_and_conflict_retraction(pool):
    basic = dict(listing_date=date(2020, 1, 1), delisting_date=None, name="甲")
    row = dict(bar(), date=DAY, trading_status="TRADING", turnover_rate=2.0, pe_ttm=10.0, pb=2.0)
    frame = pd.DataFrame([row])
    bao_publish(pool, "SZ", "000001", basic, frame, DAY, DAY)
    before = state(pool), counts(pool)
    assert bao_publish(pool, "SZ", "000001", basic, frame, DAY, DAY)[:2] == (0, 0)
    assert (state(pool), counts(pool)) == before
    assert bao_publish(pool, "SZ", "000001", basic, frame.iloc[:0], DAY, DAY)[:2] == (0, 0)
    assert (state(pool), counts(pool)) == before
    # Explicit source conflict adjudication may retract only a dated overlay,
    # keeping the primary bar and its reliable pre_close.
    conflict = pd.DataFrame([dict(row, pre_close=9.0)])
    bao_publish(pool, "SZ", "000001", basic, conflict, DAY, DAY)
    with catalog(pool) as conn:
        assert conn.execute("SELECT pre_close FROM security_daily_facts").fetchone()[0] is None
    assert DataPool(pool).read_research_daily(symbols="000001.SZ").iloc[0].pre_close == 10.0
    before = state(pool), counts(pool)
    bao_publish(pool, "SZ", "000001", basic, conflict, DAY, DAY)
    assert (state(pool), counts(pool)) == before


def test_universe_omissions_and_empty_source_preserve_facts_and_ttl(pool):
    before = state(pool), counts(pool)
    _publish_universe(pool, [])
    assert (state(pool), counts(pool)) == before
    _publish_universe(pool, [dict(symbol="000002", market="SZ", name="乙")])
    with catalog(pool) as conn:
        assert conn.execute("SELECT active FROM universe WHERE symbol='000001'").fetchone() == (
            True,
        )
        conn.execute("UPDATE universe SET last_seen = current_timestamp - INTERVAL 30 DAY")
    _publish_universe(pool, [dict(symbol="000001", market="SZ", name="甲")])
    assert not universe_is_stale(pool, asset_type="stock")
    with pytest.raises(ValueError, match="unsupported"):
        _publish_universe(pool, [dict(symbol="000001", market="SZ", name="甲", active=False)])
    with catalog(pool) as conn:
        assert conn.execute("SELECT active FROM universe WHERE symbol='000001'").fetchone() == (
            True,
        )


def test_explicit_delete_rejected_before_mutation(pool):
    before = state(pool), counts(pool)
    with pytest.raises(ValueError, match="unsupported"):
        merge_daily(pool, "SZ", "000001", [dict(bar(), operation="delete")], "test")
    with pytest.raises(ValueError, match="unsupported"):
        save_index(pool, ITEM, [dict(index_bar(), operation="delete")], "test")
    assert (state(pool), counts(pool)) == before


def test_many_updates_keep_only_delta_history_and_bounded_row_spool(pool):
    from aspool.change_observation import ChangeSpool

    for amount in range(1100, 1110):
        merge_daily(pool, "SZ", "000001", [bar(amount)], "test")
    assert not list((pool / "change-state/applied").rglob("new.parquet"))
    assert not pending(pool)
    records = [
        json.loads(p.read_text()) for p in (pool / "change-state/applied").rglob("manifest.json")
    ]
    assert all(r["state"] == "applied" for r in records)
    spool = ChangeSpool(pool)
    spool.extend(dict(trade_date=str(DAY), value=i) for i in range(10000))
    assert len(spool) == 10000
    assert not any(isinstance(v, list) for v in vars(spool).values())
    assert sum(1 for _ in spool) == 10000
    spool.close()


def test_task_aggregation_uses_point_totals_not_change_history(pool):
    import inspect

    from aspool.change_protocol import _finish_run

    assert "SUM(" not in inspect.getsource(_finish_run).upper()
    assert "FROM business_changes" not in inspect.getsource(_finish_run)
    with catalog(pool) as conn:
        conn.execute(
            "INSERT INTO maintenance_totals SELECT 'old-' || i, 1, 1, 1 FROM range(20000) t(i)"
        )
        plan = conn.execute(
            "EXPLAIN ANALYZE SELECT * FROM maintenance_totals WHERE run_id='old-100'"
        ).fetchone()[1]
    assert "Index Scan" in plan


def test_baostock_missing_optional_fields_do_not_retract_known_overlay(pool):
    basic = dict(listing_date=date(2020, 1, 1), delisting_date=None, name="甲")
    row = dict(bar(), date=DAY, trading_status="TRADING", turnover_rate=2.0, pe_ttm=10.0, pb=2.0)
    bao_publish(pool, "SZ", "000001", basic, pd.DataFrame([row]), DAY, DAY)
    before = state(pool), counts(pool)
    missing = pd.DataFrame([dict(row, pre_close=None, is_st=None)])
    assert bao_publish(pool, "SZ", "000001", basic, missing, DAY, DAY)[:2] == (0, 0)
    assert (state(pool), counts(pool)) == before
    with catalog(pool) as conn:
        assert conn.execute("SELECT pre_close, is_st FROM security_daily_facts").fetchone() == (
            10,
            False,
        )


def test_snapshot_refuses_pending_without_recovering_source(pool, tmp_path, monkeypatch):
    import importlib.util

    import aspool.change_protocol as protocol

    spec = importlib.util.spec_from_file_location(
        "baseline", Path(__file__).parents[2] / "scripts/aspool_recovery_baseline.py"
    )
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)

    def fail(at):
        if at == "after_prepared":
            raise OSError("stop before source replacement")

    monkeypatch.setattr(protocol, "_fault", fail)
    with pytest.raises(OSError):
        merge_daily(pool, "SZ", "000001", [bar(1234)], "test")
    target = daily_path(pool, "SZ", "000001")
    before = target.read_bytes(), counts(pool), state(pool)
    monkeypatch.setattr(protocol, "_fault", lambda at: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "baseline",
            "--root",
            str(pool),
            "--destination",
            str(pool.parent / (pool.name + "-snapshot-destination")),
            "--evidence",
            str(pool.parent / (pool.name + "-snapshot-evidence")),
        ],
    )
    with pytest.raises(DataPoolError, match="recover"):
        baseline.main()
    assert pending(pool)
    assert (target.read_bytes(), counts(pool), state(pool)) == before


def test_snapshot_inventory_includes_durable_change_state(pool):
    from scripts.aspool_verify_baseline import inventory

    objects = inventory(pool)
    assert any(p.startswith("change-state/applied/") and p.endswith("rows.jsonl") for p in objects)
    assert any(
        p.startswith("change-state/applied/") and p.endswith("manifest.json") for p in objects
    )


def test_direct_yearly_replacement_and_optional_retraction_have_exact_evidence(tmp_path):
    import pyarrow as pa

    from aspool.free_stockdb import _write_daily
    from aspool.store import daily_year_path, read_daily_table

    initialize(tmp_path)
    rows = [dict(bar(), trade_date=date(2025, 12, 31)), bar()]
    for row in rows:
        path = daily_year_path(tmp_path, "SZ", "000001", row["trade_date"].year)
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist([row]), path)
    old_path = daily_year_path(tmp_path, "SZ", "000001", 2025)
    old = old_path.read_bytes(), old_path.stat().st_mtime_ns
    changed = [rows[0], dict(rows[1], pre_close=None)]
    _write_daily(tmp_path, "SZ", "000001", changed)
    assert read_daily_table(tmp_path, "SZ", "000001").to_pylist() == changed
    assert (old_path.read_bytes(), old_path.stat().st_mtime_ns) == old
    records = list((tmp_path / "change-state/applied").rglob("rows.jsonl"))
    assert len(records) == 1
    delta = json.loads(records[0].read_text())
    assert delta["before"] == {"pre_close": 10.0} and delta["after"] == {"pre_close": None}
    before = counts(tmp_path)
    _write_daily(tmp_path, "SZ", "000001", changed)
    assert counts(tmp_path) == before
    with pytest.raises(ValueError, match="deletions"):
        _write_daily(tmp_path, "SZ", "000001", changed[:1])


def test_quote_full_command_noop_skips_compute_and_source_outage_preserves_data(pool, monkeypatch):
    from aspool.fundamentals import QuoteUpdateError, update_from_quotes

    quote = dict(
        code="000001",
        name="甲",
        open=10.0,
        high=10.0,
        low=10.0,
        close=10.0,
        pre_close=10.0,
        vol=1.0,
        amount=1234.0,
        total_shares=2.0,
        float_shares=1.0,
        server_update_date=DAY.strftime("%Y%m%d"),
    )
    monkeypatch.setattr("aspool.fundamentals.client_factory", lambda *args: (None, None))
    monkeypatch.setattr(
        "aspool.fundamentals.fetch_sync",
        lambda *args: [(["000001"], pd.DataFrame([quote]), None, 0)],
    )
    now = datetime.combine(DAY, datetime.min.time()) + timedelta(hours=17)
    assert update_from_quotes(pool, now=now)[0] == 1
    before = state(pool), counts(pool)

    def forbidden(*args, **kwargs):
        raise AssertionError("Healthy no-op must not recompute")

    monkeypatch.setattr("aspool.limit_events.compute_limit_events", forbidden)
    assert update_from_quotes(pool, now=now) == (1, 0)
    assert (state(pool), counts(pool)) == before
    monkeypatch.setattr(
        "aspool.fundamentals.fetch_sync",
        lambda *args: [(["000001"], None, OSError("source outage"), 0)],
    )
    with pytest.raises(QuoteUpdateError, match="部分报价"):
        update_from_quotes(pool, now=now)
    assert (state(pool), counts(pool)) == before


def test_online_command_empty_and_failed_fetch_preserve_facts(pool, monkeypatch):
    from aspool.tdx_online import update_daily

    monkeypatch.setattr("aspool.tdx_online.client_factory", lambda *args: (None, None))
    monkeypatch.setattr(
        "aspool.tdx_online.fetch_sync", lambda *args: [(("000001", DAY, "stock"), [], None, 0)]
    )
    before = state(pool), counts(pool)
    assert update_daily(pool, limit=1) == (0, 0)
    assert (state(pool), counts(pool)) == before
    monkeypatch.setattr(
        "aspool.tdx_online.fetch_sync",
        lambda *args: [(("000001", DAY, "stock"), None, OSError("source outage"), 0)],
    )
    with pytest.raises(ValueError, match="部分股票"):
        update_daily(pool, limit=1)
    assert (state(pool), counts(pool)) == before


def test_baostock_full_command_refresh_health_ttl_without_business_change(pool, monkeypatch):
    from aspool.baostock_source import supplement_daily

    basic = dict(listing_date=date(2020, 1, 1), delisting_date=None, name="甲")
    row = dict(bar(), date=DAY, trading_status="TRADING", turnover_rate=2.0, pe_ttm=10.0, pb=2.0)
    frame = pd.DataFrame([row])
    _publish_calendar(pool, pd.DataFrame([dict(date=DAY, is_open=True, source="baostock")]))
    bao_publish(pool, "SZ", "000001", basic, frame, DAY, DAY)
    with catalog(pool) as conn:
        conn.execute("DELETE FROM daily_limit_staleness")
        conn.execute(
            "UPDATE fetch_observations SET observed_at=current_timestamp-INTERVAL 30 DAY "
            "WHERE object_key='lifecycle:000001.SZ'"
        )
    before = state(pool), counts(pool)

    class Source:
        calls = 0
        failed = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        @staticmethod
        def security_code(*args):
            return "sz.000001"

        def get_trade_calendar(self, *args):
            return pd.DataFrame([dict(date=DAY, is_open=True, source="baostock")])

        def get_stock_basic(self, *args):
            Source.calls += 1
            return pd.DataFrame([basic])

        def get_daily(self, *args, **kwargs):
            if Source.failed:
                raise OSError("source outage")
            return frame

    monkeypatch.setattr("aspool.baostock_source.BaostockClient", Source)

    def forbidden(*args, **kwargs):
        raise AssertionError("No-op supplement must not recompute")

    monkeypatch.setattr("aspool.limit_events.compute_limit_events", forbidden)
    report, _ = supplement_daily(pool, start=DAY, end=DAY, limit=1)
    assert report["changed_rows"] == report["fact_rows"] == 0 and Source.calls == 1
    assert (state(pool), counts(pool)) == before
    supplement_daily(pool, start=DAY, end=DAY, limit=1)
    assert Source.calls == 1  # Fresh observation, unchanged business fetched_at.
    Source.failed = True
    with catalog(pool) as conn:
        conn.execute("UPDATE fetch_observations SET observed_at=current_timestamp-INTERVAL 30 DAY")
    report, _ = supplement_daily(pool, start=DAY, end=DAY, limit=1)
    assert report["status"] == "partial" and "source outage" in str(report["failed"])
    assert (state(pool), counts(pool)) == before


def test_real_lock_contention_is_accounted(pool):
    import time

    ready = pool.parent / (pool.name + "-lock-ready")
    code = f"""
import fcntl, os, time
from pathlib import Path
fd=os.open({str(pool)!r}, os.O_RDONLY)
fcntl.flock(fd, fcntl.LOCK_EX)
Path({str(ready)!r}).write_text('locked')
time.sleep(0.3)
fcntl.flock(fd, fcntl.LOCK_UN)
os.close(fd)
"""
    process = subprocess.Popen([sys.executable, "-c", code])
    try:
        deadline = time.monotonic() + 5
        while not ready.exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        _publish_stock_rows(pool, [("000001", [bar(1234)])])
        with catalog(pool) as conn:
            metrics = json.loads(
                conn.execute(
                    "SELECT metrics FROM maintenance_runs ORDER BY rowid DESC LIMIT 1"
                ).fetchone()[0]
            )
        assert metrics["lock_wait_seconds"] >= 0.15
        assert metrics["changed_row_events"] == 1
        assert metrics["temporary_peak_bytes_proxy"] > 0
        assert metrics["ranges"]["candidate"]["row_visits"] == 1
    finally:
        process.wait(timeout=5)


@pytest.mark.parametrize("phase", ["before_prepare_manifest", "before_pending"])
def test_unpublished_preparation_aborts_without_changing_business_or_retaining_cold_redo(
    pool, monkeypatch, phase
):
    import aspool.change_protocol as protocol

    original = state(pool), counts(pool), daily_path(pool, "SZ", "000001").read_bytes()

    def fail(at):
        if at == phase:
            raise OSError("unpublished preparation")

    monkeypatch.setattr(protocol, "_fault", fail)
    with pytest.raises(OSError):
        merge_daily(pool, "SZ", "000001", [bar(1234)], "test")
    assert not pending(pool)
    assert DataPool(pool).read_research_daily(symbols="000001.SZ").iloc[0].amount == 1000
    monkeypatch.setattr(protocol, "_fault", lambda at: None)
    with pool_lock(pool, write=True):
        recover(pool)
    assert (state(pool), counts(pool), daily_path(pool, "SZ", "000001").read_bytes()) == original
    assert not list((pool / "change-state/unprepared").rglob("new.parquet"))


def test_missing_pending_manifest_keeps_public_reads_blocked(pool, monkeypatch):
    import aspool.change_protocol as protocol

    def fail(at):
        if at == "after_replace":
            raise OSError("stop")

    monkeypatch.setattr(protocol, "_fault", fail)
    with pytest.raises(OSError):
        merge_daily(pool, "SZ", "000001", [bar(1234)], "test")
    directory = next((pool / "change-state/pending").iterdir())
    (directory / "manifest.json").unlink()
    monkeypatch.setattr(protocol, "_fault", lambda at: None)
    with pytest.raises(RuntimeError, match="manifest missing"):
        recover(pool)
    assert pending(pool)
    with pytest.raises(DataPoolError, match="recover"):
        DataPool(pool).read_research_daily(symbols="000001.SZ")


def test_direct_helpers_finish_under_existing_write_lock_without_self_deadlock(pool):
    code = f"""
from pathlib import Path
from datetime import date, datetime
import pandas as pd
from aspool.pool import pool_lock
from aspool.daily_storage import merge_daily
from aspool.free_stockdb import _write_daily
from aspool.fundamentals import _write, _snapshot_rows
from aspool.index_pool import save_index
from aspool.baostock_source import _publish_calendar
root=Path({str(pool)!r})
row=dict(symbol='000001',trade_date=date(2026,5,4),open=10.,high=10.,low=10.,close=10.,
         volume=100.,amount=1234.,pre_close=10.,is_st=False)
with pool_lock(root, write=True):
    merge_daily(root,'SZ','000001',[row],'test')
    _write_daily(root,'SZ','000001',[row])
    _write(root,_snapshot_rows(pd.DataFrame([dict(code='000001',total_shares=3.)]),datetime.now()))
    save_index(root,dict(market='SH',code='000001',name='上证'),
               [dict(row,date=row['trade_date'],vol=100.,up_count=5,down_count=6)],'offline')
    _publish_calendar(root,pd.DataFrame([dict(date=date(2026,5,4),is_open=True,source='test')]))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).parents[2],
        timeout=15,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_direct_coverage_noop_and_metadata_repair_are_not_business_changes(pool):
    from aspool.store import record_coverage, record_coverages

    before = counts(pool)
    record_coverage(pool, "000001", "SZ", DAY, DAY, 1, "test")
    with catalog(pool) as conn:
        coverage = conn.execute("SELECT * FROM coverage").fetchall()
        metadata = conn.execute("SELECT count(*) FROM coverage_changes").fetchone()[0]
    record_coverages(pool, [("000001", "SZ", DAY, DAY, 1, "test", "daily")])
    with catalog(pool) as conn:
        assert conn.execute("SELECT * FROM coverage").fetchall() == coverage
        assert conn.execute("SELECT count(*) FROM coverage_changes").fetchone()[0] == metadata
    assert counts(pool) == before


def test_quote_after_catalog_commit_failure_reports_actual_changed_rows(pool, monkeypatch):
    import aspool.change_protocol as protocol

    def fail(at):
        if at == "after_catalog_commit":
            raise OSError("committed data, failed final observation")

    monkeypatch.setattr(protocol, "_fault", fail)
    result = _publish_quote_rows(pool, [("000001", bar(1234), snapshot()[0])])
    assert result[0] == 0 and result[1] == 1
    assert result[3][0]["changed_rows"] == 1
    assert "committed data" in result[3][0]["error"]


def test_direct_writer_duplicate_rejected_before_business_mutation(pool):
    from aspool.free_stockdb import _write_daily

    before = state(pool), counts(pool)
    with pytest.raises(ValueError, match="duplicate"):
        _write_daily(pool, "SZ", "000001", [bar(), bar()])
    assert (state(pool), counts(pool)) == before
