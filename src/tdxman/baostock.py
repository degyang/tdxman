"""Supplementary, dated Shanghai/Shenzhen data from BaoStock.

BaoStock owns a process-global socket. A client session is therefore serial;
it must not be shared with concurrent direct calls to the BaoStock SDK.
"""

from __future__ import annotations

import io
import math
import re
import socket
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from threading import Lock
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from .exceptions import TdxError
from .models.enums import Market

_SESSION_LOCK = Lock()
_FIELDS = (
    "date,code,open,high,low,close,preclose,volume,amount,turn,"
    "tradestatus,pctChg,isST,peTTM,pbMRQ,psTTM,pcfNcfTTM"
)
_NUMBERS = {
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "preclose": "pre_close",
    "volume": "volume",
    "amount": "amount",
    "turn": "turnover_rate",
    "pctChg": "pct_chg",
    "peTTM": "pe_ttm",
    "pbMRQ": "pb",
    "psTTM": "ps_ttm",
    "pcfNcfTTM": "pcf_ttm",
}
DAILY_COLUMNS = [
    "date",
    "symbol",
    "market",
    "code",
    *_NUMBERS.values(),
    "is_st",
    "trading_status",
    "source",
    "fetched_at",
]


class BaostockError(TdxError):
    """BaoStock failed or returned data that cannot be interpreted safely."""


def _number(value: str) -> float | None:
    if value == "":
        return None
    result = float(value)
    if not math.isfinite(result):
        raise BaostockError(f"BaoStock 返回非有限数值: {value!r}")
    return result


class _Socket:
    """Make an SDK receive loop fail on EOF instead of spinning forever."""

    def __init__(self, connection: socket.socket):
        self.connection = connection

    def recv(self, size: int) -> bytes:
        data = self.connection.recv(size)
        if not data:
            raise ConnectionError("BaoStock connection closed")
        return data

    def send(self, data: bytes) -> int:
        self.connection.sendall(data)
        return len(data)

    def close(self) -> None:
        self.connection.close()


