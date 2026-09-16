"""Financial CLI command routing."""

import json
from unittest.mock import patch

import pandas as pd
from click.testing import CliRunner

from tdxman.cli import cli
from tdxman.exceptions import TdxDecodeError
from tdxman.models.enums import Market


def test_finance_routes_to_standard_client() -> None:
    with patch("tdxman.cli.conn.get_tdx_client") as factory:
        client = factory.return_value.__enter__.return_value
        client.get_finance_info.return_value = pd.DataFrame(
            [{"code": "600519", "jing_zichan": 1.0}]
        )

        result = CliRunner().invoke(cli, ["finance", "SH", "600519"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == [{"code": "600519", "jing_zichan": 1.0}]
    client.get_finance_info.assert_called_once_with(Market.SH, "600519")


def test_fund_flow_routes_to_standard_client_with_output_options() -> None:
    with patch("tdxman.cli.conn.get_tdx_client") as factory:
        client = factory.return_value.__enter__.return_value
        client.get_history_fund_flow.return_value = pd.DataFrame(
            [{"date": "2026-09-15", "super_in": 100.0}]
        )

        result = CliRunner().invoke(
            cli,
            ["fund-flow", "SZ", "000001", "--start", "2", "--count", "10", "--format", "table"],
        )

    assert result.exit_code == 0, result.output
    assert "super_in" in result.output
    client.get_history_fund_flow.assert_called_once_with(Market.SZ, "000001", 2, 10)


def test_fund_flow_reports_protocol_errors_without_a_traceback() -> None:
    with patch("tdxman.cli.conn.get_tdx_client") as factory:
        client = factory.return_value.__enter__.return_value
        client.get_history_fund_flow.side_effect = TdxDecodeError("响应不完整")

        result = CliRunner().invoke(cli, ["fund-flow", "SH", "600519"])

    assert result.exit_code == 1
    assert "获取历史资金流向失败：响应不完整" in result.output
    assert "Traceback" not in result.output
