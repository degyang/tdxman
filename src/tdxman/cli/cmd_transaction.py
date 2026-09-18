"""逐笔成交命令。"""

from __future__ import annotations

from pathlib import Path

import click

from .help import StandardHelpCommand
from .output import output_options


@click.command(cls=StandardHelpCommand)
@click.argument("symbol_or_market")
@click.argument("code", required=False)
@click.option("--count", default=2000, type=int, help="请求数量")
@click.option("--start", default=0, type=int, help="起始偏移")
@click.option("--date", default=None, type=int, help="日期 YYYYMMDD（默认今天）")
@output_options
def transaction(
    symbol_or_market: str,
    code: str | None,
    count: int,
    start: int,
    date: int | None,
    output_fmt: str,
    output_path: Path | None,
) -> None:
    """获取逐笔成交数据。

    示例（新格式）：

      tdxman transaction 000001.SZ

      tdxman transaction 600519.SH --count 500 --format table

    示例（旧格式兼容）：

      tdxman transaction SZ 000001

      tdxman transaction SH 600519 --count 500
    """
    from .conn import get_mac_client
    from .output import print_output
    from .parsers import parse_symbol_or_market_code

    fmt = output_fmt
    mkt, parsed_code = parse_symbol_or_market_code(symbol_or_market, code)
    with get_mac_client() as client:
        df = client.get_transactions(mkt, parsed_code, count=count, start=start, date=date)
    print_output(df, fmt, output_path, filename=parsed_code)
