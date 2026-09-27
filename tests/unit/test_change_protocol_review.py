"""Independent DG-01 recovery and scope regressions."""

import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from aspool import DataPool
from aspool.api_contract import DataPoolError
from aspool.change_protocol import pending, recover
from aspool.daily_storage import merge_daily
from aspool.pool import pool_lock
from aspool.store import catalog, daily_path, initialize
from aspool.universe import _publish_universe
from tests.unit.test_change_protocol import DAY, bar, counts, state
from tests.unit.test_change_protocol import pool as pool


def stop_at(root, monkeypatch, phase):
    import aspool.change_protocol as protocol

    def fail(at):
        if at == phase:
            raise OSError("review fault")

    monkeypatch.setattr(protocol, "_fault", fail)
    with pytest.raises(OSError, match="review fault"):
        merge_daily(root, "SZ", "000001", [bar(2345)], "test")
    monkeypatch.setattr(protocol, "_fault", lambda at: None)
    return next((root / "change-state/pending").iterdir())


def test_revision_conflict_rejected_before_target_replacement(pool, monkeypatch):
    target = daily_path(pool, "SZ", "000001")
    original = target.read_bytes(), target.stat().st_mtime_ns
    stop_at(pool, monkeypatch, "after_prepared")
    with catalog(pool) as conn:
        conn.execute(
            "UPDATE business_revisions SET revision=revision+1 WHERE object_key=?",
            [str(target.relative_to(pool))],
        )
    before = state(pool), counts(pool)
    with pool_lock(pool, write=True), pytest.raises(RuntimeError, match="revision"):
        recover(pool)
    assert (target.read_bytes(), target.stat().st_mtime_ns) == original
    assert (state(pool), counts(pool)) == before
    assert pending(pool)


@pytest.mark.parametrize("phase", ["after_prepared", "after_catalog_commit"])
def test_manifest_corruption_blocks_without_losing_stale_contract(pool, monkeypatch, phase):
    directory = stop_at(pool, monkeypatch, phase)
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["stale_start"] = None
    path.write_text(json.dumps(manifest))
    before = state(pool), counts(pool), daily_path(pool, "SZ", "000001").read_bytes()
    with pool_lock(pool, write=True), pytest.raises(RuntimeError, match="manifest"):
        recover(pool)
    assert (state(pool), counts(pool), daily_path(pool, "SZ", "000001").read_bytes()) == before
    with pytest.raises(DataPoolError) as error:
        DataPool(pool).read_research_daily(symbols="000001.SZ")
    assert error.value.code == "RECOVERY_REQUIRED"


def test_applied_recovery_validates_catalog_revision(pool, monkeypatch):
    stop_at(pool, monkeypatch, "after_catalog_commit")
    with catalog(pool) as conn:
        conn.execute("UPDATE business_revisions SET revision=99")
    with pool_lock(pool, write=True), pytest.raises(RuntimeError, match="revision"):
        recover(pool)
    assert pending(pool)


def test_first_pool_creation_fsyncs_every_new_parent(tmp_path, monkeypatch):
    import aspool.change_protocol as protocol

    synced = []
    real_sync = protocol._sync_directory

    def sync(path):
        synced.append(path)
        real_sync(path)

    monkeypatch.setattr(protocol, "_sync_directory", sync)
    root = tmp_path / "new-parent" / "pool"
    with pool_lock(root, write=True):
        initialize(root)
        merge_daily(root, "SZ", "000001", [bar()], "test")
    directories = {p for p in root.rglob("*") if p.is_dir()} | {root, root.parent}
    assert {p.parent for p in directories} <= set(synced)


def test_stock_reclassification_invalidates_previous_name_scope(pool):
    _publish_universe(pool, [dict(symbol="000001", market="SZ", name="甲", asset_type="etf")])
    assert [row[0] for row in state(pool)["stale"]] == [DAY]


def test_only_changed_etf_in_mixed_directory_does_not_invalidate_stock(pool):
    _publish_universe(pool, [dict(symbol="159915", market="SZ", name="ETF", asset_type="etf")])
    _publish_universe(
        pool,
        [
            dict(symbol="000001", market="SZ", name="甲"),
            dict(symbol="159915", market="SZ", name="ETF2", asset_type="etf"),
        ],
    )
    assert state(pool)["stale"] == []


