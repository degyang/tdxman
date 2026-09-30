"""Proof rejection, factor/MA closure, no-op and transactional failure tests."""

from datetime import date, timedelta

import pandas as pd
import pytest

from aspool.sqlite_factor_bootstrap import publish_bootstrap, validate_evidence
from aspool.sqlite_stock_store import stock_connection
from tests.unit.test_storage_single_authority import canonical  # noqa: F401

SYMBOL = "001232.SZ"
DAYS = [(date(2026, 8, 1) + timedelta(days=i)).isoformat() for i in range(25)]


def evidence(*, cash=0.5, symbol=SYMBOL):
    raw = pd.DataFrame(dict(datetime=DAYS, open=10.0, high=10.0, low=10.0, close=10.0))
    qfq, hfq = raw.copy(), raw.copy()
    fields = ["open", "high", "low", "close"]
    qfq.loc[:19, fields] -= cash
    hfq.loc[20:, fields] += cash
    events = [
        dict(date=DAYS[20], category=1, fenhong=cash, songzhuangu=0.0, peigu=0.0, peigujia=0.0)
    ]
    frames = dict(NONE=raw, QFQ=qfq, HFQ=hfq)
    return validate_evidence(symbol, DAYS[0], DAYS[-1], frames, events), frames, events


def prepare(conn, item):
    for day in DAYS:
        conn.execute(
            "INSERT INTO daily_bars(symbol,trade_date,open,high,low,close,volume,"
            "amount,updated_at) "
            "VALUES (?,?,10,10,10,10,100,1000,1)",
            (item["symbol"], day),
        )
        conn.execute(
            "INSERT INTO daily_features(symbol,trade_date,calc_status,limit_status,updated_at) "
            "VALUES (?,?,'TRADED','UNKNOWN',1)",
            (item["symbol"], day),
        )
    conn.commit()


def test_cash_per_share_full_ma_and_repeat_noop(tmp_path):
    item, _, _ = evidence()
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        prepare(conn, item)
        result = publish_bootstrap(conn, [item])
        assert result == dict(symbols=1, factors=2, feature_rows=6, summary_rows=12)
        factor = conn.execute(
            "SELECT cumulative_factor FROM corporate_actions WHERE record_kind='factor' "
            "ORDER BY effective_date DESC LIMIT 1"
        ).fetchone()[0]
        assert factor == pytest.approx(10 / 9.5)
        ma = conn.execute(
            "SELECT ma20 FROM daily_features WHERE trade_date=?", (DAYS[20],)
        ).fetchone()[0]
        assert ma == pytest.approx((19 * 9.5 + 10) / 20)
        before = list(conn.iterdump())
        assert publish_bootstrap(conn, [item])["symbols"] == 0
        assert list(conn.iterdump()) == before


def test_evidence_ignores_prelisting_and_future_but_rejects_unknown_current():
    item, frames, events = evidence()
    outside = [dict(events[0], date="2020-01-01"), dict(events[0], date="2026-10-13")]
    assert validate_evidence(SYMBOL, DAYS[0], DAYS[-1], frames, events + outside) == item
    with pytest.raises(ValueError, match="Unsupported"):
        validate_evidence(
            SYMBOL, DAYS[0], DAYS[-1], frames, events + [dict(events[0], category=15)]
        )
    frames["HFQ"].loc[24, "close"] += 0.01
    with pytest.raises(ValueError, match="HFQ"):
        validate_evidence(SYMBOL, DAYS[0], DAYS[-1], frames, events)


def test_empty_events_alone_never_establish_baseline():
    _, frames, _ = evidence()
    with pytest.raises(ValueError, match="QFQ"):
        validate_evidence(SYMBOL, DAYS[0], DAYS[-1], frames, [])
    _, frames, _ = evidence(cash=0)
    item = validate_evidence(SYMBOL, DAYS[0], DAYS[-1], frames, [])
    assert item["price_events"] == []
    with pytest.raises(ValueError, match="exact current listing"):
        validate_evidence(SYMBOL, DAYS[1], DAYS[-1], frames, [])


