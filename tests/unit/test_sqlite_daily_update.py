"""Actual incremental and missing-bar repair transactions on the stock store."""

from datetime import date, timedelta

import pytest

from aspool.api_contract import DataPoolError
from aspool.sqlite_daily_update import apply_daily_changes, daily_window
from aspool.sqlite_market_summary import recompute_market_window
from aspool.sqlite_stock_store import stock_connection

SYMBOL = "000001.SZ"
DAYS = [(date(2024, 1, 1) + timedelta(days=i)).isoformat() for i in range(65)]
AGES = {SYMBOL: {day: 100 + i for i, day in enumerate(DAYS)}}


def row(day, close=10, **fields):
    return dict(
        symbol=SYMBOL,
        trade_date=day,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=100,
        amount=1000,
        **fields,
    )


def prepare(conn, missing=()):
    for day in DAYS[:60]:
        if day in missing:
            continue
        values = row(day)
        conn.execute(
            "INSERT INTO daily_bars ("
            + ",".join(values)
            + ",updated_at) VALUES ("
            + ",".join("?" for _ in values)
            + ",1)",
            tuple(values.values()),
        )
        conn.execute(
            "INSERT INTO daily_features(symbol,trade_date,pre_close,is_st,source_is_st,"
            "source_is_st_source,calc_status,updated_at) VALUES (?,?,10,0,0,'baostock','TRADED',1)",
            (SYMBOL, day),
        )
    conn.execute(
        "INSERT INTO corporate_actions(symbol,effective_date,record_kind,source,source_key,"
        "cumulative_factor,valid_from,valid_through,factor_basis,updated_at) "
        "VALUES (?,?,'factor','test','selected',1,?,?,'test verified interval',1)",
        (SYMBOL, DAYS[0], DAYS[0], DAYS[-1]),
    )
    recompute_market_window(conn, symbols=[SYMBOL], market_sessions=DAYS[:60], listed_days=AGES)
    conn.commit()


def apply(conn, **kwargs):
    return apply_daily_changes(conn, market_sessions=DAYS, listed_days=AGES, **kwargs)


def dump(conn):
    return list(conn.iterdump())


def test_default_window_and_explicit_finite_repair():
    assert daily_window(DAYS, as_of=DAYS[40]) == DAYS[36:41]
    assert daily_window(DAYS, as_of=DAYS[-1], start=DAYS[10], end=DAYS[12]) == DAYS[10:13]
    with pytest.raises(ValueError):
        daily_window(DAYS, as_of=DAYS[-1], start=DAYS[10])
    with pytest.raises(DataPoolError):
        daily_window(DAYS, as_of=DAYS[-1], start=DAYS[0], end=DAYS[-1])


def test_noop_and_amount_only_never_recompute_history(tmp_path):
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        prepare(conn)
        before = dump(conn)
        result = apply(conn, bars=[row(DAYS[25])])
        assert result["changed_rows"] == result["recomputed_feature_rows"] == 0
        assert dump(conn) == before
        features = conn.execute("SELECT * FROM daily_features").fetchall()
        changed = apply(conn, bars=[dict(symbol=SYMBOL, trade_date=DAYS[25], amount=2000)])
        assert changed["affected_sessions"] == [DAYS[25]]
        assert changed["recomputed_feature_rows"] == 0
        assert changed["summary_rows"] == 2
        assert conn.execute("SELECT * FROM daily_features").fetchall() == features
        assert (
            conn.execute(
                "SELECT amount_sum FROM market_daily_summary WHERE period_key=?", (DAYS[25],)
            ).fetchone()[0]
            == 2000
        )
        assert apply(conn, bars=[])["changed_rows"] == 0


def test_backfill_updates_next_reference_ma20_streak_and_summary_together(tmp_path):
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        prepare(conn, missing=[DAYS[25]])
        before = conn.execute(
            "SELECT * FROM daily_features WHERE trade_date<?", (DAYS[25],)
        ).fetchall()
        result = apply(
            conn,
            bars=[row(DAYS[25], close=11)],
            dated_facts=[
                dict(
                    symbol=SYMBOL,
                    trade_date=DAYS[25],
                    source_is_st=0,
                    source_is_st_source="baostock",
                )
            ],
        )
        assert result["changed_rows"] == 2
        assert result["affected_sessions"][0] == DAYS[25]
        assert result["affected_sessions"][-1] == DAYS[44]
        assert result["recomputed_feature_rows"] < 30
        current = conn.execute(
            "SELECT pre_close,close_limit_up,consecutive_up FROM daily_features WHERE trade_date=?",
            (DAYS[25],),
        ).fetchone()
        assert current == (10, 1, 1)
        following = conn.execute(
            "SELECT pre_close,consecutive_up,ma20 FROM daily_features WHERE trade_date=?",
            (DAYS[26],),
        ).fetchone()
        assert following == (11, 0, 10.05)
        assert (
            conn.execute("SELECT * FROM daily_features WHERE trade_date<?", (DAYS[25],)).fetchall()
            == before
        )
        again = apply(conn, bars=[row(DAYS[25], close=11)])
        assert again["changed_rows"] == 0


