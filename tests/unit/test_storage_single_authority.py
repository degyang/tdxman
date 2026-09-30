"""Physical retirement, public contracts, approved writers and crash recovery."""

import json
import shutil
import sqlite3
import subprocess
import sys

import duckdb
import pandas as pd
import pytest

from aspool import DataPool
from aspool import sqlite_publication as publication
from aspool.api_contract import DataPoolError
from aspool.platform_v2 import (
    activate_platform_v2,
    layout_version,
    prepare_platform_v2,
    verify_platform_v2,
)
from aspool.sqlite_daily_update import apply_daily_changes
from aspool.sqlite_publication import pending_path, recover_publication
from aspool.sqlite_stock_store import stock_connection
from aspool.storage_migration import CORE_FILES, consolidate_storage, file_digest, restore_migration
from tests.unit.test_platform_v2 import seed


def backup(root, destination):
    destination.mkdir(parents=True)
    entries = []
    for name in CORE_FILES:
        path = root / name
        if name.endswith(".sqlite"):
            with sqlite3.connect(path) as conn:
                assert conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
        shutil.copy2(path, destination / name)
        entries.append({"name": name, "bytes": path.stat().st_size, "sha256": file_digest(path)})
    (destination.parent / "manifest.json").write_text(
        json.dumps({"status": "verified", "files": entries})
    )
    return destination


@pytest.fixture
def canonical(tmp_path):
    root = tmp_path / "pool"
    seed(root)
    with sqlite3.connect(root / "stocks.sqlite") as conn:
        conn.execute(
            "UPDATE corporate_actions SET payload_json=?, "
            "factor_basis='source_cumulative_factor:source_anchor:test' WHERE record_kind='factor'",
            ('{"preserved_proof":"source"}',),
        )
    prepare_platform_v2(root)
    activate_platform_v2(root)
    recovery = backup(root, tmp_path / "recovery" / "snapshot")
    consolidate_storage(root, recovery=recovery)
    return root


def update(root, *, close=10.5):
    with stock_connection(root, read_only=False) as conn:
        return apply_daily_changes(
            conn,
            bars=[{"symbol": "000001.SZ", "trade_date": "2026-09-28", "close": close}],
            market_sessions=["2026-09-27", "2026-09-28"],
        )


def test_migration_retires_duplicates_and_preserves_public_contracts(tmp_path):
    root = tmp_path / "pool"
    seed(root)
    prepare_platform_v2(root)
    activate_platform_v2(root)
    pool = DataPool(root)
    before = {
        "raw": pool.read_daily(symbols=["000001.SZ"], start="2026-09-27", end="2026-09-28"),
        "qfq": pool.read_daily(
            symbols=["000001.SZ"], start="2026-09-27", end="2026-09-28", adjust="qfq"
        ),
        "features": pool.read_security_daily(start="2026-09-27", end="2026-09-28"),
        "summary": pool.read_market_summary(start="2026-09-28", end="2026-09-28"),
    }
    recovery = backup(root, tmp_path / "recovery" / "snapshot")
    result = consolidate_storage(root, recovery=recovery)
    assert result["verification"]["ready"] and layout_version(root) == 3
    after = {
        "raw": pool.read_daily(symbols=["000001.SZ"], start="2026-09-27", end="2026-09-28"),
        "qfq": pool.read_daily(
            symbols=["000001.SZ"], start="2026-09-27", end="2026-09-28", adjust="qfq"
        ),
        "features": pool.read_security_daily(start="2026-09-27", end="2026-09-28"),
        "summary": pool.read_market_summary(start="2026-09-28", end="2026-09-28"),
    }
    for name in before:
        pd.testing.assert_frame_equal(before[name], after[name])
        assert before[name].attrs == after[name].attrs
    with sqlite3.connect(root / "stocks.sqlite") as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not {"daily_features", "market_daily_summary"} & tables
        assert conn.execute("SELECT DISTINCT record_kind FROM corporate_actions").fetchall() == []
    with sqlite3.connect(root / "etfs.sqlite") as conn:
        assert not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='adjustment_factors'"
        ).fetchone()