def test_observation_utc_independent_of_catalog_session_timezone(pool):
    from aspool.change_protocol import observe

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with catalog(pool) as conn:
        conn.execute("SET TimeZone='Asia/Shanghai'")
        observe(conn, "lifecycle:000001.SZ")
        observed = conn.execute(
            "SELECT observed_at FROM fetch_observations WHERE object_key='lifecycle:000001.SZ'"
        ).fetchone()[0]
    assert abs((observed - now).total_seconds()) < 5


def test_universe_observation_ttl_uses_utc_not_business_last_seen(pool):
    from aspool.universe import universe_is_stale

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with catalog(pool) as conn:
        conn.execute("UPDATE universe SET last_seen=?", [now - timedelta(days=30)])
        conn.execute(
            "UPDATE fetch_observations SET observed_at=? WHERE object_key='universe:stock'",
            [now - timedelta(days=7) + timedelta(hours=1)],
        )
    assert not universe_is_stale(pool, asset_type="stock")
    with catalog(pool) as conn:
        conn.execute(
            "UPDATE fetch_observations SET observed_at=? WHERE object_key='universe:stock'",
            [now - timedelta(days=7) - timedelta(hours=1)],
        )
    assert universe_is_stale(pool, asset_type="stock")


@pytest.mark.parametrize("empty", [False, True])
def test_enrichment_source_outage_preserves_capital_facts_and_skips_compute(
    pool, monkeypatch, empty
):
    from aspool.enrichment import enrich_daily

    merge_daily(
        pool,
        "SZ",
        "000001",
        [dict(bar(), float_share=1000.0, float_share_source="tdx:xdxr_capital", float_mv=10000.0)],
        "test",
    )
    with catalog(pool) as conn:
        conn.execute("DELETE FROM daily_limit_staleness")
        conn.execute("INSERT INTO security_calendar VALUES (?, true, 'test')", [DAY])
    target = daily_path(pool, "SZ", "000001")
    before = state(pool), counts(pool), target.read_bytes(), target.stat().st_mtime_ns
    monkeypatch.setattr("aspool.enrichment.client_factory", lambda *args: (None, None))
    monkeypatch.setattr(
        "aspool.enrichment.fetch_sync",
        lambda *args: [
            (("000001", "SZ"), {} if empty else None, None if empty else OSError("source down"), 0)
        ],
    )

    def forbidden(*args, **kwargs):
        pytest.fail("Failed source fetch must not trigger automatic publication")

    monkeypatch.setattr("aspool.limit_events.compute_limit_events", forbidden)
    report, _ = enrich_daily(pool, start=DAY, end=DAY, compare_baostock=False)
    assert report["status"] == "partial" and report["source_failures"]
    assert (state(pool), counts(pool), target.read_bytes(), target.stat().st_mtime_ns) == before


def test_index_source_outage_is_a_failed_maintenance_observation(pool, monkeypatch):
    from aspool.index_pool import sync_indices
    from tests.unit.test_change_protocol import ITEM

    def fail(*args):
        raise OSError("source down")

    monkeypatch.setattr("aspool.index_pool.offline_records", fail)
    report, _ = sync_indices(pool, mode="offline", items=[ITEM])
    assert report["failed"]
    with catalog(pool) as conn:
        metrics = json.loads(
            conn.execute(
                "SELECT metrics FROM maintenance_runs WHERE run_id=?", [report["change_run_id"]]
            ).fetchone()[0]
        )
    assert metrics["state"] == "partial_failure"
    assert metrics["changed_row_events"] == 0


