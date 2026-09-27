"""New monthly publication checks; all data is isolated in pytest roots."""

import json
import os
import subprocess
import sys

import pandas as pd
import pytest

from aspool.dg03_candidate import literal
from aspool.dg03_monthly import MonthlyPool, connection, digest, initialize, install_part
from tests.unit.test_dg03_candidate import candidate as base_candidate


@pytest.fixture
def monthly(tmp_path):
    source, native, expected = base_candidate.__wrapped__(tmp_path)
    root = initialize(source, tmp_path / "monthly", native)
    with connection(root) as c:
        c.execute(f"ATTACH {literal(native / 'catalog.duckdb')} AS source (READ_ONLY)")
        c.execute(
            "COPY (SELECT * FROM source.dg03_daily WHERE false) TO "
            f"{literal(root / 'empty.parquet')} (FORMAT PARQUET)"
        )
        install_part(c, root, "2025-01", "SELECT * FROM source.dg03_daily")
        c.execute("CHECKPOINT")
    return root, MonthlyPool(root), expected


def test_monthly_contract_and_changes(monthly):
    root, pool, expected = monthly
    pd.testing.assert_frame_equal(pool.read_daily(), expected.read_daily())
    pd.testing.assert_frame_equal(pool.read_etf_daily(), expected.read_etf_daily())
    row = dict(market="SH", code="000001", trade_date="2025-01-02", values={"amount": 1000.0})
    old = digest(root / "catalog.duckdb")
    assert pool.apply([row], source="test", reason="retry")["changed"] == 0
    assert digest(root / "catalog.duckdb") == old
    row["values"] = {"amount": 1111.0}
    assert pool.apply([row], source="test", reason="correct")["changed"] == 1
    assert pool.read_daily(symbols="000001.SH").iloc[0].amount == 1111.0
    # Explicit NULL preserves original merge/clear distinction.
    row["values"] = {"pre_close": None}
    assert pool.apply([row], source="test", reason="merge null")["changed"] == 0
    assert pool.apply([dict(row, operation="clear")], source="test", reason="clear")["changed"] == 1
    assert pd.isna(pool.read_daily(symbols="000001.SH").iloc[0].pre_close)
    assert (
        pool.apply([dict(row, operation="delete")], source="test", reason="delete")["changed"] == 1
    )
    assert len(pool.read_daily(symbols="000001.SH")) == 2
    with pytest.raises(ValueError, match="duplicate"):
        pool.apply([dict(row, trade_date="2025-01-03")] * 2, source="test", reason="duplicate")
    with connection(root, True) as c:
        assert c.execute("SELECT * FROM sentinel").fetchall() == [(1, "original")]
        assert c.execute("SELECT revision FROM dg03_revision").fetchone()[0] == 3


def test_monthly_multipart_crash_and_coverage(monthly, tmp_path):
    root, pool, _ = monthly
    with connection(root) as c:
        c.execute(
            "CREATE TABLE coverage(symbol VARCHAR PRIMARY KEY,market VARCHAR,"
            "start_date DATE,end_date DATE,row_count BIGINT,source VARCHAR,updated_at TIMESTAMP)"
        )
        c.execute(
            "INSERT INTO coverage VALUES ('000001','SH','2025-01-02','2025-01-06',"
            "3,'fixture','2025-01-01')"
        )
        c.execute(
            "CREATE TABLE security_daily_facts(symbol VARCHAR,trade_date DATE,pre_close DOUBLE,"
            "is_st BOOLEAN,trading_status VARCHAR,source VARCHAR,fetched_at TIMESTAMP,"
            "PRIMARY KEY(symbol,trade_date))"
        )
        c.execute(
            "INSERT INTO security_daily_facts VALUES ('000001.SH','2025-01-02',10.,false,"
            "'trading','old','2025-01-01')"
        )
        c.execute("CREATE TABLE daily_limit_publication(trade_date DATE PRIMARY KEY)")
        c.execute("INSERT INTO daily_limit_publication VALUES ('2025-01-02'),('2025-02-03')")
        c.execute(
            "CREATE TABLE daily_limit_staleness(trade_date DATE PRIMARY KEY,"
            "reason VARCHAR,marked_at TIMESTAMP)"
        )
    changes = [
        dict(market="SH", code="000001", trade_date="2025-01-02", values={"amount": 2222.0}),
        dict(
            market="SH",
            code="000001",
            trade_date="2025-02-03",
            values=dict(open=10.0, high=11.0, low=9.0, close=10.0, volume=100.0, amount=3333.0),
        ),
        dict(
            domain="facts",
            market="SH",
            code="000001",
            trade_date="2025-01-02",
            values=dict(pre_close=9.0, source="new", fetched_at="2025-02-03"),
        ),
    ]
    script = """import json,os,signal,sys
from aspool.dg03_monthly import MonthlyPool
root,point,changes=sys.argv[1:]
def fault(p):
    if p==point: os.kill(os.getpid(),signal.SIGKILL)
MonthlyPool(root).apply(json.loads(changes),source="crash",reason="multipart",_fault_hook=fault)
"""
    original = digest(root / "catalog.duckdb")
    before = pool.read_daily()
    results = []
    for point in ["before_publish", "after_publish"]:
        p = subprocess.run(
            [sys.executable, "-c", script, str(root), point, json.dumps(changes)],
            capture_output=True,
            text=True,
        )
        assert p.returncode == -9, p.stderr
        if point == "before_publish":
            assert digest(root / "catalog.duckdb") == original
            pd.testing.assert_frame_equal(before, pool.read_daily())
        else:
            frame = pool.read_daily(symbols="000001.SH")
            assert (
                len(frame) == 4
                and frame.iloc[0].amount == 2222.0
                and frame.iloc[-1].amount == 3333.0
            )
            with connection(root, True) as c:
                assert c.execute("SELECT revision FROM dg03_revision").fetchone() == (1,)
                assert c.execute("SELECT count(*) FROM dg03_changes").fetchone() == (3,)
                assert c.execute(
                    "SELECT pre_close,source FROM security_daily_facts"
                ).fetchone() == (9.0, "new")
                assert c.execute("SELECT count(*) FROM daily_limit_staleness").fetchone() == (2,)
                assert c.execute("SELECT row_count FROM coverage").fetchone() == (4,)
                assert c.execute("SELECT count(*) FROM dg03_monthly_parts").fetchone() == (2,)
        results.append(
            dict(
                point=point, returncode=p.returncode, catalog_sha256=digest(root / "catalog.duckdb")
            )
        )
    # Deleting the only hot-month row must restore coverage from the cold month.
    pool.apply([dict(changes[1], operation="delete")], source="test", reason="boundary")
    with connection(root, True) as c:
        assert c.execute(
            "SELECT start_date::VARCHAR,end_date::VARCHAR,row_count FROM coverage"
        ).fetchone() == ("2025-01-02", "2025-01-06", 3)
    report = os.environ.get("DG03_MONTHLY_CRASH_REPORT")
    if report:
        from pathlib import Path

        Path(report).write_text(
            json.dumps(
                dict(
                    root=str(root),
                    results=results,
                    multipart_atomic=True,
                    six_domains_verified=True,
                ),
                indent=2,
            )
        )


