"""Public SQLite projections, snapshot isolation and bounded continuation."""

import pytest

from aspool import DataPool, DataPoolError
from aspool.sqlite_stock_store import stock_connection


def seed(root):
    with stock_connection(root, create=True, read_only=False) as conn:
        for symbol in ("000001.SZ", "000002.SZ", "000003.SZ"):
            for day in ("2026-09-23", "2026-09-24"):
                conn.execute(
                    "INSERT INTO daily_bars(symbol,trade_date,open,high,low,close,volume,amount,"
                    "updated_at) "
                    "VALUES (?,?,10,11,10,11,100,1000,1)",
                    (symbol, day),
                )
                conn.execute(
                    "INSERT INTO daily_features(symbol,trade_date,calc_status,limit_status,"
                    "pre_close,is_st,limit_up_price,limit_down_price,touch_limit_up,"
                    "close_limit_up,touch_limit_down,close_limit_down,consecutive_up,"
                    "prior_consecutive_up,streak_known,updated_at) VALUES (?,?,'TRADED','KNOWN',"
                    "10,0,11,9,1,1,0,0,1,0,1,1)",
                    (symbol, day),
                )
        conn.execute(
            "INSERT INTO market_daily_summary(frequency,period_key,scope,period_start,"
            "period_end,as_of,session_count,trading_count,updated_at) VALUES ('D',"
            "'2026-09-24','all_stocks','2026-09-24','2026-09-24','2026-09-24',1,3,1)"
        )
        conn.commit()


def test_projected_daily_aliases_lookback_and_empty(tmp_path):
    seed(tmp_path)
    pool = DataPool(tmp_path)
    frame = pool.read_daily(
        symbols="SZ.000001", lookback=1, fields=["symbol", "date", "close", "is_st"]
    )
    assert frame.columns.tolist() == ["symbol", "date", "close", "is_st"]
    assert frame.symbol.tolist() == ["000001.SZ"]
    assert frame.date.dt.strftime("%Y-%m-%d").tolist() == ["2026-09-24"]
    assert str(frame.is_st.dtype) == "boolean"
    assert pool.read_daily(symbols=[], fields=["date", "close"]).empty
    assert pool.describe()["contract_version"] == 3
    with pytest.raises(DataPoolError, match="supported fields"):
        pool.read_daily(fields=["batch_id"])


def test_snapshot_keeps_daily_and_amount_on_same_version(tmp_path):
    seed(tmp_path)
    pool = DataPool(tmp_path)
    with pool.stock_snapshot() as reader:
        first = reader.read_daily(symbols="000001.SZ", lookback=1, fields=["amount"])
        with stock_connection(tmp_path, read_only=False) as writer:
            writer.execute("UPDATE daily_bars SET amount=2000 WHERE symbol='000001.SZ'")
            writer.commit()
        events = reader.read_limit_events(
            trade_date="2026-09-24", symbols="000001.SZ", fields=["amount"]
        )
        assert first.amount.iloc[0] == events.amount.iloc[0] == 1000
    assert (
        pool.read_daily(symbols="000001.SZ", lookback=1, fields=["amount"]).amount.iloc[0] == 2000
    )


def test_single_day_over_page_limit_continues_without_loss(tmp_path):
    seed(tmp_path)
    pool = DataPool(tmp_path)
    with pool.iter_limit_events_with_amount(
        start="2026-09-23",
        end="2026-09-24",
        fields=["trade_date", "symbol", "amount"],
        max_rows=2,
        batch_days=1,
    ) as stream:
        frames = list(stream)
        assert stream.completed and stream.closed
    assert [len(frame) for frame in frames] == [2, 1, 2, 1]
    keys = [
        tuple(row)
        for frame in frames
        for row in frame[["trade_date", "symbol"]].itertuples(index=False, name=None)
    ]
    assert len(keys) == len(set(keys)) == 6
    assert keys == sorted(keys)
    with pool.iter_limit_events_with_amount(
        start="2026-09-23", end="2026-09-24", max_rows=1
    ) as stream:
        next(stream)
    assert stream.closed and not stream.completed and stream.reader is None


