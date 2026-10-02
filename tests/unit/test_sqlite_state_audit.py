"""Audit semantics and a real three-store recovery readback."""

import json

import pytest

from aspool.sqlite_state_audit import issues, prepare_recovery, scan
from aspool.sqlite_stock_store import stock_connection
from tests.unit.test_storage_single_authority import canonical  # noqa: F401


def test_missing_bar_cannot_establish_no_trade():
    assert issues(dict(trading_status="TRADING", calc_status="NO_TRADE")) == [
        "trading_calc_mismatch",
        "missing_bar",
    ]
    assert issues(dict(trading_status="SUSPENDED", calc_status="NO_TRADE")) == []


def test_positive_turnover_and_suspension_are_reported_as_conflicting():
    row = dict(
        open=10,
        high=11,
        low=9,
        close=10,
        volume=100,
        amount=1000,
        bar_date="2026-09-28",
        trading_status="SUSPENDED",
        calc_status="NO_TRADE",
    )
    assert issues(row) == ["status_conflicts_with_trade"]
    row["trading_status"] = None
    assert issues(row) == ["missing_trading_status"]


def test_three_store_recovery_is_verified_and_existing_destination_is_rejected(canonical, tmp_path):  # noqa: F811
    point = tmp_path / "new-recovery"
    report = prepare_recovery(canonical, point)
    assert report["public_read_verified"]
    assert {r["name"] for r in report["files"]} == {
        "stocks.sqlite",
        "features.sqlite",
        "adjustments.sqlite",
    }
    assert json.loads((point / "recovery.json").read_text()) == report
    with pytest.raises(FileExistsError):
        prepare_recovery(canonical, point)
    with stock_connection(point) as conn:
        assert len(list(scan(conn, "000001.SZ", "2026-09-27", "2026-09-28"))) == 2


def test_source_repair_publishes_dated_shares_and_derived_valuation(canonical, tmp_path):  # noqa: F811
    from datetime import datetime

    import pandas as pd

    from aspool.sqlite_state_audit import source_repair

    class Client:
        def get_stock_kline(self, *args, **kwargs):
            assert kwargs["count"] == 3000
            return pd.DataFrame(
                [
                    dict(
                        datetime=datetime(2026, 9, 28),
                        open=10,
                        high=11,
                        low=10,
                        close=11,
                        vol=100,
                        amount=1000,
                        float_shares=2,
                    )
                ]
            )

    with stock_connection(canonical, read_only=False) as conn:
        results = source_repair(
            conn,
            [dict(symbol="000001.SZ", trade_date="2026-09-28")],
            client=Client(),
            evidence=tmp_path,
            sessions=["2026-09-27", "2026-09-28"],
        )
        assert results[0]["result"] == "repaired"
        row = conn.execute(
            "SELECT float_share,float_share_source,float_mv,float_mv_source,"
            "turnover_rate,turnover_rate_source FROM daily_bars "
            "WHERE trade_date='2026-09-28'"
        ).fetchone()
        assert row[:4] == (20000, "tdxman:kline", 220000, "derived:raw_close*tdxman:kline")
        assert row[4] == 0.5
        assert row[5] == "derived:dated_float_share"
