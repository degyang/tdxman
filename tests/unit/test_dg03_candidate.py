"""New DG-03 candidate checks only; existing DG-00/01/02 suites are not rerun."""

from datetime import date

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from aspool.api_contract import DataPoolError
from aspool.dg03_candidate import CandidatePool, build
from aspool.dg03_etf import read_etf_daily
from aspool.pool import DataPool


@pytest.fixture
def candidate(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    with duckdb.connect(str(source / "catalog.duckdb")) as c:
        c.execute("CREATE TABLE sentinel(id INTEGER PRIMARY KEY, value VARCHAR)")
        c.execute("INSERT INTO sentinel VALUES (1,'original')")
    for code, asset in [("000001", None), ("510300", "etf")]:
        path = source / f"lake/bars/daily/market=SH/symbol={code}/bars.parquet"
        path.parent.mkdir(parents=True)
        pq.write_table(
            pa.Table.from_pylist(
                [
                    dict(
                        symbol="raw-" + code,
                        code=code,
                        trade_date=date(2025, 1, d),
                        open=10.0,
                        high=11.0,
                        low=9.0,
                        close=10.0,
                        volume=100.0,
                        amount=1000.0,
                        pre_close=10.0,
                        is_st=False,
                        extension="source-extension",
                        asset_type=asset,
                    )
                    for d in [2, 3, 6]
                ]
            ),
            path,
        )
    target = tmp_path / "candidate"
    build(source, target)
    return source, target, CandidatePool(target)


def test_full_public_and_etf(candidate):
    source, target, pool = candidate
    pd.testing.assert_frame_equal(DataPool(source).read_daily(), pool.read_daily())
    from aspool.etf_api import read_etf_daily as original

    pd.testing.assert_frame_equal(original(source), read_etf_daily(target))
    with duckdb.connect(str(target / "catalog.duckdb"), read_only=True) as c:
        assert c.execute("SELECT DISTINCT symbol FROM dg03_daily ORDER BY symbol").fetchall() == [
            ("raw-000001",),
            ("raw-510300",),
        ]
        assert c.execute("SELECT * FROM sentinel").fetchall() == [(1, "original")]
    assert len(pool.read_daily(symbols="SH.000001", lookback=1)) == 1
    with pytest.raises(DataPoolError, match="fields"):
        pool.read_daily(fields=["invalid"])


def test_atomic_changes_noop_null_delete_backfill(candidate):
    _, target, pool = candidate

    def apply(values=None, day=2, operation="merge"):
        return pool.apply(
            [
                dict(
                    market="SH",
                    code="000001",
                    trade_date=date(2025, 1, day),
                    values=values or {},
                    operation=operation,
                )
            ],
            source="fixture",
            reason="explicit correction",
        )

    import hashlib

    fingerprint = hashlib.sha256((target / "catalog.duckdb").read_bytes()).hexdigest()
    assert apply({"close": 10.0, "pre_close": None}) == {"changed": 0, "revision": 0}
    assert hashlib.sha256((target / "catalog.duckdb").read_bytes()).hexdigest() == fingerprint
    assert apply({"amount": 1001.0})["revision"] == 1
    assert apply({"pre_close": None})["changed"] == 0
    assert apply({"pre_close": None}, operation="clear")["changed"] == 1
    assert pd.isna(pool.read_daily(symbols="000001.SH").iloc[0].pre_close)
    assert apply(operation="delete")["changed"] == 1
    assert apply(operation="delete")["changed"] == 0
    values = dict(open=10.0, high=11.0, low=9.0, close=10.0, volume=100.0, amount=1000.0)
    assert apply(values, day=1)["changed"] == 1
    with pytest.raises(ValueError):
        pool.apply(
            [
                dict(market="SH", code="000001", trade_date="2025-01-03", values={"amount": 999}),
                dict(market="SH", code="000001", trade_date="2025-01-03", values={"is_st": 1}),
            ],
            source="fixture",
            reason="rollback",
        )
    assert pool.read_daily(symbols="000001.SH", start="2025-01-03").iloc[0].amount == 1000
    with duckdb.connect(str(target / "catalog.duckdb"), read_only=True) as c:
        assert c.execute("SELECT count(*) FROM dg03_changes").fetchone()[0] == 4
    with duckdb.connect(str(target / "catalog.duckdb")) as c:
        with pytest.raises(duckdb.ConstraintException):
            c.execute("INSERT INTO dg03_daily SELECT * FROM dg03_daily LIMIT 1")


def test_unsafe_target(candidate):
    source, _, _ = candidate
    with pytest.raises(ValueError, match="unsafe"):
        build(source, source / "inside")


def test_real_process_recovery_and_locking(candidate, tmp_path):
    import importlib.util
    import shutil
    from pathlib import Path

    script = Path(__file__).parents[2] / "scripts/dg03/bench.py"
    spec = importlib.util.spec_from_file_location("dg03_bench", script)
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    bench.LAB = tmp_path / "process-lab"
    bench.LAB.mkdir()
    shutil.copytree(candidate[1], bench.LAB / "candidate")
    bench.recovery()


def test_atomic_stale_and_coverage(candidate):
    _, target, pool = candidate
    with duckdb.connect(str(target / "catalog.duckdb")) as c:
        c.execute("CREATE TABLE daily_limit_publication(trade_date DATE PRIMARY KEY)")
        c.execute(
            "INSERT INTO daily_limit_publication VALUES ('2025-01-01'),"
            "('2025-01-02'),('2025-01-03')"
        )
        c.execute(
            "CREATE TABLE daily_limit_staleness(trade_date DATE PRIMARY KEY, reason VARCHAR, "
            "marked_at TIMESTAMP)"
        )
        c.execute(
            "CREATE TABLE coverage(symbol VARCHAR PRIMARY KEY,market VARCHAR,"
            "start_date DATE,end_date DATE,row_count BIGINT,source VARCHAR,updated_at TIMESTAMP)"
        )
        c.execute(
            "INSERT INTO coverage VALUES ('000001','SH','2025-01-02','2025-01-06',3,"
            "'fixture',current_timestamp)"
        )
    change = dict(market="SH", code="000001", trade_date="2025-01-02", values={"amount": 999})
    pool.apply([change], source="test", reason="correct")
    with duckdb.connect(str(target / "catalog.duckdb"), read_only=True) as c:
        assert c.execute("SELECT count(*) FROM daily_limit_staleness").fetchone()[0] == 2
        before = c.execute("SELECT * FROM coverage").fetchall()
        stale = c.execute("SELECT * FROM daily_limit_staleness ORDER BY trade_date").fetchall()
    assert pool.apply([change], source="test", reason="no-op")["changed"] == 0
    with duckdb.connect(str(target / "catalog.duckdb"), read_only=True) as c:
        assert c.execute("SELECT * FROM coverage").fetchall() == before
        assert (
            c.execute("SELECT * FROM daily_limit_staleness ORDER BY trade_date").fetchall() == stale
        )
    change["operation"] = "delete"
    pool.apply([change], source="test", reason="delete")
    with duckdb.connect(str(target / "catalog.duckdb"), read_only=True) as c:
        assert c.execute("SELECT start_date,row_count FROM coverage").fetchone() == (
            date(2025, 1, 3),
            2,
        )


def test_dated_facts_atomic_with_raw_and_null_precedence(candidate):
    from datetime import datetime

    _, target, pool = candidate
    with duckdb.connect(str(target / "catalog.duckdb")) as c:
        c.execute(
            "CREATE TABLE security_daily_facts(symbol VARCHAR, trade_date DATE, "
            "pre_close DOUBLE, is_st BOOLEAN, trading_status VARCHAR, source VARCHAR, "
            "fetched_at TIMESTAMP, PRIMARY KEY(symbol,trade_date))"
        )
    raw = dict(market="SH", code="000001", trade_date="2025-01-02", values={"amount": 123.0})
    fact = dict(
        domain="facts",
        market="SH",
        code="000001",
        trade_date="2025-01-02",
        values={
            "pre_close": 12.0,
            "is_st": True,
            "source": "dated:test",
            "fetched_at": datetime(2025, 1, 3),
        },
    )
    result = pool.apply([raw, fact], source="batch:test", reason="same input generation")
    assert result == {"changed": 2, "revision": 1}
    frame = pool.read_daily(symbols="000001.SH", start="2025-01-02", end="2025-01-02")
    assert frame.iloc[0].pre_close == 12.0
    assert frame.iloc[0].pre_close_source == "dated:test"
    assert frame.iloc[0].amount == 123.0
    assert pool.apply([raw, fact], source="batch:test", reason="retry")["changed"] == 0
    fact["operation"] = "clear"
    fact["values"] = {"pre_close": None}
    pool.apply([fact], source="batch:test", reason="explicit revoke")
    assert pool.read_daily(symbols="000001.SH").iloc[0].pre_close == 10.0
    fact["operation"] = "delete"
    pool.apply([fact], source="batch:test", reason="explicit delete fact")
    with duckdb.connect(str(target / "catalog.duckdb"), read_only=True) as c:
        assert c.execute("SELECT count(*) FROM security_daily_facts").fetchone()[0] == 0


def test_frozen_public_read_semantics():
    import ast
    import inspect
    import textwrap

    original = ast.parse(textwrap.dedent(inspect.getsource(DataPool.read_daily.__wrapped__)))
    candidate = ast.parse(textwrap.dedent(inspect.getsource(CandidatePool.read_daily.__wrapped__)))
    assert ast.dump(original) == ast.dump(candidate)


def test_candidate_public_status_and_etf_routes(candidate):
    source, target, pool = candidate
    expected = DataPool(source).status()
    actual = pool.status()
    expected.pop("root")
    actual.pop("root")
    assert actual == expected
    pd.testing.assert_frame_equal(DataPool(source).read_etf_daily(), pool.read_etf_daily())
    pd.testing.assert_frame_equal(DataPool(source).list_etfs(), pool.list_etfs())


def test_candidate_event_amount_revision_and_missing(tmp_path):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "old_amount_fixture", Path(__file__).with_name("test_limit_amount.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = tmp_path / "source"
    module._seed(source)
    target = tmp_path / "candidate"
    build(source, target)
    pool = CandidatePool(target)
    params = dict(start="2026-09-23", end="2026-09-24", batch_days=1)
    with DataPool(source).iter_limit_events_with_amount(**params) as old:
        expected = list(old)
    with pool.iter_limit_events_with_amount(**params) as new:
        actual = list(new)
    for a, b in zip(expected, actual, strict=True):
        pd.testing.assert_frame_equal(a, b)
    with pool.iter_limit_events_with_amount(**params) as new:
        next(new)
        pool.apply(
            [dict(market="SZ", code="000001", trade_date="2026-09-23", values={"amount": 13000.0})],
            source="test",
            reason="correction",
        )
        with pytest.raises(DataPoolError) as error:
            next(new)
        assert error.value.code == "LIMIT_REVISION_CHANGED"
    assert new.closed