def test_reference_withdrawal_and_invalid_bar_do_not_violate_known_constraints(tmp_path):
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        prepare(conn)
        apply(
            conn,
            dated_facts=[
                dict(
                    symbol=SYMBOL,
                    trade_date=DAYS[25],
                    source_pre_close=9,
                    source_pre_close_source="baostock",
                )
            ],
        )
        apply(conn, dated_facts=[dict(symbol=SYMBOL, trade_date=DAYS[25], source_pre_close=None)])
        assert (
            conn.execute(
                "SELECT pre_close FROM daily_features WHERE trade_date=?", (DAYS[25],)
            ).fetchone()[0]
            == 10
        )
        apply(conn, bars=[dict(symbol=SYMBOL, trade_date=DAYS[25], open=0)])
        assert (
            conn.execute(
                "SELECT calc_status FROM daily_features WHERE trade_date=?", (DAYS[25],)
            ).fetchone()[0]
            == "INVALID"
        )
        assert (
            conn.execute(
                "SELECT pre_close FROM daily_features WHERE trade_date=?", (DAYS[26],)
            ).fetchone()[0]
            == 10
        )


def test_failed_summary_rolls_back_source_and_every_derived_write(tmp_path):
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        prepare(conn)
        conn.execute(
            "INSERT INTO daily_features(symbol,trade_date,calc_status,updated_at) "
            "VALUES ('000002.SZ',?,'TRADED',1)",
            (DAYS[25],),
        )
        conn.commit()
        before = dump(conn)
        with pytest.raises(DataPoolError, match="not been computed"):
            apply(conn, bars=[row(DAYS[25], close=11)])
        assert dump(conn) == before
        assert not conn.in_transaction


def test_source_conflict_and_out_of_budget_leave_no_changes(tmp_path):
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        prepare(conn)
        before = dump(conn)
        with pytest.raises(DataPoolError, match="Conflicting dated"):
            apply(
                conn,
                bars=[row(DAYS[25], close=11)],
                dated_facts=[
                    dict(
                        symbol=SYMBOL,
                        trade_date=DAYS[25],
                        source_is_st=1,
                        source_is_st_source="other",
                    )
                ],
            )
        assert dump(conn) == before

        with pytest.raises(DataPoolError, match="session budget"):
            apply(conn, bars=[row(day) for day in DAYS[:11]])
        assert dump(conn) == before


def test_source_sync_fetches_only_window_retains_failed_and_empty_and_is_idempotent(tmp_path):
    import pandas as pd

    from aspool.sqlite_daily_sync import sync_baostock_daily

    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        prepare(conn)

    class Client:
        calls = []

        def get_daily(self, market, code, **kwargs):
            self.calls.append((code, kwargs))
            assert kwargs == dict(
                start=date.fromisoformat(DAYS[60]), end=date.fromisoformat(DAYS[64]), count=None
            )
            if code == "000002":
                return pd.DataFrame()
            if code == "000003":
                raise TimeoutError("source timeout")
            value = row(DAYS[60])
            value.pop("trade_date")
            return pd.DataFrame(
                [
                    dict(
                        value,
                        date=date.fromisoformat(DAYS[60]),
                        pre_close=10,
                        is_st=False,
                        trading_status="TRADING",
                        turnover_rate=0.5,
                        pct_chg=0,
                        pe_ttm=float("nan"),
                        pb=1,
                    )
                ]
            )

    client = Client()
    result = sync_baostock_daily(
        tmp_path,
        client=client,
        symbols=[SYMBOL, "000002.SZ", "000003.SZ", "430001.BJ"],
        market_sessions=DAYS,
        as_of=DAYS[-1],
        listed_days=AGES,
    )
    assert result["success"][0]["changed_rows"] == 2
    assert result["empty"] == ["000002.SZ"]
    assert result["failed"][0]["symbol"] == "000003.SZ"
    assert result["unsupported"] == ["430001.BJ"]
    assert len(client.calls) == 3
    again = sync_baostock_daily(
        tmp_path,
        client=client,
        symbols=[SYMBOL],
        market_sessions=DAYS,
        as_of=DAYS[-1],
        listed_days=AGES,
    )
    assert again["success"][0]["changed_rows"] == 0


def test_conflicting_duplicate_provenance_cannot_be_hidden_by_keep_last(tmp_path):
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        key = dict(symbol=SYMBOL, trade_date=DAYS[25])
        with pytest.raises(DataPoolError, match="Conflicting dated source inputs"):
            apply(
                conn,
                dated_facts=[
                    dict(key, source_pre_close=10, source_pre_close_source="provider_a"),
                    dict(key, source_pre_close=11, source_pre_close_source="provider_b"),
                ],
            )
        with pytest.raises(ValueError, match="Computed values"):
            apply(
                conn,
                dated_facts=[
                    dict(key, source_pre_close=10, source_pre_close_source="derived:previous_close")
                ],
            )
        assert conn.execute("SELECT count(*) FROM daily_features").fetchone()[0] == 0
        assert not conn.in_transaction