def test_summary_and_legacy_quality_projections(tmp_path):
    seed(tmp_path)
    pool = DataPool(tmp_path)
    frame = pool.read_market_daily(
        start="2026-09-24", end="2026-09-24", fields=["period_key", "trading_count"]
    )
    assert frame.to_dict("records") == [{"period_key": "2026-09-24", "trading_count": 3}]
    with pytest.raises(DataPoolError) as error:
        pool.read_market_summary(start="2026-09-24", end="2026-09-24", frequency="W")
    assert error.value.code == "FREQUENCY_NOT_READY"
    coverage = pool.read_limit_coverage()
    assert coverage.columns.tolist() == [
        "trade_date", "batch_id", "scope_id", "rule_version", "computed_at",
        "processed_count", "known_count", "unknown_count", "no_limit_count",
        "invalid_count", "status", "stale", "stale_reason",
    ]
    assert coverage.trade_date.dt.strftime("%Y-%m-%d").tolist() == ["2026-09-24"]
    assert coverage.batch_id.isna().all() and not coverage.stale.iloc[0]


def test_limit_exception_projection_has_no_publication_identity(tmp_path):
    seed(tmp_path)
    with stock_connection(tmp_path, read_only=False) as conn:
        conn.execute(
            "UPDATE daily_features SET limit_status='UNKNOWN',limit_reason='missing_reference',"
            "touch_limit_up=NULL,close_limit_up=NULL,touch_limit_down=NULL,"
            "close_limit_down=NULL,limit_up_price=NULL,limit_down_price=NULL,"
            "streak_known=0,consecutive_up=NULL WHERE symbol='000001.SZ' "
            "AND trade_date='2026-09-24'"
        )
        conn.commit()
    frame = DataPool(tmp_path).read_limit_exceptions(trade_date="2026-09-24")
    assert frame[["symbol", "kind", "reason"]].to_dict("records") == [
        {"symbol": "000001.SZ", "kind": "UNKNOWN", "reason": "missing_reference"}
    ]
    assert frame.batch_id.isna().all()


def test_nullable_summary_counts_stay_integers_for_consumers(tmp_path):
    import json

    seed(tmp_path)
    with stock_connection(tmp_path, read_only=False) as conn:
        conn.execute("UPDATE market_daily_summary SET max_consecutive_up=5")
        conn.execute(
            "INSERT INTO market_daily_summary(frequency,period_key,scope,period_start,period_end,"
            "as_of,session_count,updated_at) VALUES ('D','2026-09-23','all_stocks',"
            "'2026-09-23','2026-09-23','2026-09-23',1,1)"
        )
        conn.commit()
    frame = DataPool(tmp_path).read_market_daily(
        start="2026-09-23", end="2026-09-24", fields=["period_key", "max_consecutive_up"]
    )
    assert str(frame.max_consecutive_up.dtype) == "Int64"
    rows = json.loads(frame.to_json(orient="records"))
    assert rows[0]["max_consecutive_up"] is None
    assert type(rows[1]["max_consecutive_up"]) is int


def test_stream_resource_budget_and_context_ownership(tmp_path):
    seed(tmp_path)
    pool = DataPool(tmp_path)
    with pool.iter_limit_events_with_amount(
        start="2026-09-24", end="2026-09-24", memory_limit="64MB"
    ) as stream:
        assert stream.reader.conn.execute("PRAGMA cache_size").fetchone()[0] == -32768
        next(stream)
        with pytest.raises(ValueError, match="once"):
            stream.__enter__()
    with pytest.raises(DataPoolError):
        pool.iter_limit_events_with_amount(
            start="2026-09-24", end="2026-09-24", memory_limit="10GB"
        )


def test_daily_price_validation_is_not_hidden_by_narrow_projection(tmp_path):
    seed(tmp_path)
    with stock_connection(tmp_path, read_only=False) as conn:
        conn.execute("UPDATE daily_bars SET close=-1 WHERE trade_date='2026-09-24'")
        conn.commit()
    with pytest.raises(DataPoolError) as error:
        DataPool(tmp_path).read_daily(symbols="000001.SZ", lookback=1, fields=["symbol"])
    assert error.value.code == "DAILY_INVALID"


def test_symbol_alias_duplicates_do_not_duplicate_lookback_rows(tmp_path):
    seed(tmp_path)
    frame = DataPool(tmp_path).read_daily(
        symbols=["000002.SZ", "SZ.000001", "000001.SZ"],
        lookback=1,
        fields=["symbol", "date"],
    )
    assert frame.symbol.tolist() == ["000001.SZ", "000002.SZ"]