def test_approved_writer_updates_the_only_feature_store_and_is_idempotent(canonical):
    result = update(canonical)
    assert result["changed_rows"] > 0
    assert verify_platform_v2(canonical)["ready"]
    with stock_connection(canonical) as conn:
        assert (
            conn.execute("SELECT close FROM daily_bars WHERE trade_date='2026-09-28'").fetchone()[0]
            == 10.5
        )
        assert (
            conn.execute(
                "SELECT calc_status FROM daily_features WHERE trade_date='2026-09-28'"
            ).fetchone()[0]
            == "TRADED"
        )
        before = conn.execute("SELECT * FROM features.feature_state ORDER BY dataset").fetchall()
        factor = conn.execute(
            "SELECT payload_json FROM corporate_actions WHERE record_kind='factor' LIMIT 1"
        ).fetchone()[0]
        assert json.loads(factor)["preserved_proof"] == "source"
    repeat = update(canonical)
    assert repeat["changed_rows"] == repeat["changed_feature_rows"] == repeat["summary_rows"] == 0
    with stock_connection(canonical) as conn:
        assert (
            conn.execute("SELECT * FROM features.feature_state ORDER BY dataset").fetchall()
            == before
        )
    assert not pending_path(canonical).exists()


def test_unapproved_maintenance_write_is_rejected_before_mutation(canonical):
    with stock_connection(canonical, read_only=False) as conn:
        with pytest.raises(DataPoolError, match="canonical daily or summary writer"):
            conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(DataPoolError, match="explicit write transaction"):
            conn.execute("UPDATE daily_features SET ma20=99")
    assert verify_platform_v2(canonical)["ready"]


def test_factor_coverage_proof_and_new_bar_publish_to_single_stores(canonical):
    with stock_connection(canonical, read_only=False) as conn:
        result = apply_daily_changes(
            conn,
            bars=[
                {
                    "symbol": "000001.SZ",
                    "trade_date": "2026-09-29",
                    "open": 11,
                    "high": 11,
                    "low": 11,
                    "close": 11,
                    "volume": 100,
                    "amount": 1100,
                }
            ],
            dated_facts=[
                {
                    "symbol": "000001.SZ",
                    "trade_date": "2026-09-29",
                    "source_pre_close": 11,
                    "source_pre_close_source": "tdxman:quote",
                    "source_is_st": 0,
                    "source_is_st_source": "tdxman:quote",
                    "trading_status": "TRADED",
                    "trading_status_source": "tdxman:quote",
                }
            ],
            factor_extensions=[
                {
                    "symbol": "000001.SZ",
                    "verified_start": "2026-09-29",
                    "verified_end": "2026-09-29",
                    "events": [],
                    "source": "tdx:xdxr",
                }
            ],
            market_sessions=["2026-09-27", "2026-09-28", "2026-09-29"],
            listed_days={"2026-09-29": 100},
        )
    assert result["changed_factor_rows"] > 0 and result["changed_feature_rows"] > 0
    with stock_connection(canonical) as conn:
        row = conn.execute(
            "SELECT valid_through,payload_json FROM corporate_actions "
            "WHERE record_kind='factor' ORDER BY effective_date DESC LIMIT 1"
        ).fetchone()
        assert row[0] == "2026-09-29"
        payload = json.loads(row[1])
        assert payload["preserved_proof"] == "source"
        assert payload["fw03_advance"]["verified_end"] == "2026-09-29"
        assert conn.native.execute("SELECT count(*) FROM main.corporate_actions").fetchone()[0] == 0
    assert verify_platform_v2(canonical)["ready"]