def test_reference_recalculation_preserves_future_timestamp_floor(tmp_path):
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        prepare(conn)
        future = 8_000_000_000_000_000
        conn.execute(
            "UPDATE daily_features SET updated_at=? WHERE trade_date=?", (future, DAYS[25])
        )
        conn.commit()
        apply(
            conn,
            dated_facts=[
                dict(
                    symbol=SYMBOL,
                    trade_date=DAYS[25],
                    source_pre_close=9,
                    source_pre_close_source="baostock",
                )
            ],
        )
        assert (
            conn.execute(
                "SELECT updated_at FROM daily_features WHERE trade_date=?", (DAYS[25],)
            ).fetchone()[0]
            > future
        )
        assert (
            conn.execute(
                "SELECT min(updated_at) FROM market_daily_summary WHERE period_key=?", (DAYS[25],)
            ).fetchone()[0]
            > future
        )


def test_event_based_factor_dependency_cannot_be_committed_stale(tmp_path):
    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        prepare(conn)
        conn.execute(
            "INSERT INTO corporate_actions(symbol,effective_date,record_kind,source,source_key,"
            "cumulative_factor,valid_from,valid_through,factor_basis,updated_at) "
            "VALUES (?,?,'factor','derived:event_chain','selected',1.1,?,?,'event',1)",
            (SYMBOL, DAYS[26], DAYS[26], DAYS[-1]),
        )
        conn.commit()
        before = dump(conn)
        with pytest.raises(DataPoolError, match="event reference"):
            apply(conn, bars=[row(DAYS[25], close=11)])
        assert dump(conn) == before


def test_market_source_sync_aggregates_a_changed_date_once_for_multiple_stocks(tmp_path):
    import pandas as pd

    from aspool.sqlite_daily_sync import sync_baostock_daily

    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        pass

    class Client:
        def get_daily(self, market, code, **kwargs):
            item = row(DAYS[-1])
            item.pop("trade_date")
            item["symbol"] = f"{code}.SZ"
            return pd.DataFrame(
                [
                    dict(
                        item,
                        date=date.fromisoformat(DAYS[-1]),
                        pre_close=10,
                        is_st=False,
                        trading_status="TRADING",
                        turnover_rate=1,
                        pct_chg=0,
                        pe_ttm=None,
                        pb=None,
                    )
                ]
            )

    result = sync_baostock_daily(
        tmp_path,
        client=Client(),
        symbols=[SYMBOL, "000002.SZ"],
        market_sessions=DAYS,
        as_of=DAYS[-1],
    )
    assert len(result["success"]) == 1
    assert result["success"][0]["symbols"] == [SYMBOL, "000002.SZ"]
    assert result["success"][0]["summary_rows"] == 2
    with stock_connection(tmp_path) as conn:
        assert (
            conn.execute(
                "SELECT trading_count FROM market_daily_summary WHERE scope='all_stocks'"
            ).fetchone()[0]
            == 2
        )


def test_writer_deadline_rolls_back_source_writes(tmp_path, monkeypatch):
    import aspool.sqlite_daily_update as writer

    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        # The clock advances after the first actual database mutation, so this
        # exercises rollback after work, not only input validation.
        monkeypatch.setattr(writer.time, "monotonic", lambda: 31 if conn.total_changes else 0)
        inputs = [dict(row(DAYS[-1]), symbol=f"{i:06d}.SZ") for i in range(1000)]
        with pytest.raises(DataPoolError) as error:
            apply(conn, bars=inputs)
        assert error.value.code == "LOCAL_UPDATE_BUDGET_EXCEEDED"
        assert conn.total_changes > 0
        assert conn.execute("SELECT count(*) FROM daily_bars").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM daily_features").fetchone()[0] == 0
        assert not conn.in_transaction


def test_old_suspension_turnover_conflict_can_be_repaired_by_its_source(tmp_path):
    from aspool.sqlite_daily_derived import DERIVED_COLUMNS

    with stock_connection(tmp_path, create=True, read_only=False) as conn:
        prepare(conn)
        invalid = dict.fromkeys(DERIVED_COLUMNS)
        invalid.update(calc_status="INVALID", limit_status="INVALID", streak_known=0)
        conn.execute(
            "UPDATE daily_features SET trading_status='SUSPENDED',"
            "trading_status_source='baostock',"
            + ",".join(k + "=?" for k in invalid)
            + " WHERE trade_date=?",
            (*invalid.values(), DAYS[25]),
        )
        conn.commit()
        apply(
            conn,
            dated_facts=[
                dict(
                    symbol=SYMBOL,
                    trade_date=DAYS[25],
                    trading_status="TRADING",
                    trading_status_source="baostock",
                )
            ],
        )
        assert conn.execute(
            "SELECT calc_status,limit_status FROM daily_features WHERE trade_date=?", (DAYS[25],)
        ).fetchone() == ("TRADED", "KNOWN")
