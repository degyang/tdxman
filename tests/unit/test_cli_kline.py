from __future__ import annotations

from unittest.mock import patch

import pandas as pd
from click.testing import CliRunner

from tdxman.cli import cli
from tdxman.commands.security_bars import GetIndexBarsCmd
from tdxman.models.enums import KlineCategory, Market


def test_index_kline_uses_standard_protocol_and_keeps_market_breadth() -> None:
    with (
        patch("tdxman.cli.conn.get_tdx_client") as standard_factory,
        patch("tdxman.cli.conn.get_mac_client") as mac_factory,
    ):
        standard_client = standard_factory.return_value.__enter__.return_value
        standard_client.get_index_bars.return_value = pd.DataFrame(
            [
                {
                    "date": "2026-09-16",
                    "close": 3891.6,
                    "up_count": 1650,
                    "down_count": 608,
                }
            ]
        )

        result = CliRunner().invoke(
            cli, ["kline", "SH", "000001", "--period", "DAILY", "--format", "table"]
        )

    assert result.exit_code == 0, result.output
    assert "up_count" in result.output
    assert "1650" in result.output
    standard_client.get_index_bars.assert_called_once_with(
        Market.SH, "000001", KlineCategory.DAY, start=0, count=800
    )
    mac_factory.assert_not_called()


def test_index_bar_parser_keeps_market_breadth_fields() -> None:
    command = GetIndexBarsCmd(Market.SH, "000001", KlineCategory.DAY, 0, 1)
    body = b"\x01\x00" + b"\x00" * 6 + b"r\x06`\x02"

    with (
        patch("tdxman.commands.security_bars.get_datetime", return_value=(2026, 9, 16, 15, 0, 2)),
        patch(
            "tdxman.commands.security_bars.get_price",
            side_effect=[(3861750, 3), (29850, 4), (32910, 5), (-19030, 6)],
        ),
        patch(
            "tdxman.commands.security_bars.get_volume",
            side_effect=[(459125120, 7), (871141343232, 8)],
        ),
    ):
        bar = command.parse_response(body)[0]

    assert (bar.up_count, bar.down_count) == (1650, 608)
