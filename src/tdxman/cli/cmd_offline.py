"""本地通达信 vipdoc K 线命令。"""

from __future__ import annotations

from pathlib import Path

import click

from .._df import _merge_bar_datetime, _to_df
from ..exceptions import TdxError
from ..offline import (
    find_5min_bar_file,
    find_daily_bar_file,
    find_lc1_bar_file,
    read_5min_bars,
    read_daily_bars,
    read_lc_min_bars,
)
from .output import output_options, print_output
from .parsers import parse_market


@click.command("offline")
@click.argument("market")
@click.argument("code")
@click.option(
    "--period",
    type=click.Choice(["DAILY", "1MIN", "5MIN"], case_sensitive=False),
    default="DAILY",
    show_default=True,
    help="本地 K 线周期",
)
@click.option(
    "--count", default=800, type=click.IntRange(min=1), show_default=True, help="返回数量"
)
@click.option(
    "--start", default=0, type=click.IntRange(min=0), show_default=True, help="距最新记录的起始偏移"
)
@click.option(
    "--vipdoc",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="vipdoc 目录；默认读取 settings/config.yaml 的 offline.vipdoc",
)
@output_options
def offline(
    market: str,
    code: str,
    period: str,
    count: int,
    start: int,
    vipdoc: Path | None,
    output_fmt: str,
    output_path: Path | None,
) -> None:
    """读取本地通达信 vipdoc K 线数据。

    示例：

      tdxman offline SZ 000001 --period DAILY --count 30

      tdxman offline SH 600519 --period 5MIN --format table
    """
    market_value = parse_market(market)
    period = period.upper()
    try:
        if period == "DAILY":
            bars = read_daily_bars(find_daily_bar_file(market_value, code, vipdoc))
            daily_plus = True
        elif period == "1MIN":
            bars = read_lc_min_bars(find_lc1_bar_file(market_value, code, vipdoc))
            daily_plus = False
        else:
            bars = read_5min_bars(find_5min_bar_file(market_value, code, vipdoc))
            daily_plus = False
    except TdxError as exc:
        raise click.ClickException(str(exc)) from exc

    df = _merge_bar_datetime(_to_df(bars), daily_plus=daily_plus)
    if not df.empty:
        df.insert(1, "market", "SH" if market_value == 1 else "SZ")
        df.insert(2, "code", code)
    end = max(0, len(df) - start)
    begin = max(0, end - count)
    print_output(df.iloc[begin:end], output_fmt, output_path, filename=code)