def test_concurrent_reader_waits_for_writer_then_reports_pending(pool, monkeypatch):
    ready = pool.parent / (pool.name + "-reader-ready")
    code = f"""
from pathlib import Path
from aspool import DataPool
from aspool.api_contract import DataPoolError
Path({str(ready)!r}).write_text('ready')
try:
    DataPool(Path({str(pool)!r})).read_research_daily(symbols='000001.SZ')
except DataPoolError as exc:
    print(exc.code, flush=True)
"""
    process = None
    try:
        with pool_lock(pool, write=True):
            process = subprocess.Popen(
                [sys.executable, "-c", code],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            deadline = time.monotonic() + 10
            while not ready.exists():
                assert time.monotonic() < deadline
                time.sleep(0.01)
            time.sleep(0.1)
            assert process.poll() is None
            stop_at(pool, monkeypatch, "after_replace")
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        assert stdout.strip() == "RECOVERY_REQUIRED"
        with pool_lock(pool, write=True):
            recover(pool)
        assert DataPool(pool).read_research_daily(symbols="000001.SZ").iloc[0].amount == 2345
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_public_pending_check_never_enumerates_historical_archives(pool, monkeypatch):
    real_iterdir = Path.iterdir

    def guarded(path):
        if path.name in {"applied", "unprepared"}:
            pytest.fail("Public reads must not scan historical change evidence")
        return real_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", guarded)
    assert DataPool(pool).read_research_daily(symbols="000001.SZ").iloc[0].amount == 1000
    stop_at(pool, monkeypatch, "after_replace")
    with pytest.raises(DataPoolError) as error:
        DataPool(pool).read_research_daily(symbols="000001.SZ")
    assert error.value.code == "RECOVERY_REQUIRED"


@pytest.mark.parametrize("phase", ["before_prepare_manifest", "before_pending"])
def test_real_exit_before_pending_keeps_old_facts(pool, phase):
    code = f"""
import os
import datetime
from pathlib import Path
import aspool.change_protocol as protocol
from aspool.daily_storage import merge_daily
from aspool.pool import pool_lock
root = Path({str(pool)!r})
protocol._fault = lambda at: os._exit(73) if at == {phase!r} else None
with pool_lock(root, write=True):
    merge_daily(root, 'SZ', '000001', [{bar(2345)!r}], 'test')
"""
    before = state(pool), counts(pool), daily_path(pool, "SZ", "000001").read_bytes()
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 73, result.stderr
    assert not pending(pool)
    assert DataPool(pool).read_research_daily(symbols="000001.SZ").iloc[0].amount == 1000
    with pool_lock(pool, write=True):
        recover(pool)
    assert (state(pool), counts(pool), daily_path(pool, "SZ", "000001").read_bytes()) == before
    assert not list((pool / "change-state/unprepared").rglob("new.parquet"))


@pytest.mark.parametrize("damage", ["evidence", "applied_record"])
def test_recovery_damage_can_be_repaired_and_retried_exactly_once(pool, monkeypatch, damage):
    directory = stop_at(pool, monkeypatch, "after_catalog_commit")
    before = counts(pool)
    evidence = directory / "rows.jsonl"
    original = evidence.read_bytes()
    if damage == "evidence":
        evidence.write_bytes(original + b"corrupt")
    else:
        with catalog(pool) as conn:
            conn.execute(
                "UPDATE business_changes SET changed_rows=999 WHERE operation_id=?",
                [directory.name],
            )
    with pool_lock(pool, write=True), pytest.raises(RuntimeError, match="corrupt|differs"):
        recover(pool)
    assert pending(pool)
    if damage == "evidence":
        evidence.write_bytes(original)
    else:
        with catalog(pool) as conn:
            conn.execute(
                "UPDATE business_changes SET changed_rows=1 WHERE operation_id=?", [directory.name]
            )
    with pool_lock(pool, write=True):
        recover(pool)
        assert recover(pool) == []
    assert counts(pool) == before
    assert DataPool(pool).read_research_daily(symbols="000001.SZ").iloc[0].amount == 2345


def test_enrichment_command_recovers_before_its_planning_read(pool, monkeypatch):
    from aspool.enrichment import enrich_daily

    with catalog(pool) as conn:
        conn.execute("INSERT INTO security_calendar VALUES (?, true, 'test')", [DAY])
    stop_at(pool, monkeypatch, "after_replace")
    old_count = counts(pool)[0]
    monkeypatch.setattr("aspool.enrichment.client_factory", lambda *args: (None, None))
    monkeypatch.setattr(
        "aspool.enrichment.fetch_sync",
        lambda *args: [(("000001", "SZ"), None, OSError("source down"), 0)],
    )
    report, _ = enrich_daily(pool, start=DAY, end=DAY, compare_baostock=False)
    assert report["status"] == "partial"
    assert not pending(pool) and counts(pool)[0] == old_count + 1
    assert DataPool(pool).read_research_daily(symbols="000001.SZ").iloc[0].amount == 2345
    assert [r[0] for r in state(pool)["stale"]] == [DAY]
