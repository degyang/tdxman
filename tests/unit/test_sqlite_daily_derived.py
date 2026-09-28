"""Daily recursion, bounded warmup and transaction behavior on the new backend."""

from datetime import date, timedelta

import pytest

from aspool.api_contract import DataPoolError
from aspool.sqlite_daily_derived import derive_daily_row, recompute_symbol_features
from aspool.sqlite_stock_store import stock_connection


def bar(day="2024-01-02", close=11, **extra):
    return dict(
        symbol="000001.SZ",
        trade_date=day,
        bar_date=day,
        open=10,
        high=max(10, close),
        low=min(10, close),
        close=close,
        pre_close=10,
        is_st=0,
        volume=100,
        amount=1000,
        **extra,
    )


def calculate(row, previous, **kwargs):
    return derive_daily_row(row, previous_streak=previous, listed_days=10, **kwargs)


def seed(conn, day, close=10, **fields):
    conn.execute(
        "INSERT INTO daily_bars(symbol,trade_date,open,high,low,close,volume,amount,updated_at) "
        "VALUES ('000001.SZ',?,?,?,?,?,100,1000,1)",
        (day, close, close, close, close),
    )
    values = dict(
        symbol="000001.SZ",
        trade_date=day,
        pre_close=10,
        is_st=0,
        calc_status="TRADED",
        updated_at=1,
        **fields,
    )
    conn.execute(
        "INSERT INTO daily_features ("
        + ",".join(values)
        + ") VALUES ("
        + ",".join("?" for _ in values)
        + ")",
        tuple(values.values()),
    )


def test_gaps_freeze_but_invalid_and_unknown_break_streak():
    up, state = calculate(bar(), 2)
    assert up["close_limit_up"] == 1 and state == 3
    suspended = dict(bar(), trading_status="SUSPENDED", volume=0, amount=0)
    for gap in [suspended, dict(bar(), bar_date=None)]:
        out, next_state = calculate(gap, state)
        assert next_state == 3
        assert out["calc_status"] == "NO_TRADE"
        assert all(value is None for key, value in out.items() if key != "calc_status")
    _, state = calculate(bar(), state)
    assert state == 4
    invalid, state = calculate(dict(bar(), open=0), state)
    assert invalid["limit_status"] == "INVALID" and state is None
    unknown_height, state = calculate(bar(), state)
    assert unknown_height["close_limit_up"] == 1
    assert unknown_height["streak_known"] == 0 and state is None
    _, state = calculate(bar(close=10), state)
    assert state == 0
    _, state = calculate(bar(), state)
    assert state == 1
    missing, state = calculate(dict(bar(), pre_close=None), state)
    assert missing["limit_status"] == "UNKNOWN" and state is None


def test_first_observed_up_is_unknown_and_ipo_no_limit_is_known_zero():
    first, state = calculate(bar(), None)
    assert first["consecutive_up"] is None and state is None
    ipo, state = derive_daily_row(dict(bar(), pre_close=None), previous_streak=None, listed_days=1)
    assert ipo["limit_status"] == "NO_LIMIT" and state == 0
    assert all(
        ipo[key] == 0
        for key in ("touch_limit_up", "close_limit_up", "touch_limit_down", "close_limit_down")
    )
    unknown, _ = derive_daily_row(bar(), previous_streak=0, observed_sessions=1)
    assert unknown["limit_status"] == "UNKNOWN"


def test_suspension_with_turnover_is_a_conflict():
    with pytest.raises(DataPoolError, match="turnover"):
        calculate(dict(bar(), trading_status="SUSPENDED"), 2)


def test_ma20_uses_twenty_adjusted_valid_closes_with_no_future_anchor():
    row = dict(bar(close=5), pre_close=5)
    result, _ = calculate(row, 0, prior_closes=[(10, 1)] * 19, current_factor=2)
    assert result["ma20"] == 5 and result["above_ma20"] == 0
    for history, factor in [
        ([(10, 1)] * 18, 2),
        ([(10, 1)] * 18 + [(10, None)], 2),
        ([(10, 1)] * 19, None),
    ]:
        result, _ = calculate(row, 0, prior_closes=history, current_factor=factor)
        assert result["ma20"] is result["above_ma20"] is None