def test_raw_difference_rolls_back_entire_batch(tmp_path):
    first, _, _ = evidence()
    second, _, _ = evidence(symbol="001233.SZ")
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        prepare(conn, first)
        prepare(conn, second)
        conn.execute(
            "UPDATE daily_bars SET close=9 WHERE symbol=? AND trade_date=?",
            (second["symbol"], DAYS[0]),
        )
        conn.commit()
        before = list(conn.iterdump())
        with pytest.raises(ValueError, match="local raw history differs"):
            publish_bootstrap(conn, [first, second])
        assert list(conn.iterdump()) == before


def test_summary_failure_rolls_back_factors_and_features(tmp_path, monkeypatch):
    item, _, _ = evidence()

    def fail(*args, **kwargs):
        raise RuntimeError("injected failure")

    monkeypatch.setattr("aspool.sqlite_market_summary.recompute_daily_summary", fail)
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        prepare(conn, item)
        before = list(conn.iterdump())
        with pytest.raises(RuntimeError, match="injected"):
            publish_bootstrap(conn, [item])
        assert list(conn.iterdump()) == before


def test_beijing_contract_keeps_ma_unavailable(tmp_path):
    item, _, _ = evidence(symbol="920001.BJ")
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        prepare(conn, item)
        assert publish_bootstrap(conn, [item])["factors"] == 2
        assert (
            conn.execute("SELECT count(*) FROM daily_features WHERE ma20 IS NOT NULL").fetchone()[0]
            == 0
        )


def test_canonical_bootstrap_recovers_interrupted_publication(canonical, monkeypatch):  # noqa: F811
    from aspool import sqlite_publication as publication
    from aspool.api_contract import DataPoolError
    from aspool.platform_v2 import verify_platform_v2
    from aspool.sqlite_daily_update import apply_daily_changes

    item, _, _ = evidence()
    with stock_connection(canonical, read_only=False) as conn:
        apply_daily_changes(
            conn,
            bars=[
                dict(
                    symbol=SYMBOL,
                    trade_date=day,
                    open=10.0,
                    high=10.0,
                    low=10.0,
                    close=10.0,
                    volume=100.0,
                    amount=1000.0,
                )
                for day in DAYS
            ],
            market_sessions=[*DAYS, "2026-09-27", "2026-09-28"],
        )

    def fail(phase):
        if phase == "after_sqlite_commit":
            raise RuntimeError("injected bootstrap interruption")

    monkeypatch.setattr(publication, "_fault", fail)
    with stock_connection(canonical, read_only=False) as conn:
        with pytest.raises(RuntimeError, match="injected bootstrap"):
            publish_bootstrap(conn, [item])
    with pytest.raises(DataPoolError, match="recover"):
        with stock_connection(canonical):
            pass
    monkeypatch.setattr(publication, "_fault", lambda phase: None)
    assert publication.recover_publication(canonical)["recovered"]
    assert verify_platform_v2(canonical)["ready"]
    with stock_connection(canonical, read_only=False) as conn:
        assert publish_bootstrap(conn, [item])["symbols"] == 0
        assert conn.execute(
            "SELECT ma20 FROM daily_features WHERE symbol=? AND trade_date=?", (SYMBOL, DAYS[20])
        ).fetchone()[0] == pytest.approx(9.525)


def test_daily_discovery_initializes_new_listing_and_skips_network_on_repeat(tmp_path):
    import duckdb

    from aspool.sqlite_factor_bootstrap import bootstrap_factors

    item, _, _ = evidence()
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        prepare(conn, item)
    with duckdb.connect(str(tmp_path / "catalog.duckdb")) as conn:
        conn.execute("CREATE TABLE security_calendar(trade_date DATE,is_open BOOLEAN)")
        conn.execute("INSERT INTO security_calendar VALUES (?,true)", [DAYS[-1]])
        conn.execute(
            "CREATE TABLE securities(symbol VARCHAR,listing_date DATE,"
            "delisting_date DATE,active BOOLEAN,asset_type VARCHAR)"
        )
        conn.execute("INSERT INTO securities VALUES (?,?,NULL,true,'stock')", [SYMBOL, DAYS[0]])
    calls = []

    def fetch(symbol, listing, target):
        calls.append((symbol, listing, target))
        return item

    assert bootstrap_factors(tmp_path, fetcher=fetch)["symbols"] == 1
    assert len(calls) == 1
    assert bootstrap_factors(tmp_path, fetcher=fetch)["symbols"] == 0
    assert len(calls) == 1


