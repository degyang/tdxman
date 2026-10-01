"""Exclusive breadth bins, scopes and cache stamps for bounded daily summaries."""

import json
import time

import pytest

from aspool.api_contract import DataPoolError
from aspool.sqlite_market_summary import (
    BUCKETS,
    recompute_daily_summary,
    recompute_market_window,
    return_bucket,
)
from aspool.sqlite_stock_store import stock_connection


@pytest.mark.parametrize(
    "magnitude,suffix", [(2, "02_04"), (4, "04_06"), (6, "06_08"), (8, "08_10"), (10, None)]
)
def test_exact_symmetric_bucket_edges(magnitude, suffix):
    assert return_bucket(100 + magnitude, 100) == ("pos_" + suffix if suffix else "pos_ge_10")
    assert return_bucket(100 - magnitude, 100) == ("neg_" + suffix if suffix else "neg_le_10")


def test_exclusive_limits_flat_and_tiny_returns():
    assert return_bucket(10.996, 10, limit_up=True) == "limit_up"
    assert return_bucket(9, 10, limit_down=True) == "limit_down"
    assert return_bucket(10.000000001, 10) == "pos_00_02"
    assert return_bucket(9.999999999, 10) == "neg_00_02"
    assert return_bucket(10, 10) == "flat"
    with pytest.raises(ValueError):
        return_bucket(10, 10, limit_up=True, limit_down=True)


def observation(
    conn,
    code,
    *,
    close=10,
    pre=10,
    status="TRADED",
    st=0,
    limit="KNOWN",
    up=0,
    touch=0,
    streak=0,
    previous=0,
    amount=0,
    turnover=None,
):
    symbol = f"{code:06d}.SZ"
    conn.execute(
        "INSERT INTO daily_bars(symbol,trade_date,close,amount,turnover_rate,updated_at) "
        "VALUES (?,'2024-01-02',?,?,?,1)",
        (symbol, close, amount, turnover),
    )
    values = dict(
        symbol=symbol,
        trade_date="2024-01-02",
        calc_status=status,
        pre_close=pre,
        is_st=st,
        updated_at=1,
    )
    if status == "TRADED":
        values.update(
            limit_status=limit,
            streak_known=int(streak is not None),
            consecutive_up=streak,
            prior_consecutive_up=previous,
        )
        if limit == "KNOWN":
            values.update(
                limit_up_price=11,
                limit_down_price=9,
                close_limit_up=up,
                touch_limit_up=touch,
                close_limit_down=0,
                touch_limit_down=0,
            )
    elif status == "INVALID":
        values.update(limit_status="INVALID", streak_known=0)
    conn.execute(
        "INSERT INTO daily_features ("
        + ",".join(values)
        + ") VALUES ("
        + ",".join("?" for _ in values)
        + ")",
        tuple(values.values()),
    )


def summary(conn, scope="all_stocks", day="2024-01-02"):
    cur = conn.execute(
        "SELECT * FROM market_daily_summary WHERE period_key=? AND scope=?", (day, scope)
    )
    return dict(zip([column[0] for column in cur.description], cur.fetchone()))