def test_monthly_changed_months_and_portable_snapshot(monthly, tmp_path):
    import shutil

    from aspool.api_contract import DataPoolError
    from aspool.dg03_monthly import part_path

    root, pool, _ = monthly
    values = dict(open=10.0, high=11.0, low=9.0, close=10.0, volume=100.0, amount=1000.0)
    feb = dict(market="SH", code="000001", trade_date="2025-02-03", values=values)
    pool.apply([feb], source="test", reason="new month")
    with connection(root, True) as c:
        old = dict(c.execute("SELECT month,path FROM dg03_monthly_parts").fetchall())
    mixed = [
        feb,
        dict(market="SH", code="000001", trade_date="2025-01-02", values={"amount": 2222.0}),
    ]
    assert pool.apply(mixed, source="test", reason="mixed")["published_months"] == 1
    with connection(root, True) as c:
        new = dict(c.execute("SELECT month,path FROM dg03_monthly_parts").fetchall())
    assert old["2025-02"] == new["2025-02"] and old["2025-01"] != new["2025-01"]
    dest = tmp_path / "restored"
    shutil.copytree(root, dest)
    shutil.move(root, tmp_path / "unavailable-old-root")
    restored = MonthlyPool(dest)
    assert len(restored.read_daily(symbols="000001.SH")) == 4
    with connection(dest, True) as c:
        for path, sha in c.execute("SELECT path,sha256 FROM dg03_monthly_parts").fetchall():
            assert digest(part_path(dest, path)) == sha
    part_path(dest, new["2025-01"]).unlink()
    for method in [restored.read_etf_daily, restored.list_etfs]:
        with pytest.raises(DataPoolError) as caught:
            method()
        assert caught.value.code == "DAILY_INVALID"


def test_monthly_facts_only_plus_noop_preserves_parts(monthly):
    root, pool, _ = monthly
    with connection(root) as c:
        c.execute(
            "CREATE TABLE security_daily_facts(symbol VARCHAR,trade_date DATE,"
            "pre_close DOUBLE,is_st BOOLEAN,trading_status VARCHAR,source VARCHAR,"
            "fetched_at TIMESTAMP,PRIMARY KEY(symbol,trade_date))"
        )
        before = c.execute("SELECT * FROM dg03_monthly_parts ORDER BY month").fetchall()
    changes = [
        dict(market="SH", code="000001", trade_date="2025-01-02", values={"amount": 1000.0}),
        dict(
            domain="facts",
            market="SH",
            code="000001",
            trade_date="2025-01-02",
            values=dict(pre_close=9.0, source="new", fetched_at="2025-01-02"),
        ),
    ]
    result = pool.apply(changes, source="test", reason="facts with raw no-op")
    assert result["changed"] == 1 and result["published_months"] == 0
    with connection(root, True) as c:
        assert c.execute("SELECT * FROM dg03_monthly_parts ORDER BY month").fetchall() == before
        assert c.execute("SELECT pre_close FROM security_daily_facts").fetchone() == (9.0,)


def test_monthly_symbol_pushdown_preserves_selection(monthly):
    import duckdb

    from aspool.dg03_monthly import MonthlyStorage

    root, pool, expected = monthly
    for symbols in ["000001.SH", "999999.SH", [], ["000001.SH", "510300.SH"]]:
        pd.testing.assert_frame_equal(
            pool.read_daily(symbols=symbols), expected.read_daily(symbols=symbols)
        )
    pd.testing.assert_frame_equal(
        pool.read_etf_daily(symbols="510300.SH"), expected.read_etf_daily(symbols="510300.SH")
    )
    with duckdb.connect() as c:
        MonthlyStorage(root).bind(c, "bars", symbols=["SH.000001"])
        plan = c.execute("EXPLAIN SELECT amount FROM bars").fetchone()[1]
        assert "__code=" in plan
