"""Dated shares survive quote conversion and feed valuation without historical guesses."""

from datetime import date

import pytest

from aspool.fundamentals import _quote_bar
from aspool.sqlite_daily_update import valuation_patch


def test_source_shares_are_converted_and_zero_is_not_missing():
    row = _quote_bar(
        dict(code="000001", close=10, total_shares=2, float_shares=0), date(2026, 9, 30)
    )
    assert row["total_share"] == 20000
    assert row["float_share"] == 0
    assert row["float_share_source"] == "tdxman:quote"
    patch = valuation_patch(row, {"close", "total_share", "float_share"})
    assert patch["total_mv"] == 200000
    assert patch["float_mv"] == 0
    assert patch["total_mv_source"] == "derived:raw_close*tdxman:quote"


def test_unknown_shares_do_not_borrow_latest_financial_values():
    row = _quote_bar(dict(code="000001", close=10), date(2026, 9, 30))
    assert "total_share" not in row
    assert valuation_patch(row, {"close"}) == {}
    row.update(float_share=None, float_mv=100, float_mv_source="derived:old")
    assert valuation_patch(row, {"float_share"}) == dict(float_mv=None, float_mv_source=None)


@pytest.mark.parametrize("value", [-1, float("inf"), True])
def test_invalid_dated_shares_rejected(value):
    with pytest.raises(ValueError):
        valuation_patch(
            dict(close=10, total_share=value, total_share_source="source"), {"total_share"}
        )


def test_same_date_close_revision_updates_values():
    row = dict(close=11, total_share=100, total_share_source="source")
    assert valuation_patch(row, {"close"})["total_mv"] == 1100
    assert valuation_patch(row, {"amount"}) == {}


def test_quote_to_canonical_writer_keeps_both_share_counts(tmp_path, monkeypatch):
    import pandas as pd

    from aspool.sqlite_stock_store import stock_connection
    from aspool.sqlite_update_cli import run_update
    from tdxman.mac.client import MacClient
    from tests.unit.test_sqlite_update_cli import NOW, FakeMac, quote, store

    store(tmp_path)

    class Shares(FakeMac):
        def get_stock_quotes(self, stocks, fields):
            return pd.DataFrame(
                [quote(code, total_shares=4, float_shares=2) for market, code in stocks]
            )

    monkeypatch.setattr(MacClient, "from_best_host", lambda **kwargs: Shares())
    result = run_update(tmp_path, symbols=["000001.SZ"], now=NOW, retry_delay=0)
    assert result["status"] == "ok"
    with stock_connection(tmp_path) as c:
        row = c.execute(
            "SELECT close,total_share,float_share,total_mv,float_mv,total_mv_source "
            "FROM daily_bars WHERE symbol='000001.SZ' AND trade_date='2026-09-28'"
        ).fetchone()
    assert row[1:3] == (40000, 20000)
    assert row[3:5] == (row[0] * 40000, row[0] * 20000)
    assert row[5] == "derived:raw_close*tdxman:quote"
    repeat = run_update(tmp_path, symbols=["000001.SZ"], now=NOW, retry_delay=0)
    assert repeat["success"][0]["changed_rows"] == 0
