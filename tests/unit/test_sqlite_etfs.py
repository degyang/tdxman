"""ETF migration, public compatibility, hot writes, and command routing."""

import importlib.util
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from click.testing import CliRunner

from aspool import DataPool, DataPoolError
from aspool.cli import cli
from aspool.sqlite_etf_store import connection, save_rows

ITEM = dict(market="SH", code="510010", name="TestETF", source=["ETF"])


def migrator():
    spec = importlib.util.spec_from_file_location(
        "etf_migrate", Path(__file__).parents[2] / "scripts/ops/migrate_etfs_sqlite.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.migrate


def legacy(root):
    path = root / "lake/bars/daily/market=SH/symbol=510010/bars.parquet"
    path.parent.mkdir(parents=True)
    rows = [
        dict(
            symbol="510010",
            asset_type="etf",
            name="TestETF",
            trade_date=date(2026, 9, 14) + timedelta(days=i),
            open=10.0,
            high=11.0,
            low=9.0,
            close=10.0,
            volume=100.0,
            amount=1000.0,
            float_shares=10.0,
        )
        for i in range(11)
    ]
    pq.write_table(pa.Table.from_pylist(rows), path)
    with duckdb.connect(str(root / "catalog.duckdb")) as c:
        c.execute("CREATE TABLE fixture(value INTEGER)")
    return path


def test_migration_and_public_contract(tmp_path):
    root = tmp_path / "data"
    legacy(root)
    pool = DataPool(root)
    before = pool.read_etf_daily()
    result = migrator()(root, workdir=tmp_path / "recovery")
    assert result["rows"] == 11 and result["status"] == "installed"
    assert not (root / "lake/bars/daily").exists()
    after = pool.read_etf_daily()
    pd.testing.assert_frame_equal(before, after, check_dtype=False)
    assert before.attrs == after.attrs
    assert pool.list_etfs().row_count.tolist() == [11]
    assert pool.read_etf_daily(
        symbols="SH.510010", lookback=2, fields=["date", "close"]
    ).date.dt.date.tolist() == [date(2026, 9, 23), date(2026, 9, 24)]
    assert pool.read_etf_daily(symbols=[]).empty
    with pytest.raises(FileExistsError):
        migrator()(root, workdir=tmp_path / "again")

    response = CliRunner().invoke(
        cli,
        [
            "query",
            "SH510010",
            "--dataset",
            "etf",
            "--start",
            "2026-09-24",
            "--root",
            str(root),
            "--format",
            "json",
        ],
    )
    assert response.exit_code == 0, response.output
    assert json.loads(response.output)[0]["symbol"] == "510010.SH"


def test_migration_rejects_non_etf_without_switch(tmp_path):
    root = tmp_path / "data"
    path = legacy(root)
    with pq.ParquetFile(path) as p:
        rows = p.read().to_pylist()
    rows[-1]["asset_type"] = "stock"
    pq.write_table(pa.Table.from_pylist(rows), path)
    with pytest.raises(ValueError, match="Non-ETF"):
        migrator()(root, workdir=tmp_path / "recovery")
    assert path.exists() and not (root / "etfs.sqlite").exists()


def test_hot_upsert_noop_metrics_and_atomic_rollback(tmp_path):
    root = tmp_path / "data"
    legacy(root)
    migrator()(root, workdir=tmp_path / "recovery")
    with connection(root, read_only=False) as conn:
        row = dict(
            trade_date="2026-09-28",
            open=10.0,
            high=11.0,
            low=9.0,
            close=10.0,
            volume=200.0,
            amount=2000.0,
            float_shares=10.0,
        )
        assert save_rows(conn, ITEM, [row], source="tdx")["added"] == 1
        value = conn.execute(
            "SELECT vol_ratio,turnover_rate FROM daily_bars WHERE trade_date='2026-09-28'"
        ).fetchone()
        assert tuple(value) == (2.0, 0.2)
        before = list(conn.iterdump())
        assert save_rows(conn, ITEM, [row], source="tdx")["changed"] == 0
        assert list(conn.iterdump()) == before
        conn.execute(
            "CREATE TEMP TRIGGER fail_insert BEFORE INSERT ON daily_bars "
            "BEGIN SELECT RAISE(ABORT,'forced'); END"
        )
        with pytest.raises(sqlite3.IntegrityError, match="forced"):
            save_rows(
                conn,
                ITEM,
                [dict(row, close=11.0), dict(row, trade_date="2026-09-29")],
                source="tdx",
            )
        assert list(conn.iterdump()) == before
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT close FROM daily_bars WHERE symbol=? AND trade_date<=? "
            "ORDER BY trade_date DESC LIMIT 5",
            ("510010.SH", "2026-09-28"),
        ).fetchall()
        assert "PRIMARY KEY" in str([tuple(r) for r in plan])


