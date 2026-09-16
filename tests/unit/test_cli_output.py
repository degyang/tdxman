from __future__ import annotations

import json

import pandas as pd

from tdxman.cli.output import format_output


def test_format_output_rounds_price_columns_without_changing_other_floats() -> None:
    df = pd.DataFrame(
        [
            {
                "price": 1271.9000244140625,
                "pre_close": 1272.7500000001,
                "close": 1271.8999999999,
                "change_pct": -0.067891234,
            }
        ]
    )

    record = json.loads(format_output(df))[0]

    assert record == {
        "price": 1271.9,
        "pre_close": 1272.75,
        "close": 1271.9,
        "change_pct": -0.0679,
    }


def test_table_and_csv_render_price_columns_with_two_decimal_places() -> None:
    df = pd.DataFrame([{"price": 1271.9, "close": 1271.0, "change_pct": 1.23456}])

    table = format_output(df, "table")
    csv = format_output(df, "csv")

    assert "1271.90" in table
    assert "1271.00" in table
    assert "1271.90,1271.00,1.2346" in csv


def test_output_formats_quote_metrics_by_field_type() -> None:
    df = pd.DataFrame(
        [
            {
                "vol": 739246.0,
                "amount": 860858752.0,
                "vol_ratio": 1.6189632415771484,
                "turnover": 0.17181703448295593,
                "total_shares": 1940591.875,
            }
        ]
    )

    table = format_output(df, "table")
    record = json.loads(format_output(df))[0]

    assert "1.619" in table
    assert "0.1718" in table
    assert "1940591.875" in table
    assert "860858752.00" in table
    assert record == {
        "vol": 739246,
        "amount": 860858752,
        "vol_ratio": 1.619,
        "turnover": 0.1718,
        "total_shares": 1940591.875,
    }


def test_output_formats_finance_and_fund_flow_amounts_to_two_decimal_places() -> None:
    df = pd.DataFrame([{"zong_zichan": 100.5678, "super_in": 20.1256}])

    table = format_output(df, "table")
    record = json.loads(format_output(df))[0]

    assert "100.57" in table
    assert "20.13" in table
    assert record == {"zong_zichan": 100.57, "super_in": 20.13}
