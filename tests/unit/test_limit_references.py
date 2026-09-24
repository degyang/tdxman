"""Reference-price recovery must preserve uncertainty and corporate actions."""

from datetime import date

from aspool.limit_events import _reference_price, _SymbolBars


def test_previous_close_requires_same_day_return_confirmation():
    previous = date(2021, 9, 23)
    price, audit = _reference_price({
        "close": 11.0, "pre_close": 8.0, "pct_chg": 10.0,
        "previous_trade_date": previous, "previous_close": 10.0,
    }, previous)
    assert price == 10.0
    assert audit["basis"] == "previous_session_close_confirmed_by_return"


def test_ex_dividend_does_not_use_previous_close():
    previous = date(2021, 9, 23)
    price, audit = _reference_price({
        "close": 10.45, "pre_close": 8.0, "pct_chg": 10.0,
        "previous_trade_date": previous, "previous_close": 10.0,
    }, previous)
    assert price == 9.5
    assert audit["basis"] == "same_day_return_unique_cent"


def test_high_price_rounded_return_is_ambiguous():
    price, audit = _reference_price({"close": 1694.0, "pre_close": 1446.62, "pct_chg": 3.609})
    assert price is None
    assert audit["candidate_min"] < audit["candidate_max"]


def test_two_decimal_return_does_not_invent_precision():
    price, audit = _reference_price({"close": 347.0, "pre_close": 300.0, "pct_chg": 0.35})
    assert price is None
    assert audit["basis"] == "same_day_return_no_unique_cent"


def test_authoritative_same_day_reference_is_preserved():
    price, audit = _reference_price({
        "close": 2045.0, "pre_close": 2008.33, "pct_chg": 1.826,
        "pre_close_source": "baostock",
    })
    assert price == 2008.33 and audit is None


def test_missing_return_does_not_invent_reference():
    assert _reference_price({"close": 10.0}) == (None, None)


def test_compact_window_retains_columns_after_sparse_first_row():
    first, second = date(2021, 1, 4), date(2021, 1, 5)
    rows = _SymbolBars([
        {"trade_date": first, "trading_status": "SUSPENDED"},
        {"trade_date": second, "close": 10.0, "pre_close": 9.0, "is_st": False},
    ], first, second, None)
    assert rows.on(second)["close"] == 10.0
    assert rows.on(second)["is_st"] is False


def test_year_window_preserves_prior_observation_count():
    rows = _SymbolBars([
        {"trade_date": date(2020, 12, 30), "close": 10.0},
        {"trade_date": date(2020, 12, 31), "close": 11.0},
        {"trade_date": date(2021, 1, 4), "close": 12.1},
    ], date(2021, 1, 1), date(2021, 12, 31), None)
    assert rows.observed_sessions(date(2021, 1, 4)) == 3
