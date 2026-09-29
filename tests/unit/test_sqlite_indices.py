"""Real index migration, unchanged public API, bounded sync and atomic updates."""

import asyncio
import importlib.util
import sqlite3
import struct
from datetime import date, datetime
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from click.testing import CliRunner

from aspool import DataPool, DataPoolError
from aspool.cli import cli
from aspool.index_pool import SCHEMA, index_path, normalize, sync_indices
from aspool.sqlite_index_store import index_connection, save_rows
from aspool.sqlite_index_sync import index_record, online_records

ITEM = dict(market="SH", code="881165", name="其他饰品", source=["HY"])


def bar(day="2026-09-24", close=10):
    return dict(
        date=day,
        open=10,
        high=11,
        low=9,
        close=close,
        vol=100,
        amount=1000,
        up_count=498,
        down_count=1795,
    )


def migrator():
    path = Path(__file__).parents[2] / "scripts/ops/migrate_indices_sqlite.py"
    spec = importlib.util.spec_from_file_location("migrate_indices", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.migrate


def legacy(root):
    path = index_path(root, ITEM)
    path.parent.mkdir(parents=True)
    rows = normalize([bar("2026-09-23"), bar()], ITEM)
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), path)
    with duckdb.connect(str(root / "catalog.duckdb")) as conn:
        conn.execute("CREATE TABLE fixture(value INTEGER)")
    return path


def test_migration_preserves_values_public_api_and_archives_sources(tmp_path):
    root = tmp_path / "data"
    legacy(root)
    pool = DataPool(root)
    before = pool.read_index_daily(symbols=["SH.881165"])
    report = migrator()(root, workdir=tmp_path / "recovery")
    assert report["rows"] == 2 and report["status"] == "installed"
    assert not (root / "lake/indices").exists()
    assert (tmp_path / "recovery/legacy-indices").exists()
    after = pool.read_index_daily(symbols=["881165.SH"])
    pd.testing.assert_frame_equal(before, after, check_dtype=False)
    assert before.attrs == after.attrs
    selected = pool.read_index_daily(
        symbols=["881165.SH"], end="2026-09-23", lookback=1, fields=["date", "close"]
    )
    assert selected.close.tolist() == [10]
    assert selected.date.dt.date.tolist() == [date(2026, 9, 23)]
    assert pool.read_index_daily(symbols=[]).empty
    listing = pool.list_indices()
    assert listing.row_count.tolist() == [2]
    with pytest.raises(FileExistsError):
        migrator()(root, workdir=tmp_path / "second")


def test_bad_migration_never_switches_or_moves_legacy(tmp_path):
    root = tmp_path / "data"
    path = legacy(root)
    table = pq.ParquetFile(path).read()
    pq.write_table(pa.concat_tables([table, table]), path)
    with pytest.raises(ValueError, match="duplicated"):
        migrator()(root, workdir=tmp_path / "failed")
    assert path.exists() and not (root / "indices.sqlite").exists()


def test_verified_install_can_resume_failed_archive(tmp_path, monkeypatch):
    root = tmp_path / "data"
    path = legacy(root)
    workdir = tmp_path / "recovery"
    migrate = migrator()
    replace = Path.replace

    def deny_archive(self, target):
        if self == root / "lake/indices":
            raise PermissionError("File handle still open")
        return replace(self, target)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", deny_archive)
        with pytest.raises(PermissionError):
            migrate(root, workdir=workdir)
    assert path.exists() and (root / "indices.sqlite").exists()
    finish_archive = migrate.__globals__["finish_archive"]
    report = finish_archive(root, workdir=workdir)
    assert report["status"] == "installed"
    assert report["archive_retry_error"] == "File handle still open"
    assert not path.exists()
    assert (workdir / "legacy-indices").is_dir()


