"""市场监控命令：unusual, market-stat。"""

from __future__ import annotations

from pathlib import Path

import click
import pandas as pd

from .._df import _to_df
from ..exceptions import TdxError
from ..models.enums import Market
from ..models.stats import MarketStat
from .output import output_options


def _market_stat_from_quotes(quotes: pd.DataFrame) -> pd.DataFrame:
    """Map MAC statistical quotes to the existing market-stat output schema."""
    required = {
        "880005": ("close", "open", "low", "high", "amount", "vol"),
        "880001": ("close",),
        "880006": ("close", "open"),
    }
    if quotes.empty or not {"market", "code"}.issubset(quotes.columns):
        raise click.ClickException("无法获取市场统计数据：MAC 服务器返回空数据或缺少代码字段")

    values: dict[str, dict[str, float]] = {}
    for code, fields in required.items():
        rows = quotes[(quotes["market"] == Market.SH) & (quotes["code"] == code)]
        if len(rows) != 1:
            raise click.ClickException(f"市场统计数据不完整：{code} 应返回一条记录")
        values[code] = {}
        for field in fields:
            try:
                value = float(rows.iloc[0][field])
            except (KeyError, TypeError, ValueError) as exc:
                raise click.ClickException(f"市场统计字段缺失或无效：{code}.{field}") from exc
            if not 0 <= value < float("inf"):
                raise click.ClickException(f"市场统计字段无效：{code}.{field}")
            values[code][field] = value

    counts = values["880005"]
    up, down, neutral, total = (int(counts[k]) for k in ("close", "open", "low", "high"))
    return _to_df(
        MarketStat(
            up_count=up,
            down_count=down,
            neutral_count=neutral,
            suspended_count=max(0, total - up - down - neutral),
            total_count=total,
            total_amount=counts["amount"],
            total_volume=counts["vol"],
            # Preserve the existing CLI conversion and output contract.
            total_market_cap=values["880001"]["close"] * 1e10,
            limit_up_count=int(values["880006"]["close"]),
            limit_down_count=int(values["880006"]["open"]),
        )
    )


@click.command()
@click.argument("market")
@click.option("--count", default=600, type=int, help="请求数量")
@click.option("--start", default=0, type=int, help="起始偏移")
@output_options
def unusual(
    market: str,
    count: int,
    start: int,
    output_fmt: str,
    output_path: Path | None,
) -> None:
    """获取市场异动数据。

    示例：

      tdxman unusual SZ

      tdxman unusual SH --count 100 --format table
    """
    from .conn import get_mac_client
    from .output import print_output
    from .parsers import parse_market

    fmt = output_fmt
    mkt = parse_market(market)
    with get_mac_client() as client:
        df = client.get_unusual(mkt, start=start, count=count)
    print_output(df, fmt, output_path)


@click.command("market-stat")
@output_options
def market_stat(output_fmt: str, output_path: Path | None) -> None:
    """获取 A 股全市场涨跌统计概况。

    示例：

      tdxman market-stat

      tdxman market-stat --format table
    """
    from .conn import get_mac_client
    from .output import print_output

    fmt = output_fmt
    try:
        with get_mac_client() as client:
            quotes = client.get_stock_quotes(
                [(Market.SH, "880005"), (Market.SH, "880001"), (Market.SH, "880006")]
            )
        df = _market_stat_from_quotes(quotes)
    except (TdxError, OSError) as exc:
        raise click.ClickException(f"无法获取市场统计数据：{exc}") from exc
    print_output(df, fmt, output_path)