class BaostockClient:
    """Read raw daily bars, dated ST/trading status, and security life cycles."""

    def __init__(self, timeout: float = 15):
        self.timeout = timeout
        self.sdk: Any = None
        self._connection: _Socket | None = None

    @staticmethod
    def security_code(market: int | Market, code: str) -> str:
        market = Market(market)
        if market not in (Market.SH, Market.SZ):
            raise ValueError("BaoStock 不支持北交所；请使用通达信数据源")
        prefixes = ("60", "68") if market == Market.SH else ("00", "30")
        if not re.fullmatch(r"[0-9]{6}", code) or not code.startswith(prefixes):
            raise ValueError("BaoStock 补充接口仅支持显式指定市场的沪深 A 股")
        return f"{market.name.lower()}.{code}"

    def __enter__(self) -> BaostockClient:
        try:
            import baostock as bs
            import baostock.common.contants as constants
            import baostock.common.context as context
            import baostock.util.socketutil as sockets
        except ImportError as exc:
            raise BaostockError("缺少 baostock；请更新 tdxman 的依赖") from exc
        _SESSION_LOCK.acquire()
        original_connect = sockets.SocketUtil.connect

        def connect(_instance: Any, api_key: str) -> None:
            host = (
                constants.BAOSTOCK_VIP_SERVER_IP
                if api_key.startswith("bs-")
                else constants.BAOSTOCK_SERVER_IP
            )
            self._connection = _Socket(
                socket.create_connection(
                    (host, constants.BAOSTOCK_SERVER_PORT), timeout=self.timeout
                )
            )
            context.default_socket = self._connection

        # SDK 0.9.x neither sets a connection timeout nor detects socket EOF.
        sockets.SocketUtil.connect = connect
        try:
            with redirect_stdout(io.StringIO()):
                self._check(bs.login())
            self.sdk = bs
            return self
        except Exception as exc:
            if self._connection is not None:
                self._connection.close()
            context.default_socket = None
            _SESSION_LOCK.release()
            raise BaostockError(f"BaoStock 登录失败: {exc}") from exc
        finally:
            sockets.SocketUtil.connect = original_connect

    def __exit__(self, *args: Any) -> None:
        import baostock.common.context as context

        try:
            with redirect_stdout(io.StringIO()):
                self.sdk.logout()
        finally:
            if self._connection is not None:
                self._connection.close()
            context.default_socket = None
            self.sdk = None
            _SESSION_LOCK.release()

    @staticmethod
    def _check(result: Any) -> None:
        if result is None or result.error_code != "0":
            raise BaostockError(
                f"BaoStock 查询失败: {getattr(result, 'error_code', None)} "
                f"{getattr(result, 'error_msg', '')}"
            )

    def _query(self, method: str, **kwargs: Any) -> list[dict[str, str]]:
        if self.sdk is None:
            raise BaostockError("请在 with BaostockClient() 会话内查询")
        with redirect_stdout(io.StringIO()):
            result = getattr(self.sdk, method)(**kwargs)
            self._check(result)
            rows = []
            while result.next():
                self._check(result)
                values = result.get_row_data()
                if len(values) != len(result.fields):
                    raise BaostockError("BaoStock 返回的列数不一致")
                rows.append(dict(zip(result.fields, values)))
            self._check(result)
        return rows

    def get_daily(
        self,
        market: int | Market,
        code: str,
        *,
        start: date | None = None,
        end: date | None = None,
        count: int | None = 30,
    ) -> pd.DataFrame:
        """Return raw prices; count includes explicitly reported suspended sessions.

        An explicit start bounds the query. With count=None, return the entire
        requested range. Never synthesize pre_close from a previous close.
        """
        security = self.security_code(market, code)
        if count is not None and count < 1:
            raise ValueError("count 必须为正整数")
        end = end or datetime.now(ZoneInfo("Asia/Shanghai")).date()
        floor = date(1990, 12, 19)
        if end < floor:
            raise ValueError("end 早于沪深股票历史范围")
        if start is not None and start > end:
            raise ValueError("start 不能晚于 end")
        if start is None and count is None:
            raise ValueError("count=None 时必须提供 start")
        days = max(60, (count or 30) * 2)
        while True:
            first = start or max(floor, end - timedelta(days=days))
            rows = self._query(
                "query_history_k_data_plus",
                code=security,
                fields=_FIELDS,
                start_date=first.isoformat(),
                end_date=end.isoformat(),
                frequency="d",
                adjustflag="3",
            )
            if start is not None or len(rows) >= (count or 0) or first == floor:
                break
            days *= 2
        normalized = []
        seen = set()
        fetched = datetime.now(timezone.utc)
        for row in rows:
            day = date.fromisoformat(row["date"])
            if row["code"] != security or not first <= day <= end or day in seen:
                raise BaostockError("BaoStock 返回错误证券、越界或重复交易日")
            seen.add(day)
            if row["isST"] not in ("", "0", "1") or row["tradestatus"] not in ("0", "1"):
                raise BaostockError("BaoStock 返回未知 ST 或交易状态")
            normalized.append(
                {
                    "date": day,
                    "symbol": f"{code}.{Market(market).name}",
                    "market": Market(market).name,
                    "code": code,
                    **{dest: _number(row[src]) for src, dest in _NUMBERS.items()},
                    "is_st": None if row["isST"] == "" else row["isST"] == "1",
                    "trading_status": "TRADING" if row["tradestatus"] == "1" else "SUSPENDED",
                    "source": "baostock",
                    "fetched_at": fetched,
                }
            )
        frame = pd.DataFrame(normalized, columns=DAILY_COLUMNS).sort_values("date")
        frame.attrs.update(price_adjustment="raw", volume_unit="share", amount_unit="CNY")
        return frame.tail(count).reset_index(drop=True) if count is not None else frame

    def get_stock_basic(self, market: int | Market, code: str) -> pd.DataFrame:
        security = self.security_code(market, code)
        rows = self._query("query_stock_basic", code=security)
        if len(rows) != 1 or rows[0]["code"] != security or rows[0]["type"] != "1":
            raise BaostockError(f"BaoStock 未返回 {security} 的唯一股票基本资料")
        row = rows[0]
        listing = date.fromisoformat(row["ipoDate"])
        delisting = date.fromisoformat(row["outDate"]) if row["outDate"] else None
        if delisting is not None and delisting < listing:
            raise BaostockError("BaoStock 返回无效的上市/退出日期")
        return pd.DataFrame(
            [
                {
                    "symbol": f"{code}.{Market(market).name}",
                    "code": code,
                    "market": Market(market).name,
                    "name": row["code_name"],
                    "listing_date": listing,
                    "delisting_date": delisting,
                    "source": "baostock",
                    "fetched_at": datetime.now(timezone.utc),
                }
            ]
        )

    def get_trade_calendar(self, start: date, end: date) -> pd.DataFrame:
        rows = self._query(
            "query_trade_dates", start_date=start.isoformat(), end_date=end.isoformat()
        )
        result = []
        for row in rows:
            if row["is_trading_day"] not in ("0", "1"):
                raise BaostockError("BaoStock 返回未知交易日标志")
            result.append(
                {
                    "date": date.fromisoformat(row["calendar_date"]),
                    "is_open": row["is_trading_day"] == "1",
                    "source": "baostock",
                }
            )
        expected = {start + timedelta(days=i) for i in range((end - start).days + 1)}
        if len(result) != len(expected) or {r["date"] for r in result} != expected:
            raise BaostockError("BaoStock 交易日历缺日或越界")
        return pd.DataFrame(result)