def test_writer_noop_revisions_and_failure_rollback(tmp_path):
    root = tmp_path / "data"
    legacy(root)
    migrator()(root, workdir=tmp_path / "recovery")
    with index_connection(root, read_only=False) as conn:
        before = list(conn.iterdump())
        assert save_rows(conn, ITEM, [bar()], source="tdx")["unchanged"] == 1
        assert list(conn.iterdump()) == before
        conn.execute(
            "CREATE TEMP TRIGGER fail_new BEFORE INSERT ON daily_bars "
            "BEGIN SELECT RAISE(ABORT, 'forced failure'); END"
        )
        with pytest.raises(sqlite3.IntegrityError, match="forced failure"):
            save_rows(conn, ITEM, [bar(close=11), bar("2026-09-28")], source="tdx")
        assert list(conn.iterdump()) == before
        conn.execute("DROP TRIGGER fail_new")
        stats = save_rows(conn, ITEM, [bar(close=11), bar("2026-09-28")], source="tdx")
        assert (stats["added"], stats["changed"]) == (1, 1)
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT close FROM daily_bars "
            "WHERE symbol=? AND trade_date>=? ORDER BY trade_date DESC LIMIT 5",
            ("881165.SH", "2026-09-23"),
        ).fetchall()
        assert "SEARCH" in str(plan) and "PRIMARY KEY" in str(plan)


def mac_bar(day="2026-09-28"):
    return dict(
        datetime=datetime.fromisoformat(day),
        open=10,
        high=11,
        low=9,
        close=10,
        vol=100,
        amount=1000,
        float_shares=struct.unpack("<f", struct.pack("<HH", 498, 1795))[0],
    )


def test_mac_index_breadth_and_bounded_pages():
    assert (index_record(mac_bar())["up_count"], index_record(mac_bar())["down_count"]) == (
        498,
        1795,
    )

    class Client:
        def get_stock_kline(self, *args, **kwargs):
            assert kwargs["count"] == 30
            return pd.DataFrame([mac_bar("2026-09-23"), mac_bar("2026-09-28")])

    rows = asyncio.run(online_records(Client(), ITEM, "2026-09-24"))
    assert [r["date"] for r in rows] == ["2026-09-28"]


def test_existing_cli_routes_sqlite_retry_and_keeps_data_clean(tmp_path, monkeypatch):
    from tdxman.mac.client import MacClient

    root = tmp_path / "data"
    legacy(root)
    migrator()(root, workdir=tmp_path / "recovery")
    from aspool.securities import publish_index_directory

    publish_index_directory(root, [{**ITEM, "source": ["HY2"]}])
    calls = []

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get_stock_kline(self, *args, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise OSError("read failed")
            return pd.DataFrame([mac_bar("2026-09-23"), mac_bar()])

    monkeypatch.setattr(MacClient, "from_best_host", lambda **kwargs: Client())
    monkeypatch.setattr("aspool.universe.refresh_index_universe", lambda _: {})
    result = CliRunner().invoke(
        cli,
        ["sync", "--type", "index", "--root", str(root), "--retry-delay", "0", "--workers", "1"],
    )
    assert result.exit_code == 0, result.output
    assert len(calls) == 2
    assert DataPool(root).read_index_daily(lookback=1).date.dt.date.tolist() == [date(2026, 9, 28)]
    assert not (root / "reports").exists() and not (root / "change-state").exists()
    again, path = sync_indices(root, items=[ITEM], retry_delay=0)
    assert again["success"][0]["added"] == again["success"][0]["changed"] == 0
    assert root not in path.parents


def test_corrupt_sqlite_does_not_silently_fall_back(tmp_path):
    root = tmp_path / "data"
    legacy(root)
    (root / "indices.sqlite").write_bytes(b"broken")
    with pytest.raises(DataPoolError, match="not a database") as error:
        DataPool(root).read_index_daily()
    assert error.value.code == "INDEX_INVALID"


def test_async_index_sync_and_stale_source_reporting(tmp_path, monkeypatch):
    from tdxman.mac.client import AsyncMacClient

    root = tmp_path / "data"
    legacy(root)
    migrator()(root, workdir=tmp_path / "recovery")
    with duckdb.connect(str(root / "catalog.duckdb")) as conn:
        conn.execute("CREATE TABLE security_calendar(trade_date DATE, is_open BOOLEAN)")
        conn.execute("INSERT INTO security_calendar VALUES ('2026-09-28',true)")

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get_stock_kline(self, *args, **kwargs):
            return pd.DataFrame([mac_bar("2026-09-23"), mac_bar("2026-09-24")])

    monkeypatch.setattr(AsyncMacClient, "from_best_host", lambda **kwargs: Client())
    report, _ = sync_indices(root, items=[ITEM], asynchronous=True, retry_delay=0)
    assert report["status"] == "partial" and report["stale"][0]["source_end"] == "2026-09-24"
    assert not report["failed"]