@pytest.mark.parametrize("phase", ["after_intent", "after_sqlite_commit", "after_verification"])
def test_failed_publication_blocks_reads_and_next_writes_until_recovery(
    canonical, monkeypatch, phase
):
    def fail(observed):
        if observed == phase:
            raise RuntimeError("injected publication interruption")

    monkeypatch.setattr(publication, "_fault", fail)
    with pytest.raises(RuntimeError, match="injected"):
        update(canonical)
    assert pending_path(canonical).exists()
    with pytest.raises(DataPoolError) as error:
        DataPool(canonical).read_daily(symbols=["000001.SZ"], start="2026-09-28")
    assert error.value.code == "RECOVERY_REQUIRED"
    with pytest.raises(DataPoolError) as error:
        update(canonical)
    assert error.value.code == "RECOVERY_REQUIRED"
    monkeypatch.setattr(publication, "_fault", lambda phase: None)
    assert recover_publication(canonical)["recovered"]
    assert not recover_publication(canonical)["recovered"]
    assert verify_platform_v2(canonical)["ready"]
    assert (
        DataPool(canonical)
        .read_daily(symbols=["000001.SZ"], start="2026-09-28", fields=["close"])
        .iloc[0, 0]
        == 10.5
    )


@pytest.mark.parametrize("phase", ["after_intent", "after_sqlite_commit"])
def test_actual_process_exit_is_recoverable(canonical, phase):
    code = """
import os,sys
from aspool import sqlite_publication as publication
from aspool.sqlite_daily_update import apply_daily_changes
from aspool.sqlite_stock_store import stock_connection
publication._fault=lambda observed: os._exit(91) if observed==sys.argv[2] else None
with stock_connection(sys.argv[1],read_only=False) as conn:
    apply_daily_changes(conn,bars=[{'symbol':'000001.SZ','trade_date':'2026-09-28','close':10.5}],market_sessions=['2026-09-27','2026-09-28'])
"""
    child = subprocess.run([sys.executable, "-c", code, str(canonical), phase], capture_output=True)
    assert child.returncode == 91, child.stderr.decode()
    assert pending_path(canonical).exists()
    assert recover_publication(canonical)["recovered"]
    assert verify_platform_v2(canonical, deep=True)["ready"]


def test_recovery_interrupted_between_files_is_idempotent(canonical, monkeypatch):
    def fail(observed):
        if observed in {"after_intent", "after_recovery_main"}:
            raise RuntimeError("injected")

    monkeypatch.setattr(publication, "_fault", fail)
    with pytest.raises(RuntimeError):
        update(canonical)
    with pytest.raises(RuntimeError):
        recover_publication(canonical)
    assert pending_path(canonical).exists()
    monkeypatch.setattr(publication, "_fault", lambda phase: None)
    assert recover_publication(canonical)["recovered"]
    assert verify_platform_v2(canonical)["ready"]


def test_corrupt_intent_does_not_execute_recovery_writes(canonical, monkeypatch):
    monkeypatch.setattr(
        publication,
        "_fault",
        lambda phase: (_ for _ in ()).throw(RuntimeError()) if phase == "after_intent" else None,
    )
    with pytest.raises(RuntimeError):
        update(canonical)
    path = pending_path(canonical)
    value = json.loads(path.read_text())
    value["root"] = "/wrong/root"
    path.write_text(json.dumps(value))
    with pytest.raises(DataPoolError) as error:
        recover_publication(canonical)
    assert error.value.code == "RECOVERY_INVALID"
    with sqlite3.connect(canonical / "stocks.sqlite") as conn:
        assert (
            conn.execute("SELECT close FROM daily_bars WHERE trade_date='2026-09-28'").fetchone()[0]
            == 11
        )


def test_catalog_access_error_never_selects_old_layout(canonical, monkeypatch):
    import aspool.platform_v2 as platform

    def unavailable(*args, **kwargs):
        raise duckdb.Error("injected catalog error")

    monkeypatch.setattr(platform.duckdb, "connect", unavailable)
    with pytest.raises(DataPoolError) as error:
        layout_version(canonical)
    assert error.value.code == "LAYOUT_UNAVAILABLE"


def test_retired_or_missing_layout_metadata_is_rejected(canonical):
    with duckdb.connect(str(canonical / "catalog.duckdb")) as conn:
        conn.execute("UPDATE pool_metadata SET value='1' WHERE key='layout_version'")
    with pytest.raises(DataPoolError) as error:
        layout_version(canonical)
    assert error.value.code == "LAYOUT_INVALID"


