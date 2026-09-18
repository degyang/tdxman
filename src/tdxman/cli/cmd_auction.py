"""集合竞价命令。"""

from __future__ import annotations

from pathlib import Path

import click

from .help import StandardHelpCommand
from .output import output_options


@click.command(cls=StandardHelpCommand)
@click.argument("symbol_or_market")
@click.argument("code", required=False)
@output_options
def auction(
    symbol_or_market: str,
    code: str | None,
    output_fmt: str,
    output_path: Path | None,
) -> None:
    """获取集合竞价数据。

    示例（新格式）：

      tdxman auction 000001.SZ

      tdxman auction 600519.SH --format table

    示例（旧格式兼容）：

      tdxman auction SZ 000001

      tdxman auction SH 600519
    """
    from .conn import get_mac_client
    from .output import print_output
    from .parsers import parse_symbol_or_market_code

    fmt = output_fmt
    mkt, parsed_code = parse_symbol_or_market_code(symbol_or_market, code)
    with get_mac_client() as client:
        df = client.get_auction(mkt, parsed_code)
    print_output(df, fmt, output_path, filename=parsed_code)
