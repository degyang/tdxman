"""集合竞价命令。"""

from __future__ import annotations

from pathlib import Path

import click

from .help import StandardHelpCommand
from .output import output_options


@click.command(cls=StandardHelpCommand)
@click.argument("market")
@click.argument("code")
@output_options
def auction(market: str, code: str, output_fmt: str, output_path: Path | None) -> None:
    """获取集合竞价数据。

    示例：

      tdxman auction SZ 000001

      tdxman auction SH 600519 --format table
    """
    from .conn import get_mac_client
    from .output import print_output
    from .parsers import parse_market

    fmt = output_fmt
    mkt = parse_market(market)
    with get_mac_client() as client:
        df = client.get_auction(mkt, code)
    print_output(df, fmt, output_path, filename=code)
