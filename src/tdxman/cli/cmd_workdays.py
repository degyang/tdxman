"""CLI for observed trading sessions."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import click
import pandas as pd

from .help import StandardHelpCommand


@click.command("workdays", cls=StandardHelpCommand)
@click.option("--date", "query_date", type=click.DateTime(formats=["%Y-%m-%d", "%Y%m%d"]))
@click.option("--start", type=click.DateTime(formats=["%Y-%m-%d", "%Y%m%d"]))
@click.option("--end", type=click.DateTime(formats=["%Y-%m-%d", "%Y%m%d"]))
@click.option("--max-bars", type=click.IntRange(1, 15000), default=10000, show_default=True)
@click.option(
    "-f",
    "--format",
    "output_fmt",
    type=click.Choice(["json", "table", "csv"]),
    default="json",
    show_default=True,
)
@click.option("-o", "--output", "output_path", type=click.Path(path_type=Path))
def workdays(
    query_date: datetime | None,
    start: datetime | None,
    end: datetime | None,
    max_bars: int,
    output_fmt: str,
    output_path: Path | None,
) -> None:
    """查询已观察到的沪深交易日；不能预测未来休市安排。"""
    from ..exceptions import TdxError
    from ..workdays import WorkdayService
    from .conn import get_mac_client
    from .output import print_output

    if (start is None) != (end is None):
        raise click.UsageError("--start 和 --end 必须同时指定")
    if query_date is not None and start is not None:
        raise click.UsageError("--date 与 --start/--end 不能同时使用")
    if start is not None and end is not None:
        if start > end:
            raise click.UsageError("--start 不能晚于 --end")
        if (end - start).days > 3660:
            raise click.UsageError("日期范围不能超过 3660 天")

    try:
        with get_mac_client() as client:
            calendar = WorkdayService(client, max_bars=max_bars)
            first_seen, last_seen = calendar.coverage
            if start is not None and end is not None:
                observed = set(calendar.range(start.date(), end.date()))
                rows = []
                day = start.date()
                while day <= end.date():
                    rows.append(
                        {
                            "date": day.isoformat(),
                            "is_open": day in observed,
                            "source": "tdx:index:kline",
                        }
                    )
                    day += timedelta(days=1)
                frame = pd.DataFrame(rows)
            else:
                day = (
                    query_date.date()
                    if query_date is not None
                    else datetime.now(ZoneInfo("Asia/Shanghai")).date()
                )
                previous = calendar.previous_workday(day)
                following = calendar.next_workday(day)
                frame = pd.DataFrame(
                    [
                        {
                            "date": day.isoformat(),
                            "is_open": calendar.is_workday(day),
                            "previous": previous.isoformat() if previous else None,
                            "next": following.isoformat() if following else None,
                            "coverage_start": first_seen.isoformat(),
                            "coverage_end": last_seen.isoformat(),
                            "source": "tdx:index:kline",
                        }
                    ]
                )
    except (TdxError, ValueError, OSError) as exc:
        raise click.ClickException(f"交易日查询失败：{exc}") from exc
    print_output(frame, output_fmt, output_path, filename="workdays")
