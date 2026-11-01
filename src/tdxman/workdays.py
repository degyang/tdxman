"""Observed Shanghai/Shenzhen trading sessions from a TDX benchmark index."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import date, datetime

import pandas as pd

from .exceptions import TdxDecodeError
from .mac.client import MacClient
from .mac.enums import Period
from .models.enums import Market


def _day(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        if "-" in value:
            return date.fromisoformat(value)
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError as exc:
        raise ValueError("日期应为 YYYY-MM-DD 或 YYYYMMDD") from exc


class WorkdayService:
    """Query sessions observed in the Shanghai Composite daily K-line series.

    Dates outside the loaded series are unknown. An in-range missing date is
    treated as closed, assuming the benchmark series itself is complete.
    """

    def __init__(self, client: MacClient, *, max_bars: int = 10000) -> None:
        if max_bars < 1:
            raise ValueError("max_bars must be positive")
        self.client = client
        self.max_bars = max_bars
        self._days: list[date] | None = None
        self._day_set: set[date] = set()

    def refresh(self) -> int:
        frame = self.client.get_stock_kline(Market.SH, "000001", Period.DAILY, count=self.max_bars)
        if frame.empty or "datetime" not in frame:
            raise TdxDecodeError("上证指数日 K 为空，无法建立交易日序列")
        timestamps = pd.to_datetime(frame["datetime"], errors="coerce")
        if timestamps.isna().any():
            raise TdxDecodeError("上证指数日 K 包含无效日期")
        days = sorted(set(timestamps.dt.date))
        if not days:
            raise TdxDecodeError("上证指数日 K 未返回交易日期")
        self._days = days
        self._day_set = set(days)
        return len(days)

    @property
    def coverage(self) -> tuple[date, date]:
        days = self._loaded_days()
        return days[0], days[-1]

    def is_workday(self, value: date | datetime | str) -> bool | None:
        day = _day(value)
        days = self._loaded_days()
        if day < days[0] or day > days[-1]:
            return None
        return day in self._day_set

    def next_workday(
        self, value: date | datetime | str, *, include_self: bool = False
    ) -> date | None:
        day = _day(value)
        days = self._loaded_days()
        if day < days[0] or day > days[-1]:
            return None
        index = bisect_left(days, day) if include_self else bisect_right(days, day)
        return days[index] if index < len(days) else None

    def previous_workday(
        self, value: date | datetime | str, *, include_self: bool = False
    ) -> date | None:
        day = _day(value)
        days = self._loaded_days()
        if day < days[0] or day > days[-1]:
            return None
        index = bisect_right(days, day) - 1 if include_self else bisect_left(days, day) - 1
        return days[index] if index >= 0 else None

    def range(self, start: date | datetime | str, end: date | datetime | str) -> list[date]:
        first, last = _day(start), _day(end)
        days = self._loaded_days()
        if first > last:
            raise ValueError("开始日期不能晚于结束日期")
        if first < days[0] or last > days[-1]:
            raise ValueError("日期范围超出已取得的指数日 K，不能判断交易日")
        return days[bisect_left(days, first) : bisect_right(days, last)]

    def _loaded_days(self) -> list[date]:
        if self._days is None:
            self.refresh()
        assert self._days is not None
        return self._days
