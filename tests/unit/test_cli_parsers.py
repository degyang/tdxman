"""CLI parameter parser tests."""

import click
import pytest
from click.testing import CliRunner

from tdxman.cli import cli
from tdxman.cli.parsers import (
    parse_adjust,
    parse_board_type,
    parse_category,
    parse_ex_market,
    parse_market,
    parse_period,
    parse_sort_order,
    parse_sort_type,
)
from tdxman.mac.enums import BoardType


def test_parse_board_type_supports_second_level_industry() -> None:
    assert parse_board_type("HY2") is BoardType.HY2
    assert parse_board_type("industry2") is BoardType.HY2


@pytest.mark.parametrize(
    "parser, value",
    [
        (parse_market, "S"),
        (parse_period, "invalid"),
        (parse_adjust, "adjusted"),
        (parse_ex_market, "unknown-market"),
        (parse_category, "unknown-category"),
        (parse_sort_type, "unknown-sort"),
        (parse_sort_order, "sideways"),
    ],
)
def test_invalid_parser_values_raise_click_parameter_errors(parser, value) -> None:
    with pytest.raises(click.BadParameter):
        parser(value)


def test_quote_rejects_invalid_market_without_traceback() -> None:
    result = CliRunner().invoke(cli, ["quote", "S 000001", "--format", "table"])
    assert result.exit_code == 2
    assert "Traceback" not in result.output
    assert "市场代码应为 SH/SZ/BJ 或 0/1/2" in result.output


@pytest.mark.parametrize("value", ["3", "S"])
def test_parse_market_rejects_unknown_market(value) -> None:
    with pytest.raises(click.BadParameter):
        parse_market(value)


@pytest.mark.parametrize(
    "stocks",
    [
        "SZ 000001 extra",
        "SZ 00001",
        "SZ",
        "",
    ],
)
def test_quote_rejects_malformed_stock_list_without_traceback(stocks) -> None:
    result = CliRunner().invoke(cli, ["quote", stocks])
    assert result.exit_code == 2
    assert "Traceback" not in result.output
