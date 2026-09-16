"""Market statistics CLI routing, mapping, and failure handling."""

import json
from unittest.mock import patch

import pandas as pd
import pytest
from click.testing import CliRunner

from tdxman.cli import cli
from tdxman.exceptions import TdxConnectionError
from tdxman.models.enums import Market


@pytest.fixture
def quotes():
    # Deliberately shuffled: response order must not determine the mapping.
    return pd.DataFrame(
        [
            {"market": 1, "code": "880006", "close": 65, "open": 4},
            {
                "market": 1,
                "code": "880005",
                "close": 4228,
                "open": 1150,
                "low": 172,
                "high": 5565,
                "amount": 1.2e12,
                "vol": 635407152,
            },
            {"market": 1, "code": "880001", "close": 11329.33},
        ]
    )


def invoke(quotes, *args):
    with patch("tdxman.cli.conn.get_mac_client") as factory:
        client = factory.return_value.__enter__.return_value
        client.get_stock_quotes.return_value = quotes
        result = CliRunner().invoke(cli, ["market-stat", *args])
        client.get_stock_quotes.assert_called_once_with(
            [(Market.SH, "880005"), (Market.SH, "880001"), (Market.SH, "880006")]
        )
    return result


def test_market_stat_mac_mapping(quotes):
    result = invoke(quotes)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == [
        {
            "up_count": 4228,
            "down_count": 1150,
            "neutral_count": 172,
            "suspended_count": 15,
            "total_count": 5565,
            "total_amount": 1.2e12,
            "total_volume": 635407152,
            "total_market_cap": 11329.33 * 1e10,
            "limit_up_count": 65,
            "limit_down_count": 4,
        }
    ]


@pytest.mark.parametrize("args", [("--format", "table",), ("--format", "csv")])
def test_market_stat_formats(quotes, args):
    result = invoke(quotes, *args)
    assert result.exit_code == 0, result.output
    assert "up_count" in result.output
    assert "4228" in result.output


@pytest.mark.parametrize("case", ["empty", "missing_code", "duplicate", "missing_field", "nan"])
def test_market_stat_incomplete_data(quotes, case):
    if case == "empty":
        quotes = pd.DataFrame()
    elif case == "missing_code":
        quotes = quotes[quotes.code != "880006"]
    elif case == "duplicate":
        quotes = pd.concat([quotes, quotes.iloc[:1]])
    elif case == "missing_field":
        quotes = quotes.drop(columns="close")
    else:
        quotes.loc[quotes.code == "880005", "close"] = float("nan")
    result = invoke(quotes)
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "Traceback" not in result.output


def test_market_stat_connection_error():
    with patch("tdxman.cli.conn.get_mac_client", side_effect=TdxConnectionError("离线")):
        result = CliRunner().invoke(cli, ["market-stat"])
    assert result.exit_code == 1
    assert "无法获取市场统计数据：离线" in result.output
    assert "Traceback" not in result.output
