"""信息查询命令：server-info, symbol-info。"""

from __future__ import annotations

from pathlib import Path

import click

from .help import StandardHelpCommand
from .output import output_options


@click.command("markets", cls=StandardHelpCommand)
@output_options
def markets(output_fmt: str, output_path: Path | None) -> None:
    """列出 A 股市场代码及其含义。"""
    import pandas as pd

    from .output import print_output

    df = pd.DataFrame(
        [
            {"market": "SH", "name": "上海证券交易所", "example": "600519"},
            {"market": "SZ", "name": "深圳证券交易所", "example": "000001"},
            {"market": "BJ", "name": "北京证券交易所", "example": "430047"},
        ]
    )
    fmt = output_fmt
    print_output(df, fmt, output_path, filename="markets")


@click.command("server-info", cls=StandardHelpCommand)
@output_options
def server_info(output_fmt: str, output_path: Path | None) -> None:
    """获取服务器交易时段信息。

    示例：

      tdxman server-info

      tdxman server-info --format table
    """
    from .conn import get_mac_client
    from .output import print_output

    fmt = output_fmt
    with get_mac_client() as client:
        df = client.get_server_info()
    print_output(df, fmt, output_path, filename="server-info")


@click.command("symbol-info", cls=StandardHelpCommand)
@click.argument("symbol_or_market")
@click.argument("code", required=False)
@output_options
def symbol_info(
    symbol_or_market: str,
    code: str | None,
    output_fmt: str,
    output_path: Path | None,
) -> None:
    """获取个股简要特征快照。

    示例（新格式）：

      tdxman symbol-info 000001.SZ

      tdxman symbol-info 600519.SH --format table

    示例（旧格式兼容）：

      tdxman symbol-info SZ 000001

      tdxman symbol-info SH 600519
    """
    from .conn import get_mac_client
    from .output import print_output
    from .parsers import parse_symbol_or_market_code

    fmt = output_fmt
    mkt, parsed_code = parse_symbol_or_market_code(symbol_or_market, code)
    with get_mac_client() as client:
        df = client.get_symbol_info(mkt, parsed_code)
    print_output(df, fmt, output_path, filename=parsed_code)
