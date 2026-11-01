"""Historical normalization equivalence, writer bounds and failed-source preservation."""

import sqlite3

import pandas as pd
import pytest

from aspool import DataPool
from aspool.sqlite_canonical import canonical_operation
from aspool.sqlite_daily_state import SOURCE, has_real_trade, normalize_traded_states
from aspool.sqlite_daily_sync import sync_daily_source
from aspool.sqlite_stock_store import stock_connection
from tests.unit.test_storage_single_authority import canonical  # noqa: F401,F811


def test_normalization_preserves_numeric_features_and_is_noop_on_replay(canonical):  # noqa: F811
    pool = DataPool(canonical)
    before = pool.read_security_daily(start="2026-09-27", end="2026-09-28")
    with stock_connection(canonical, read_only=False) as conn:
        result = normalize_traded_states(
            conn, symbol="000001.SZ", start="2026-09-27", end="2026-09-28"
        )
        assert result["changed_rows"] == result["enriched_verified_rows"] == 2
        assert (
            normalize_traded_states(conn, symbol="000001.SZ", start="2026-09-27", end="2026-09-28")[
                "changed_rows"
            ]
            == 0
        )
    after = pool.read_security_daily(start="2026-09-27", end="2026-09-28")
    omitted = {"trading_status", "trading_status_source", "updated_at"}
    columns = [c for c in before if c not in omitted]
    pd.testing.assert_frame_equal(before[columns], after[columns])
    assert (after.trading_status == "TRADING").all()
    assert (after.trading_status_source == SOURCE).all()


def test_state_writer_rejects_numeric_and_other_store_mutations(canonical):  # noqa: F811
    with stock_connection(canonical, read_only=False) as conn:
        with canonical_operation(conn, "daily_state_normalize"):
            for sql in [
                "UPDATE daily_features SET ma20=123,above_ma20=0",
                "UPDATE daily_bars SET close=123",
            ]:
                conn.execute("BEGIN IMMEDIATE")
                with pytest.raises(sqlite3.OperationalError, match="user-defined"):
                    conn.execute(sql)
                conn.rollback()


@pytest.mark.parametrize("changes", [{"volume": 0}, {"high": 9}, {"close": float("inf")}])
def test_nontrade_or_invalid_data_cannot_prove_trading(changes):
    row = dict(open=10, high=11, low=9, close=10, volume=10, amount=100)
    assert has_real_trade(row)
    assert not has_real_trade(dict(row, **changes))


def test_partial_kline_does_not_turn_existing_real_trade_into_no_trade(canonical):  # noqa: F811
    class Client:
        def get_daily(self, market, code, **kwargs):
            from datetime import date

            return pd.DataFrame(
                [
                    dict(
                        symbol="000001.SZ",
                        date=date(2026, 9, 28),
                        open=10,
                        high=11,
                        low=10,
                        close=11,
                        volume=100,
                        amount=1000,
                        trading_status="TRADING",
                    )
                ]
            )

    sync_daily_source(
        canonical,
        client=Client(),
        symbols=["000001.SZ"],
        market_sessions=["2026-09-27", "2026-09-28"],
        as_of="2026-09-28",
        start="2026-09-27",
        end="2026-09-28",
        source="tdxman:kline",
    )
    with stock_connection(canonical) as conn:
        assert conn.execute(
            "SELECT calc_status,trading_status FROM daily_features WHERE trade_date='2026-09-27'"
        ).fetchone() == ("TRADED", None)


def test_source_failure_preserves_confirmed_trade(canonical):  # noqa: F811
    with stock_connection(canonical, read_only=False) as conn:
        normalize_traded_states(conn, symbol="000001.SZ", start="2026-09-27", end="2026-09-28")

    class Client:
        def get_daily(self, *args, **kwargs):
            raise OSError("timeout")

    sync_daily_source(
        canonical,
        client=Client(),
        symbols=["000001.SZ"],
        market_sessions=["2026-09-27", "2026-09-28"],
        as_of="2026-09-28",
        start="2026-09-27",
        end="2026-09-28",
        source="tdxman:kline",
    )
    with stock_connection(canonical) as conn:
        assert conn.execute("SELECT DISTINCT trading_status FROM daily_features").fetchall() == [
            ("TRADING",)
        ]


def test_final_publication_advances_cache_stamp_and_preserves_summary(canonical):  # noqa: F811
    from aspool.sqlite_daily_state import finalize_state_publication

    with stock_connection(canonical, read_only=False) as conn:
        normalize_traded_states(
            conn, symbol="000001.SZ", start="2026-09-27", end="2026-09-28", publication_stamp=1
        )
        cursor = conn.execute("SELECT * FROM market_daily_summary")
        names = [r[0] for r in cursor.description]
        before = cursor.fetchall()
        result = finalize_state_publication(conn, start="2026-09-27", end="2026-09-28")
        assert result["summary_publication_rows"] == 1
        after = conn.execute("SELECT * FROM market_daily_summary").fetchall()
        stamp = names.index("updated_at")
        for old, new in zip(before, after):
            assert old[:stamp] + old[stamp + 1 :] == new[:stamp] + new[stamp + 1 :]
            assert new[stamp] > old[stamp]


def test_missing_amount_does_not_negate_observed_trading():
    row = dict(open=10, high=11, low=9, close=10, volume=100, amount=None)
    assert has_real_trade(row)
