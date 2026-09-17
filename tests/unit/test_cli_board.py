from unittest.mock import patch

import pandas as pd
import pytest
from click.testing import CliRunner

from tdxman.cli import cli
from tdxman.mac.enums import BoardType, Category, SortOrder, SortType


def test_board_list_zs_returns_standard_index_directory():
    quotes = pd.DataFrame([{"market": 1, "code": "880301", "name": "煤炭"}])
    with patch("tdxman.cli.conn.get_mac_client") as factory:
        client = factory.return_value.__enter__.return_value
        client.get_stock_quotes_list.return_value = quotes
        result = CliRunner().invoke(cli, ["board-list", "--type", "ZS", "--count", "600"])
    assert result.exit_code == 0, result.output
    client.get_stock_quotes_list.assert_called_once_with(
        category=Category.ZS,
        count=600,
        sort_type=SortType.CODE,
        sort_order=SortOrder.ASC,
    )
    assert "880301" in result.output


def test_board_list_rejects_unknown_type_without_traceback():
    result = CliRunner().invoke(cli, ["board-list", "--type", "not-a-type"])
    assert result.exit_code == 2
    assert "Traceback" not in result.output
    assert "ZS" in result.output


def test_board_list_help_orders_options_type_reference_and_examples():
    result = CliRunner().invoke(cli, ["board-list", "--help"])
    assert result.exit_code == 0, result.output
    options = result.output.index("Options:")
    reference = result.output.index("--type 类型对照:")
    examples = result.output.index("示例:")
    assert options < reference < examples
    assert "YJ_LEVEL1/2/3" in result.output
    assert "tdxman board-list --type ZS --count 600 --format table" in result.output


@pytest.mark.parametrize(
    ("board_type", "expected"),
    [
        ("OTHER", BoardType.OTHER),
        ("YJ_LEVEL1", BoardType.YJ_LEVEL1),
        ("YJ_LEVEL2", BoardType.YJ_LEVEL2),
        ("YJ_LEVEL3", BoardType.YJ_LEVEL3),
    ],
)
def test_board_list_supports_documented_named_board_types(board_type, expected):
    with patch("tdxman.cli.conn.get_mac_client") as factory:
        client = factory.return_value.__enter__.return_value
        client.get_board_list.return_value = pd.DataFrame()
        result = CliRunner().invoke(cli, ["board-list", "--type", board_type])
    assert result.exit_code == 0, result.output
    client.get_board_list.assert_called_once_with(board_type=expected, count=10000)


def test_quote_list_help_orders_options_category_reference_and_examples():
    result = CliRunner().invoke(cli, ["quote-list", "--help"])
    assert result.exit_code == 0, result.output
    options = result.output.index("Options:")
    reference = result.output.index("CATEGORY 类型对照:")
    examples = result.output.index("示例:")
    assert options < reference < examples
    assert "FXJS" in result.output
    assert "tdxman quote-list ZS --count 600 --format table" in result.output
