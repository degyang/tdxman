"""Source fidelity and isolation for the first SQLite facts migration stage."""

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from aspool.sqlite_stock_store import stock_connection

OPS = Path(__file__).parents[2] / "scripts/ops"


def load(name):
    spec = importlib.util.spec_from_file_location(name, OPS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


snapshot = load("snapshot_development_data")
migration = load("migrate_stocks_sqlite")
verifier = load("verify_stock_migration")
public_data = load("stage_development_public_data")


def test_aliases_keys_and_nonfinite():
    notes = []
    row = dict(symbol="000001", code="000001", trade_date=date(2024, 1, 2),
               volume=100.0, vol=200.0, turnover=3.0, float_share=5.0, float_shares=6.0)
    result = dict(zip(migration.BAR_COLUMNS, migration.normalize_bar(
        row, "000001.SZ", lambda *args: notes.append(args))))
    assert result["volume"] == 100
    assert result["turnover_rate"] == 3
    assert (result["float_share"], result["float_shares"]) == (5, 6)
    assert result["name_as_of"] is None
    assert notes[0][0] == "alias_precedence"
    with pytest.raises(ValueError, match="Non-finite"):
        migration.normalize_bar(dict(row, close=float("inf")), "000001.SZ", lambda *a: None)
    with pytest.raises(ValueError, match="mismatch"):
        migration.normalize_bar(dict(row, code="000002"), "000001.SZ", lambda *a: None)


def test_derived_values_not_promoted_to_source():
    row = dict(open=10.0, high=11.0, low=9.0, close=10.0, pre_close=9.0,
               pre_close_source="derived:previous_close", is_st=False,
               is_st_source="tdxman:quote_name", is_st_name_date=date(2024, 1, 2))
    result = dict(zip(migration.FEATURE_COLUMNS, migration.normalize_features(
        row, {}, "000001.SZ", "2024-01-02", lambda *a: None)))
    assert result["source_pre_close"] is None
    assert result["source_is_st"] is None
    assert result["pre_close"] is None
    assert result["calc_status"] == "TRADED"
    assert result["limit_status"] is None


def test_full_source_migration_and_resume(tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    with duckdb.connect(str(old / "catalog.duckdb")) as c:
        c.execute("CREATE TABLE universe(symbol VARCHAR, market VARCHAR, asset_type VARCHAR)")
        c.execute("INSERT INTO universe VALUES ('000001','SZ','stock'),('159915','SZ','etf')")
        c.execute("INSERT INTO universe VALUES ('920001','BJ','stock')")
        c.execute("CREATE TABLE security_lifecycle(symbol VARCHAR)")
        c.execute("CREATE TABLE security_calendar(trade_date DATE,is_open BOOLEAN)")
        c.execute("INSERT INTO security_calendar VALUES ('2024-01-02',true)")
        c.execute("CREATE TABLE index_coverage(market VARCHAR,code VARCHAR)")
        c.execute("CREATE TABLE coverage(symbol VARCHAR,market VARCHAR)")
        c.execute("INSERT INTO coverage VALUES ('000001','SZ'),('159915','SZ')")
        c.execute("CREATE TABLE security_daily_facts(symbol VARCHAR, trade_date DATE, "
                  "pre_close DOUBLE,is_st BOOLEAN,trading_status VARCHAR,source VARCHAR)")
        c.execute("INSERT INTO security_daily_facts VALUES "
                  "('000001.SZ','2024-01-02',9.5,false,'TRADING','baostock'),"
                  "('000001.SZ','2024-01-03',10,false,'SUSPENDED','baostock')")
    for code, asset in [("000001", "stock"), ("159915", "etf")]:
        p = old / f"lake/bars/daily/market=SZ/symbol={code}/bars.parquet"
        p.parent.mkdir(parents=True)
        pq.write_table(pa.Table.from_pylist([dict(
            symbol=code, trade_date=date(2024, 1, 2), open=10., high=11., low=9., close=10.,
            volume=100., amount=1000., asset_type=asset, pre_close=9.,
            pre_close_source="provider", is_st=False, is_st_source="provider",
        )]), p)
    p = old / "lake/adjustments/factors.parquet"
    p.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([
        dict(symbol="000001", market="SZ", trade_date=date(2024, 1, 2), cumulative_factor=2.),
        dict(symbol="159915", market="SZ", trade_date=date(2024, 1, 2), cumulative_factor=3.),
        dict(symbol="920001", market="SH", trade_date=date(2024, 1, 2), cumulative_factor=4.),
    ]), p)
    p = old / "lake/fundamentals/dated_inputs/000001.SZ.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"events": [dict(date="2024-01-02", category=1, fenhong=1.)]}))
    frozen, target = tmp_path / "frozen", tmp_path / "target"
    snapshot.snapshot(old, frozen)
    report = migration.migrate(frozen, target)
    assert report["rows"] == dict(daily_bars=1, daily_features=2, corporate_actions=3)
    assert report["derived_ready"] is False
    assert report["excluded_non_stock"] == {"etf_factor_anchors": 1}
    assert report["factor_market_aliases"] == {"920001.SH->920001.BJ": 1}
    public_result = public_data.stage(frozen, target)
    assert public_result["tables"]["coverage"] == 1
    assert public_result["tables"]["security_calendar"] == 1
    assert (target / "lake/bars/daily/market=SZ/symbol=159915/bars.parquet").exists()
    assert not (target / "lake/bars/daily/market=SZ/symbol=000001/bars.parquet").exists()
    assert public_data.stage(frozen, target) == public_result
    with stock_connection(target) as c:
        assert c.execute("SELECT count(*) FROM market_daily_summary").fetchone() == (0,)
        assert c.execute("SELECT source_pre_close,calc_status FROM daily_features "
                         "ORDER BY trade_date").fetchall() == [(9.5, "TRADED"), (10., "NO_TRADE")]
        assert c.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone() == (4,)
        factor_payload = c.execute("SELECT payload_json FROM corporate_actions "
                                   "WHERE symbol='920001.BJ'").fetchone()[0]
        assert json.loads(factor_payload)["market"] == "SH"
        with pytest.raises(Exception):
            c.execute("DELETE FROM daily_bars")
    repeated = migration.migrate(frozen, target, resume=True)
    assert repeated["rows"] == report["rows"]
    verified = verifier.verify(target)
    assert verified["status"] == "verified"
    assert verified["counts"]["daily_bars"] == 1
    with stock_connection(target, read_only=False) as c:
        c.execute("UPDATE daily_bars SET close=9.75")
        c.commit()
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    with pytest.raises(ValueError, match="checksum"):
        verifier.verify(target)
    with pytest.raises(FileExistsError):
        migration.migrate(frozen, target)
    (frozen / "catalog.duckdb").write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        migration.migrate(frozen, target, resume=True)


def test_store_does_not_create_on_read_or_overwrite(tmp_path):
    root = tmp_path / "db"
    with pytest.raises(Exception):
        with stock_connection(root):
            pass
    assert not root.exists()
    with stock_connection(root, create=True, read_only=False):
        pass
    with pytest.raises(FileExistsError):
        with stock_connection(root, create=True, read_only=False):
            pass