def test_existing_cli_uses_sqlite_retries_and_never_recreates_lake(tmp_path, monkeypatch):
    import aspool.sqlite_etf_sync as sync

    root = tmp_path / "data"
    legacy(root)
    migrator()(root, workdir=tmp_path / "recovery")
    monkeypatch.setattr(sync, "online_items", lambda **kwargs: [ITEM])

    class Client:
        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(sync.MacClient, "from_best_host", lambda **kw: Client())
    attempts = []

    def fetch(client, job):
        attempts.append(job)
        if len(attempts) == 1:
            raise OSError("retry me")
        return [
            dict(
                trade_date=date(2026, 9, 28),
                open=10.0,
                high=11.0,
                low=9.0,
                close=10.0,
                volume=200.0,
                amount=2000.0,
            )
        ]

    monkeypatch.setattr(sync, "_stock_records", fetch)
    result = CliRunner().invoke(
        cli,
        [
            "sync",
            "--type",
            "ex",
            "--category",
            "ETF",
            "--root",
            str(root),
            "--workers",
            "1",
            "--start",
            "2026-09-01",
            "--end",
            "2026-09-29",
            "--retry-delay",
            "0",
        ],
    )
    assert result.exit_code == 0, result.output
    assert len(attempts) == 2
    assert not (root / "lake/bars/daily").exists()
    assert not (root / "reports").exists()
    assert DataPool(root).read_etf_daily(lookback=1).date.dt.date.tolist() == [date(2026, 9, 28)]


def test_corrupt_sqlite_is_not_hidden_by_legacy_fallback(tmp_path):
    legacy(tmp_path)
    (tmp_path / "etfs.sqlite").write_bytes(b"broken")
    with pytest.raises(DataPoolError) as error:
        DataPool(tmp_path).read_etf_daily()
    assert error.value.code == "ETF_INVALID"


def test_no_trade_requires_matching_identity_date_and_zero_activity():
    from aspool.source_retry import EmptySourceResponse
    from aspool.sqlite_etf_sync import no_trade_quote

    raw = dict(
        code="510010",
        market=1,
        server_update_date=20260928,
        open=0.0,
        high=0.0,
        low=0.0,
        vol=0,
        amount=0.0,
    )
    assert no_trade_quote(pd.DataFrame([raw]), ITEM, "2026-09-28")["status"] == "NO_TRADE"
    for altered in (
        dict(raw, server_update_date=20260924),
        dict(raw, vol=1),
        dict(raw, code="510020"),
    ):
        with pytest.raises(EmptySourceResponse):
            no_trade_quote(pd.DataFrame([altered]), ITEM, "2026-09-28")


def test_async_no_trade_keeps_history_without_inventing_bar(tmp_path, monkeypatch):
    import aspool.sqlite_etf_sync as sync

    root = tmp_path / "data"
    legacy(root)
    migrator()(root, workdir=tmp_path / "recovery")
    with duckdb.connect(str(root / "catalog.duckdb")) as c:
        c.execute("CREATE TABLE security_calendar(trade_date DATE,is_open BOOLEAN)")
        c.execute("INSERT INTO security_calendar VALUES ('2026-09-28',true)")

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get_stock_quotes(self, *args):
            return pd.DataFrame(
                [
                    dict(
                        code="510010",
                        market=1,
                        server_update_date=20260928,
                        open=0.0,
                        high=0.0,
                        low=0.0,
                        vol=0,
                        amount=0.0,
                    )
                ]
            )

    async def empty(client, job):
        return []

    monkeypatch.setattr(sync, "_stock_records_async", empty)
    monkeypatch.setattr(sync, "client_factory", lambda *args: (Client, None))
    report, _ = sync.sync_etfs(root, asynchronous=True, items=[ITEM], workers=1)
    assert report["status"] == "ok" and len(report["no_trade"]) == 1
    assert not report["success"] and not report["failed"]
    assert DataPool(root).list_etfs().row_count.tolist() == [11]
