"""Fetch current stock directories without persisting a local universe."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from pathlib import Path

import click
import pandas as pd

from ..codec.bitmap import PresetField
from ..exceptions import TdxError
from ..mac.enums import Category, SortOrder, SortType
from ..models.enums import Market
from .help import StandardHelpCommand
from .output import output_options, print_output

COLUMNS = ["symbol", "code", "market", "name", "board"]
SCOPES = ("all", "main", "star", "chinext", "bse", "cdr")
BOARD_NAMES = {"main": "主板", "star": "科创板", "chinext": "创业板", "bse": "北交所", "cdr": "CDR"}


def _board(market: str, code: str) -> str | None:
    if market == "SH":
        if code.startswith(("600", "601", "603", "605")):
            return "main"
        if code.startswith("688"):
            return "star"
        if code.startswith("689"):
            return "cdr"
    if market == "SZ":
        if code.startswith(("000", "001", "002", "003", "004")):
            return "main"
        if code.startswith(("300", "301")):
            return "chinext"
    if market == "BJ" and code.startswith(("43", "83", "87", "88", "92")):
        return "bse"
    return None


def _collect(
    client, market: str, *, mac: bool, progress: Callable[[str], None] | None = None
) -> list[dict]:
    """Reject partial, repeated or malformed pages before exposing any output."""
    if progress:
        progress(f"{market}：读取证券目录…")
    count = None if mac else client.get_security_count(Market[market])
    if count is not None and not 0 < count <= 100_000:
        raise ValueError(f"{market} 证券总数无效：{count}")
    offset, seen, rows = 0, set(), []
    while offset < 100_000:
        if mac:
            frame = client.get_stock_quotes_list(
                Category[market],
                start=offset,
                count=80,
                sort_type=SortType.CODE,
                sort_order=SortOrder.ASC,
                fields=PresetField.NONE,
            )
        else:
            frame = client.get_security_list(Market[market], offset)
        if frame.empty:
            if count is not None or not seen:
                raise ValueError(f"{market} 目录提前结束或为空（offset={offset}）")
            break
        for item in frame.to_dict("records"):
            code, name = item.get("code"), item.get("name")
            if (
                not isinstance(code, str)
                or not re.fullmatch(r"[0-9]{6}", code)
                or not isinstance(name, str)
                or not name.strip()
                or ("market" in item and item["market"] != int(Market[market]))
            ):
                raise ValueError(f"{market} 目录身份或名称无效")
            if code in seen:
                raise ValueError(f"{market} 目录分页重复：{code}")
            seen.add(code)
            board = _board(market, code)
            if board:
                rows.append(
                    dict(
                        symbol=f"{code}.{market}",
                        code=code,
                        market=market,
                        name=name.strip(),
                        board=BOARD_NAMES[board],
                    )
                )
        offset += len(frame)
        if progress:
            total = str(count) if count is not None else "未知"
            progress(f"{market}：已读取 {offset}/{total} 条证券，匹配 {len(rows)} 只股票")
        if count is not None:
            if offset > count:
                raise ValueError(f"{market} 目录条数超过来源总数")
            if offset == count:
                if client.get_security_count(Market[market]) != count:
                    raise ValueError(f"{market} 采集期间目录总数变化，请重试")
                break
        elif len(frame) < 80:
            break
    else:
        raise ValueError(f"{market} 目录超过分页预算")
    if not rows:
        raise ValueError(f"{market} 来源目录没有匹配的股票，请核查来源和分类规则")
    return rows


def _read_bj_rows(progress):
    """Retry the complete directory on another configured MAC endpoint."""
    from ..bestip import preferred
    from ..mac.client import MacClient
    from .conn import get_mac_client

    try:
        with get_mac_client() as client:
            return _collect(client, "BJ", mac=True, progress=progress)
    except (TdxError, OSError, ValueError) as exc:
        progress(f"北交所目录读取失败：{exc}；正在重试已配置的 MAC 服务器…")
    errors = []
    for endpoint in preferred("mac")[:3]:
        host, port = endpoint["host"], endpoint["port"]
        progress(f"北交所：重试 {host}:{port}…")
        try:
            with MacClient(
                host, port, timeout=3, auto_reconnect=False, heartbeat_interval=0
            ) as client:
                return _collect(client, "BJ", mac=True, progress=progress)
        except (TdxError, OSError, ValueError) as exc:
            errors.append(f"{host}:{port}: {exc}")
    raise ValueError("北交所目录重试失败，未输出部分清单；" + "；".join(errors))


def _codes(values: tuple[str, ...]) -> set[str]:
    result = set()
    for value in values:
        for code in value.upper().split(","):
            code = code.strip()
            match = re.fullmatch(r"(SH|SZ|BJ)([0-9]{6})", code)
            if match:
                code = f"{match[2]}.{match[1]}"
            if not re.fullmatch(r"[0-9]{6}(?:\.(?:SH|SZ|BJ))?", code):
                raise click.BadParameter(f"无效证券代码：{code}", param_hint="--code")
            result.add(code)
    return result


@click.command("stock-codes", cls=StandardHelpCommand)
@click.argument(
    "market", type=click.Choice(["ALL", "SH", "SZ", "BJ"], case_sensitive=False), default="ALL"
)
@click.option(
    "--scope",
    type=click.Choice(SCOPES, case_sensitive=False),
    default="all",
    show_default=True,
    help="股票范围：all/main/star/chinext/bse/cdr",
)
@click.option("--code", "codes", multiple=True, help="代码或 CODE.MARKET；可重复或逗号分隔")
@click.option("--name", "names", multiple=True, help="证券简称精确匹配；可重复")
@click.option("--progress", "show_progress", is_flag=True, help="显示连接与分页进度（标准错误）")
@output_options
def stock_codes(
    market: str,
    scope: str,
    codes: tuple[str, ...],
    names: tuple[str, ...],
    show_progress: bool,
    output_fmt: str,
    output_path: Path | None,
) -> None:
    """获取当前股票清单（沪深北，含 CDR），不入库。

    示例：

      tdxman stock-codes SH --format table

      tdxman stock-codes --scope bse --format json

      tdxman stock-codes --code 000001.SZ --name 平安银行
    """
    started = time.monotonic()

    def progress(message: str) -> None:
        if show_progress:
            click.echo(message, err=True)

    rows = read_stock_rows(market, scope, codes, names, progress)
    frame = pd.DataFrame(rows, columns=COLUMNS)
    progress(f"完成：{len(frame)} 只股票，耗时 {time.monotonic() - started:.1f} 秒")
    print_output(frame, output_fmt, output_path, filename="stock-codes")


def read_stock_rows(market, scope, codes, names, progress):
    """Read and filter the shared current stock universe before producing output."""
    from .conn import get_tdx_client

    requested = _codes(codes)
    scopes = {
        "main": {"SH", "SZ"},
        "star": {"SH"},
        "cdr": {"SH"},
        "chinext": {"SZ"},
        "bse": {"BJ"},
        "all": {"SH", "SZ", "BJ"},
    }
    markets = [
        m
        for m in ("SH", "SZ", "BJ")
        if (market.upper() == "ALL" or m == market.upper()) and m in scopes[scope.lower()]
    ]
    if not markets:
        raise click.UsageError("市场与 --scope 不匹配")
    rows = []
    try:
        standard = [m for m in markets if m != "BJ"]
        if standard:
            progress("正在连接标准行情服务器…")
            with get_tdx_client() as client:
                for m in standard:
                    rows.extend(_collect(client, m, mac=False, progress=progress))
        if "BJ" in markets:
            progress("正在连接北交所 MAC 行情服务器…")
            rows.extend(_read_bj_rows(progress))
    except (TdxError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    selected = [
        r
        for r in rows
        if (scope.lower() == "all" or r["board"] == BOARD_NAMES[scope.lower()])
        and (not requested or r["code"] in requested or r["symbol"] in requested)
        and (not names or r["name"] in names)
    ]
    return sorted(selected, key=lambda r: r["symbol"])
