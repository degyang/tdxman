"""分时图命令。"""

from __future__ import annotations

from pathlib import Path

import click

from .help import StandardHelpCommand
from .output import output_options


@click.command(cls=StandardHelpCommand)
@click.argument("symbol_or_market")
@click.argument("code", required=False)
@click.option("--date", default=None, type=int, help="日期 YYYYMMDD（默认今天）")
@click.option("--days", default=1, type=click.Choice([1, 5]), help="交易日数量：1 或 5")
@output_options
def tick(
    symbol_or_market: str,
    code: str | None,
    date: int | None,
    days: int,
    output_fmt: str,
    output_path: Path | None,
) -> None:
    """获取分时图数据。

    示例（新格式）：

      tdxman tick 000001.SZ

      tdxman tick 600519.SH --days 5 --format table

    示例（旧格式兼容）：

      tdxman tick SZ 000001

      tdxman tick SH 600519 --days 5
    """
    from .conn import get_mac_client
    from .output import print_output
    from .parsers import parse_symbol_or_market_code

    fmt = output_fmt
    mkt, parsed_code = parse_symbol_or_market_code(symbol_or_market, code)
    with get_mac_client() as client:
        if days > 1:
            df = client.get_tick_charts(mkt, parsed_code, date=date, days=days)
        else:
            df = client.get_tick_chart(mkt, parsed_code, date=date)
    print_output(df, fmt, output_path, filename=parsed_code)