def test_summary_scopes_unknowns_denominators_and_noop_stamps(tmp_path):
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        observation(conn, 1, close=11, up=1, touch=1, streak=2, previous=1, amount=100, turnover=2)
        observation(conn, 2, close=10.8, touch=1, amount=None)
        observation(conn, 3, close=10.5, st=1, amount=0)
        observation(conn, 4, close=9.8, st=None, limit="UNKNOWN", streak=None, amount=200)
        observation(conn, 5, pre=None, limit="UNKNOWN", streak=None, amount=None)
        observation(conn, 6, status="NO_TRADE", amount=0)
        observation(conn, 7, status="INVALID", close=0)
        result = recompute_daily_summary(conn, trade_date="2024-01-02")
        assert result == dict(read_rows=14, changed_rows=2)
        all_stocks, ex_st = summary(conn), summary(conn, "exclude_known_st")
        assert all_stocks["trading_count"] == 5 and ex_st["trading_count"] == 4
        assert all_stocks["valid_return_count"] == 4 and all_stocks["invalid_return_count"] == 1
        assert all_stocks["limit_invalid_count"] == 0 and all_stocks["limit_unknown_count"] == 2
        assert all_stocks["st_unknown_count"] == 1
        assert all_stocks["amount_sum"] == 300 and all_stocks["amount_valid_count"] == 3
        assert all_stocks["turnover_valid_count"] == 1 and all_stocks["avg_turnover"] == 2
        assert all_stocks["sealed_ratio"] == 0.5 and all_stocks["promotion_ratio"] == 1
        assert all_stocks["max_consecutive_up"] == 2
        bins = json.loads(all_stocks["return_distribution_json"])
        assert set(bins) == set(BUCKETS) and sum(bins.values()) == 4
        assert bins["limit_up"] == bins["pos_08_10"] == bins["pos_04_06"] == bins["neg_02_04"] == 1
        changes = conn.total_changes
        assert recompute_daily_summary(conn, trade_date="2024-01-02")["changed_rows"] == 0
        assert conn.total_changes == changes
        assert summary(conn)["updated_at"] == all_stocks["updated_at"]
        assert (
            recompute_daily_summary(conn, trade_date="2024-01-02", inputs_changed=True)[
                "changed_rows"
            ]
            == 2
        )
        assert summary(conn)["updated_at"] > all_stocks["updated_at"]


def test_summary_stamp_is_after_source_stamp_when_wall_clock_moves_back(tmp_path):
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        observation(conn, 1)
        future_stamp = time.time_ns() // 1000 + 1_000_000_000
        conn.execute("UPDATE daily_bars SET updated_at=?", (future_stamp,))
        recompute_daily_summary(conn, trade_date="2024-01-02", inputs_changed=True)
        assert summary(conn)["updated_at"] > future_stamp


def test_zero_market_session_and_unknown_only_height(tmp_path):
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        conn.execute("BEGIN IMMEDIATE")
        recompute_daily_summary(conn, trade_date="2024-01-01")
        empty = summary(conn, day="2024-01-01")
        assert empty["trading_count"] == 0 and empty["max_consecutive_up"] == 0
        assert empty["sealed_ratio"] is empty["amount_sum"] is empty["up_pct"] is None
        assert sum(json.loads(empty["return_distribution_json"]).values()) == 0
        observation(conn, 1, close=11, up=1, touch=1, streak=None, previous=None)
        recompute_daily_summary(conn, trade_date="2024-01-02")
        unknown = summary(conn)
        assert unknown["max_consecutive_up"] is None and unknown["streak_unknown_count"] == 1


def test_uncomputed_features_and_budget_never_publish_partial_scopes(tmp_path):
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        observation(conn, 1, limit=None, streak=None)
        recompute_daily_summary(conn, trade_date="2024-01-02")
        assert summary(conn)["trading_count"] == 0
        before = conn.execute("SELECT * FROM market_daily_summary ORDER BY scope").fetchall()
        conn.execute("UPDATE daily_features SET limit_status='UNKNOWN'")
        observation(conn, 2)
        with pytest.raises(DataPoolError) as error:
            recompute_daily_summary(conn, trade_date="2024-01-02", max_rows=1)
        assert error.value.code == "LOCAL_UPDATE_BUDGET_EXCEEDED"
        assert (
            conn.execute("SELECT * FROM market_daily_summary ORDER BY scope").fetchall() == before
        )


def test_window_atomicity_when_another_symbol_is_not_prepared(tmp_path):
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        for code in (1, 2):
            observation(conn, code, limit=None, streak=None)
        conn.execute("UPDATE daily_bars SET open=close,high=close,low=close")
        result = recompute_market_window(
            conn, symbols=["000001.SZ"], market_sessions=["2024-01-02"]
        )
        assert result["feature_rows"] == 1 and summary(conn)["trading_count"] == 1
        result = recompute_market_window(
            conn, symbols=["000001.SZ", "000002.SZ"], market_sessions=["2024-01-02"]
        )
        assert result["feature_rows"] == 1 and summary(conn)["trading_count"] == 2
        before = conn.total_changes
        result = recompute_market_window(
            conn, symbols=["000001.SZ", "000002.SZ"], market_sessions=["2024-01-02"]
        )
        assert result["feature_rows"] == result["summary_rows"] == 0
        assert conn.total_changes == before