def test_reader_keeps_previous_coherent_snapshot_while_writer_publishes(canonical):
    pool = DataPool(canonical)
    with pool.stock_snapshot() as reader:
        assert (
            reader.read_daily(symbols=["000001.SZ"], start="2026-09-28", fields=["close"]).iloc[
                0, 0
            ]
            == 11
        )
        update(canonical)
        assert (
            reader.read_daily(symbols=["000001.SZ"], start="2026-09-28", fields=["close"]).iloc[
                0, 0
            ]
            == 11
        )
    assert (
        pool.read_daily(symbols=["000001.SZ"], start="2026-09-28", fields=["close"]).iloc[0, 0]
        == 10.5
    )


def test_failed_migration_restores_complete_pool_before_reads(tmp_path, monkeypatch):
    import aspool.storage_migration as migration

    root = tmp_path / "pool"
    seed(root)
    prepare_platform_v2(root)
    activate_platform_v2(root)
    recovery = backup(root, tmp_path / "recovery" / "snapshot")
    monkeypatch.setattr(migration, "verify_canonical", None, raising=False)
    import aspool.storage_verify as verifier

    monkeypatch.setattr(verifier, "verify_canonical", lambda *args, **kwargs: {"ready": False})
    with pytest.raises(ValueError, match="Canonical storage verification failed"):
        consolidate_storage(root, recovery=recovery)
    with pytest.raises(DataPoolError):
        DataPool(root).read_daily(symbols=["000001.SZ"], start="2026-09-28")
    assert restore_migration(root, recovery=recovery)["restored"]
    assert layout_version(root) == 2 and verify_platform_v2(root)["ready"]


def test_reader_rechecks_publication_after_acquiring_pool_lock(canonical, monkeypatch):
    from contextlib import contextmanager

    import aspool.pool as module

    original = module.pool_lock

    @contextmanager
    def interrupted(root, **options):
        with original(root, **options):
            pending_path(root).parent.mkdir(parents=True, exist_ok=True)
            pending_path(root).write_text("{}")
            yield

    monkeypatch.setattr(module, "pool_lock", interrupted)
    with pytest.raises(DataPoolError) as error:
        with stock_connection(canonical):
            pytest.fail("Incomplete publication was exposed")
    assert error.value.code == "RECOVERY_REQUIRED"