def test_sqlite_warmup_crosses_long_gaps_and_second_call_writes_nothing(tmp_path):
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        start = date(2023, 1, 1)
        for i in range(19):
            seed(
                conn,
                (start + timedelta(days=i)).isoformat(),
                limit_status="UNKNOWN",
                streak_known=0,
            )
        target = "2024-01-02"
        seed(conn, target, close=11)
        conn.execute(
            "INSERT INTO corporate_actions(symbol,effective_date,record_kind,source,source_key,"
            "cumulative_factor,valid_from,valid_through,factor_basis,updated_at) "
            "VALUES ('000001.SZ','2023-01-01','factor','verified','base',1,'2023-01-01',"
            "'2024-01-02','confirmed',1)"
        )
        result = recompute_symbol_features(conn, symbol="000001.SZ", start=target, end=target)
        assert result["read_rows"] == 20 and result["changed_rows"] == 1
        got = conn.execute(
            "SELECT ma20,above_ma20,streak_known FROM daily_features WHERE trade_date=?", (target,)
        ).fetchone()
        assert got == (10.05, 1, 0)
        assert (
            conn.execute(
                "SELECT count(*) FROM daily_features WHERE trade_date<? AND updated_at<>1",
                (target,),
            ).fetchone()[0]
            == 0
        )
        before = conn.total_changes
        result = recompute_symbol_features(conn, symbol="000001.SZ", start=target, end=target)
        assert result["changed_rows"] == 0 and conn.total_changes == before
        conn.rollback()


def test_feature_budget_rolls_back_partial_updates_and_requires_outer_transaction(tmp_path):
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        for day in ("2024-01-02", "2024-01-03"):
            seed(conn, day)
        conn.commit()
        with pytest.raises(ValueError, match="outer"):
            recompute_symbol_features(
                conn, symbol="000001.SZ", start="2024-01-02", end="2024-01-03"
            )
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(DataPoolError) as error:
            recompute_symbol_features(
                conn, symbol="000001.SZ", start="2024-01-02", end="2024-01-03", max_rows=1
            )
        assert error.value.code == "LOCAL_UPDATE_BUDGET_EXCEEDED"
        assert conn.execute("SELECT limit_status FROM daily_features").fetchall() == [
            (None,),
            (None,),
        ]
        assert conn.in_transaction


def test_historical_close_propagates_twenty_valid_positions_then_stops(tmp_path):
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        days = [(date(2024, 1, 1) + timedelta(days=i)).isoformat() for i in range(55)]
        for day in days:
            seed(conn, day)
        conn.execute(
            "INSERT INTO corporate_actions(symbol,effective_date,record_kind,source,source_key,"
            "cumulative_factor,valid_from,valid_through,factor_basis,updated_at) "
            "VALUES ('000001.SZ',?,'factor','verified','base',1,?,?,'confirmed',1)",
            (days[0], days[0], days[-1]),
        )
        recompute_symbol_features(conn, symbol="000001.SZ", start=days[0], end=days[-1])
        old = dict(conn.execute("SELECT trade_date,updated_at FROM daily_features"))
        conn.execute(
            "UPDATE daily_bars SET open=11,high=11,low=11,close=11 WHERE trade_date=?", (days[20],)
        )
        updated = recompute_symbol_features(
            conn, symbol="000001.SZ", start=days[20], end=days[20], propagate=True
        )
        assert updated["changed_dates"] == days[20:40]
        assert updated["processed_rows"] == 21
        now = dict(conn.execute("SELECT trade_date,updated_at FROM daily_features"))
        assert all(now[day] == old[day] for day in days[:20] + days[40:])
        bounded = conn.execute("SELECT * FROM daily_features ORDER BY trade_date").fetchall()
        # A fresh reference calculation is justified for this new propagation
        # algorithm; identical output proves its early stop did not miss changes.
        assert (
            recompute_symbol_features(conn, symbol="000001.SZ", start=days[0], end=days[-1])[
                "changed_rows"
            ]
            == 0
        )
        assert (
            conn.execute("SELECT * FROM daily_features ORDER BY trade_date").fetchall() == bounded
        )


