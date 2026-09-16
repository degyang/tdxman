"""本地通达信 vipdoc 数据命令。"""

from __future__ import annotations

from pathlib import Path

import click
import pandas as pd

from .._df import _merge_bar_datetime, _to_df
from ..exceptions import TdxOfflineError
from ..offline import find_daily_bar_file, read_daily_bars
from .output import output_options, print_output
from .parsers import parse_market, parse_stocks


@click.group("offline")
@click.option(
    "--vipdoc",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="vipdoc 目录；默认读取 settings/config.yaml 的 offline.vipdoc",
)
@click.pass_context
def offline(ctx: click.Context, vipdoc: Path | None) -> None:
    """读取本地通达信 vipdoc 数据。"""
    ctx.ensure_object(dict)
    ctx.obj["vipdoc"] = vipdoc


def _daily_frame(market: int, code: str, vipdoc: Path | None) -> pd.DataFrame:
    bars = read_daily_bars(find_daily_bar_file(market, code, vipdoc))
    df = _merge_bar_datetime(_to_df(bars), daily_plus=True)
    if not df.empty:
        df.insert(1, "market", "SH" if market == 1 else "SZ")
        df.insert(2, "code", code)
    return df


@offline.command("quote")
@click.argument("stocks", nargs=-1, required=True)
@output_options
@click.pass_context
def quote(
    ctx: click.Context, stocks: tuple[str, ...], output_fmt: str, output_path: Path | None
) -> None:
    """读取本地日线的最新一根记录（支持多只）。

    示例：

      tdxman offline quote SH 600519

      tdxman offline quote "SZ 000001,SH 600519"
    """
    if len(stocks) == 1:
        stock_list = parse_stocks(stocks[0])
    elif len(stocks) % 2 == 0:
        stock_list = [
            (parse_market(market), code)
            for market, code in zip(stocks[::2], stocks[1::2], strict=True)
        ]
    else:
        raise click.UsageError("标的格式为 MARKET CODE；多个标的请用逗号分隔或成对传入")
    try:
        frames = [
            _daily_frame(market, code, ctx.obj["vipdoc"]).tail(1) for market, code in stock_list
        ]
    except TdxOfflineError as exc:
        raise click.ClickException(str(exc)) from exc
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    print_output(df, output_fmt, output_path, split_rows=len(frames) > 1)


@offline.command("kline")
@click.argument("market")
@click.argument("code")
@click.option(
    "--count", default=800, type=click.IntRange(min=1), show_default=True, help="返回数量"
)
@click.option(
    "--start", default=0, type=click.IntRange(min=0), show_default=True, help="距最新记录的起始偏移"
)
@output_options
@click.pass_context
def kline(
    ctx: click.Context,
    market: str,
    code: str,
    count: int,
    start: int,
    output_fmt: str,
    output_path: Path | None,
) -> None:
    """读取本地日线 K 线。"""
    try:
        df = _daily_frame(parse_market(market), code, ctx.obj["vipdoc"])
    except TdxOfflineError as exc:
        raise click.ClickException(str(exc)) from exc
    end = max(0, len(df) - start)
    begin = max(0, end - count)
    print_output(df.iloc[begin:end], output_fmt, output_path, filename=code)