def test_daily_discovery_defers_old_history_without_fetching(tmp_path):
    import duckdb

    from aspool.sqlite_factor_bootstrap import bootstrap_factors

    item, _, _ = evidence()
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        prepare(conn, item)
        for offset in range(25, 61):
            day = (date.fromisoformat(DAYS[0]) + timedelta(days=offset)).isoformat()
            conn.execute(
                "INSERT INTO daily_bars(symbol,trade_date,updated_at) VALUES (?,?,1)", [SYMBOL, day]
            )
        conn.commit()
    with duckdb.connect(str(tmp_path / "catalog.duckdb")) as conn:
        conn.execute("CREATE TABLE security_calendar(trade_date DATE,is_open BOOLEAN)")
        conn.execute("INSERT INTO security_calendar VALUES ('2026-09-30',true)")
        conn.execute(
            "CREATE TABLE securities(symbol VARCHAR,listing_date DATE,"
            "delisting_date DATE,active BOOLEAN,asset_type VARCHAR)"
        )
        conn.execute("INSERT INTO securities VALUES (?,?,NULL,true,'stock')", [SYMBOL, DAYS[0]])

    def fail(*args):
        raise AssertionError("Deferred history must not trigger network calls")

    result = bootstrap_factors(tmp_path, fetcher=fail)
    assert result["symbols"] == 0
    assert result["deferred"][0]["symbol"] == SYMBOL


def test_existing_period_summary_prevents_partial_publication(tmp_path):
    from aspool.api_contract import DataPoolError

    item, _, _ = evidence()
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        prepare(conn, item)
        conn.execute(
            "INSERT INTO market_daily_summary(frequency,period_key,scope,period_start,period_end,"
            "as_of,session_count,trading_count,updated_at) VALUES "
            "('W','2026-08','all_stocks',?,?,?,1,1,1)",
            (DAYS[0], DAYS[-1], DAYS[-1]),
        )
        conn.commit()
        before = list(conn.iterdump())
        with pytest.raises(DataPoolError, match="period writer"):
            publish_bootstrap(conn, [item])
        assert list(conn.iterdump()) == before


def test_initialized_chain_supports_daily_event_extension_and_repeat(tmp_path):
    from aspool.sqlite_daily_update import apply_daily_changes

    item, _, _ = evidence()
    next_day = (date.fromisoformat(DAYS[-1]) + timedelta(days=1)).isoformat()
    bar = dict(
        symbol=SYMBOL,
        trade_date=next_day,
        open=10.0,
        high=10.0,
        low=10.0,
        close=10.0,
        volume=100.0,
        amount=1000.0,
    )
    extension = dict(
        symbol=SYMBOL,
        verified_start=next_day,
        verified_end=next_day,
        events=[
            dict(
                effective_date=next_day,
                category=1,
                source="tdx:xdxr",
                source_key="category=1:slot=1",
                cash_dividend_per_share=0.25,
                bonus_shares_per_share=0.0,
                rights_shares_per_share=0.0,
                rights_price=0.0,
            )
        ],
    )
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        prepare(conn, item)
        publish_bootstrap(conn, [item])
        result = apply_daily_changes(
            conn, bars=[bar], factor_extensions=[extension], market_sessions=[*DAYS, next_day]
        )
        assert result["changed_factor_rows"] > 0
        assert conn.execute(
            "SELECT cumulative_factor FROM corporate_actions WHERE record_kind='factor' "
            "AND effective_date=?",
            [next_day],
        ).fetchone()[0] == pytest.approx(10 / 9.5 * 10 / 9.75)
        before = list(conn.iterdump())
        repeat = apply_daily_changes(
            conn, bars=[bar], factor_extensions=[extension], market_sessions=[*DAYS, next_day]
        )
        assert repeat["changed_factor_rows"] == repeat["changed_feature_rows"] == 0
        assert list(conn.iterdump()) == before