def test_etf_factor_cli_reads_unique_owner_with_preserved_columns(canonical):
    from click.testing import CliRunner

    from aspool.cli import cli

    result = CliRunner().invoke(
        cli, ["query", "--root", str(canonical), "--dataset", "etf-factors", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert rows and set(rows[0]) == {"symbol", "trade_date", "cumulative_factor", "source"}


def test_no_trade_state_and_summary_share_canonical_publication(canonical):
    options = dict(
        dated_facts=[
            dict(
                symbol="000001.SZ",
                trade_date="2026-09-29",
                trading_status="NO_TRADE",
                trading_status_source="tdxman:quote",
            )
        ],
        market_sessions=["2026-09-27", "2026-09-28", "2026-09-29"],
    )
    with stock_connection(canonical, read_only=False) as conn:
        result = apply_daily_changes(conn, **options)
        assert result["changed_rows"] > 0
        assert (
            conn.execute(
                "SELECT count(*) FROM daily_bars WHERE trade_date='2026-09-29'"
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT calc_status FROM daily_features WHERE trade_date='2026-09-29'"
            ).fetchone()[0]
            == "NO_TRADE"
        )
        assert apply_daily_changes(conn, **options)["changed_feature_rows"] == 0
    assert verify_platform_v2(canonical)["ready"]


def test_summary_maintenance_publishes_to_unique_store_and_replay_is_noop(canonical):
    from aspool.sqlite_summary_repair import apply_summary_changes

    with stock_connection(canonical, read_only=False) as conn:
        raw = conn.execute("SELECT * FROM daily_bars ORDER BY symbol,trade_date").fetchall()
        assert apply_summary_changes(conn, day="2026-09-28")["changed_rows"] > 0
        first = conn.execute("SELECT * FROM features.feature_state ORDER BY dataset").fetchall()
        assert apply_summary_changes(conn, day="2026-09-28")["changed_rows"] == 0
        assert conn.execute("SELECT * FROM daily_bars ORDER BY symbol,trade_date").fetchall() == raw
        assert (
            conn.execute("SELECT * FROM features.feature_state ORDER BY dataset").fetchall()
            == first
        )
    assert verify_platform_v2(canonical)["ready"]


def test_historical_event_revision_updates_single_reference_and_feature_owners(canonical):
    event = dict(
        effective_date="2026-09-28",
        category=1,
        source_key="dividend",
        cash_dividend_per_share=1,
        bonus_shares_per_share=0,
        rights_shares_per_share=0,
        rights_price=0,
    )
    options = dict(
        factor_extensions=[
            dict(
                symbol="000001.SZ",
                verified_start="2026-09-28",
                verified_end="2026-09-28",
                events=[event],
                source="tdx:xdxr",
            )
        ],
        merge_event_revisions=True,
        market_sessions=["2026-09-27", "2026-09-28"],
    )
    with stock_connection(canonical, read_only=False) as conn:
        assert apply_daily_changes(conn, **options)["changed_factor_rows"] > 0
        assert (
            conn.execute(
                "SELECT pre_close FROM daily_features WHERE trade_date='2026-09-28'"
            ).fetchone()[0]
            == 9
        )
        state = conn.execute("SELECT * FROM features.feature_state ORDER BY dataset").fetchall()
        assert apply_daily_changes(conn, **options)["changed_factor_rows"] == 0
        assert (
            conn.execute("SELECT * FROM features.feature_state ORDER BY dataset").fetchall()
            == state
        )
        event["cash_dividend_per_share"] = 2
        assert apply_daily_changes(conn, **options)["changed_factor_rows"] > 0
        assert (
            conn.execute(
                "SELECT pre_close FROM daily_features WHERE trade_date='2026-09-28'"
            ).fetchone()[0]
            == 8
        )
        assert conn.native.execute(
            "SELECT DISTINCT record_kind FROM main.corporate_actions"
        ).fetchall() == [("event",)]
    assert verify_platform_v2(canonical)["ready"]


def _hold_catalog_write_lock(root, ready, release):
    from aspool.pool import pool_lock

    with pool_lock(root, write=True), duckdb.connect(str(root / "catalog.duckdb")):
        ready.set()
        assert release.wait(10)


def test_layout_reader_waits_for_catalog_writer_in_another_process(canonical):
    import multiprocessing
    import threading

    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()

    worker = context.Process(target=_hold_catalog_write_lock, args=(canonical, ready, release))
    worker.start()
    assert ready.wait(10)
    started, finished = threading.Event(), threading.Event()
    result = []

    def reader():
        started.set()
        try:
            result.append(layout_version(canonical))
        except Exception as error:
            result.append(error)
        finally:
            finished.set()

    thread = threading.Thread(target=reader)
    thread.start()
    try:
        assert started.wait(10)
        assert not finished.wait(0.1)
    finally:
        release.set()
        thread.join(10)
        worker.join(10)
    assert worker.exitcode == 0 and result == [3]


def test_quote_calendar_writer_holds_pool_lock_for_catalog_lifetime(canonical, monkeypatch):
    from types import SimpleNamespace

    from aspool.pool import _holds_write_lock
    from aspool.sqlite_update_cli import _quote_calendar

    with duckdb.connect(str(canonical / "catalog.duckdb")) as conn:
        conn.execute(
            "CREATE TABLE security_calendar(trade_date DATE PRIMARY KEY,"
            "is_open BOOLEAN,source VARCHAR)"
        )
    original = duckdb.connect

    def guarded(path, **options):
        if not options.get("read_only", False):
            assert _holds_write_lock(canonical)
        return original(path, **options)

    monkeypatch.setattr(duckdb, "connect", guarded)
    quotes = SimpleNamespace(rows={"000001.SH": {}}, fetch=lambda symbols: None)
    _quote_calendar(canonical, quotes, "2026-09-29")
    with original(str(canonical / "catalog.duckdb"), read_only=True) as conn:
        assert conn.execute("SELECT is_open FROM security_calendar").fetchall() == [(True,)]
