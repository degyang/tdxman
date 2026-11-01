"""Current ST names and source-flagged stock suspensions."""

from __future__ import annotations

import time
from datetime import datetime
from numbers import Integral

import click
import pandas as pd

from ..exceptions import TdxError
from ..models.enums import Market
from .cmd_stock_codes import COLUMNS, SCOPES, read_stock_rows
from .help import StandardHelpCommand
from .output import output_options, print_output


def _st_type(name: str) -> str | None:
    text = name.strip().upper()
    if text.startswith(("*ST", "S*ST")):
        return "*ST"
    if text.startswith(("ST", "SST")):
        return "ST"
    return None


def _suspensions(rows, progress):
    from .conn import get_tdx_quote_client

    result = []
    if not rows:
        return result
    progress("正在连接标准行情服务器读取停牌状态…")
    with get_tdx_quote_client() as client:
        for start in range(0, len(rows), 80):
            batch = rows[start : start + 80]
            identities = {(int(Market[r["market"]]), r["code"]): r for r in batch}
            frame = client.get_security_quotes(list(identities))
            seen = set()
            for quote in frame.to_dict("records"):
                key = (quote.get("market"), quote.get("code"))
                raw = quote.get("trading_status_raw")
                if key not in identities or key in seen:
                    raise ValueError("停牌状态报价身份重复或不匹配")
                if isinstance(raw, bool) or not isinstance(raw, Integral) or not 0 <= raw <= 65535:
                    raise ValueError(f"{identities[key]['symbol']} 缺少有效的来源状态位")
                seen.add(key)
                if raw & 0x20:
                    result.append(
                        dict(
                            identities[key], trading_status="SUSPENDED", trading_status_raw=int(raw)
                        )
                    )
            if seen != set(identities):
                raise ValueError(
                    f"停牌状态报价缺失 {len(identities) - len(seen)}/{len(identities)} 只，"
                    "未输出部分结果。"
                    "来源未返回完整状态，无法确认停牌清单；可用 --type st 单独查询 ST/*ST。"
                )
            progress(f"已检查 {start + len(batch)}/{len(rows)} 只，命中 {len(result)} 只停牌股票")
    return result


def _run(kind, market, scope, codes, names, output_fmt, output_path, show_progress):
    started = time.monotonic()

    def progress(message):
        if show_progress:
            click.echo(message, err=True)

    rows = read_stock_rows(market, scope, codes, names, progress)
    try:
        suspended = {r["symbol"]: r for r in _suspensions(rows, progress)} if kind != "st" else {}
    except (TdxError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    selected = []
    for row in rows:
        st = _st_type(row["name"])
        suspension = suspended.get(row["symbol"])
        if (kind in {"all", "st"} and st) or suspension:
            selected.append(
                dict(
                    row,
                    st_type=st,
                    is_st=st is not None,
                    is_suspended=(None if kind == "st" else suspension is not None),
                    trading_status="SUSPENDED" if suspension else None,
                    trading_status_raw=suspension["trading_status_raw"] if suspension else None,
                )
            )
    checked_at = datetime.now().astimezone().isoformat(timespec="seconds")
    extra = [
        "st_type",
        "is_st",
        "is_suspended",
        "trading_status",
        "trading_status_raw",
        "checked_at",
    ]
    frame = pd.DataFrame(
        [dict(r, checked_at=checked_at) for r in selected], columns=COLUMNS + extra
    )
    progress(f"完成：{len(frame)} 只异常股票，耗时 {time.monotonic() - started:.1f} 秒")
    print_output(frame, output_fmt, output_path, filename="abnormal-list")


def _status_options(function):
    function = output_options(function)
    function = click.option(
        "--progress", "show_progress", is_flag=True, help="显示连接与分页进度（标准错误）"
    )(function)
    function = click.option("--name", "names", multiple=True, help="简称精确匹配；可重复")(function)
    function = click.option("--code", "codes", multiple=True, help="代码；可重复或逗号分隔")(
        function
    )
    function = click.option(
        "--scope", type=click.Choice(SCOPES, case_sensitive=False), default="all", show_default=True
    )(function)
    return click.argument(
        "market", type=click.Choice(["ALL", "SH", "SZ", "BJ"], case_sensitive=False), default="ALL"
    )(function)


@click.command("abnormal-list", cls=StandardHelpCommand)
@click.option(
    "--type",
    "kind",
    type=click.Choice(["all", "st", "suspended"]),
    default="all",
    show_default=True,
    help="全部异常、ST/*ST 或停牌",
)
@_status_options
def stock_abnormal_list(kind, market, scope, codes, names, output_fmt, output_path, show_progress):
    """当前 ST / *ST 或停牌股票列表。"""
    _run(kind, market, scope, codes, names, output_fmt, output_path, show_progress)