def test_streak_suffix_continues_beyond_ma20_and_budget_failure_rolls_back(tmp_path):
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        days = [(date(2024, 1, 1) + timedelta(days=i)).isoformat() for i in range(55)]
        for i, day in enumerate(days):
            seed(conn, day, close=10 if i < 6 else 11)
        recompute_symbol_features(conn, symbol="000001.SZ", start=days[0], end=days[-1])
        before = conn.execute("SELECT * FROM daily_features ORDER BY trade_date").fetchall()
        conn.execute(
            "UPDATE daily_bars SET open=10,high=10,low=10,close=10 WHERE trade_date=?", (days[10],)
        )
        with pytest.raises(DataPoolError) as error:
            recompute_symbol_features(
                conn,
                symbol="000001.SZ",
                start=days[10],
                end=days[10],
                propagate=True,
                max_affected_dates=25,
            )
        assert error.value.code == "LOCAL_UPDATE_BUDGET_EXCEEDED"
        assert conn.execute("SELECT * FROM daily_features ORDER BY trade_date").fetchall() == before
        result = recompute_symbol_features(
            conn, symbol="000001.SZ", start=days[10], end=days[10], propagate=True
        )
        assert result["changed_rows"] == 45
        assert (
            conn.execute(
                "SELECT consecutive_up FROM daily_features WHERE trade_date=?", (days[-1],)
            ).fetchone()[0]
            == 44
        )
        assert (
            recompute_symbol_features(conn, symbol="000001.SZ", start=days[0], end=days[-1])[
                "changed_rows"
            ]
            == 0
        )


def test_unchanged_suspension_dates_do_not_consume_the_changed_date_budget(tmp_path):
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        days = [(date(2024, 1, 1) + timedelta(days=i)).isoformat() for i in range(150)]
        for day in days[:25] + days[125:]:
            seed(conn, day)
        conn.executemany(
            "INSERT INTO daily_features(symbol,trade_date,calc_status,trading_status,updated_at) "
            "VALUES ('000001.SZ',?,'NO_TRADE','SUSPENDED',1)",
            [(day,) for day in days[25:125]],
        )
        conn.execute(
            "INSERT INTO corporate_actions(symbol,effective_date,record_kind,source,source_key,"
            "cumulative_factor,valid_from,valid_through,factor_basis,updated_at) "
            "VALUES ('000001.SZ',?,'factor','verified','base',1,?,?,'confirmed',1)",
            (days[0], days[0], days[-1]),
        )
        recompute_symbol_features(conn, symbol="000001.SZ", start=days[0], end=days[-1])
        conn.execute(
            "UPDATE daily_bars SET open=11,high=11,low=11,close=11 WHERE trade_date=?", (days[24],)
        )
        result = recompute_symbol_features(
            conn,
            symbol="000001.SZ",
            start=days[24],
            end=days[24],
            propagate=True,
            max_affected_dates=25,
        )
        assert result["changed_rows"] == 20
        assert result["processed_rows"] == 121
        assert (
            conn.execute(
                "SELECT max(updated_at) FROM daily_features WHERE calc_status='NO_TRADE'"
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT ma20 FROM daily_features WHERE trade_date=?", (days[143],)
            ).fetchone()[0]
            == 10.05
        )
        assert (
            conn.execute(
                "SELECT ma20 FROM daily_features WHERE trade_date=?", (days[144],)
            ).fetchone()[0]
            == 10
        )
