"""Read-only daily market data contract: shares, CNY, percentage points."""

from __future__ import annotations

import fcntl
import re
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

import duckdb
import pandas as pd

from .api_contract import DAILY_FIELDS, OPTIONAL_FIELDS, DataPoolError, public_read

# 支持的符号格式: 000001.SH (规范) 或 SH.000001 (兼容)
_SYMBOL_PATTERN = re.compile(r"^(\d{6})\.(SH|SZ|BJ)$|^(SH|SZ|BJ)\.(\d{6})$")


def _normalize_symbol(s: str) -> str:
    """将符号规范化为存储格式 SH.XXXXXX。

    支持输入:
    - 000001.SH -> SH.000001
    - SH.000001 -> SH.000001
    """
    s = s.strip().upper()
    m = re.match(r"^(\d{6})\.(SH|SZ|BJ)$", s)
    if m:
        return f"{m.group(2)}.{m.group(1)}"
    m = re.match(r"^(SH|SZ|BJ)\.(\d{6})$", s)
    if m:
        return s
    raise DataPoolError(
        "INVALID_ARGUMENT",
        f"Invalid symbol format: {s}; use 000001.SH or SH.000001"
    )


def _normalize_symbols(values):
    """规范化符号列表为存储格式。"""
    if values is None:
        return None
    result = [values] if isinstance(values, str) else list(values)
    normalized = []
    for v in result:
        if not isinstance(v, str):
            raise DataPoolError("INVALID_ARGUMENT", f"Symbol must be a string: {v}")
        normalized.append(_normalize_symbol(v))
    return normalized


@contextmanager
def pool_lock(root: Path, *, write: bool = False):
    """Coordinate batch writers and readers without opening the DuckDB catalog."""
    root = Path(root).expanduser().resolve()
    if write:
        root.mkdir(parents=True, exist_ok=True)
    # Directory locks allow a reader to remain strictly read-only.
    import os

    fd = os.open(root, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def writer(function):
    @wraps(function)
    def wrapped(root, *args, **kwargs):
        from .store import catalog_session

        with pool_lock(root, write=True), catalog_session(root):
            return function(root, *args, **kwargs)

    return wrapped


class DataPool:
    """Read raw daily bars without network access or catalog mutations.

    volume is shares; amount is CNY; turnover_rate is percentage points
    (0.43 means 0.43%). Unknown source fields stay null.
    """

    def __init__(self, root: str | Path = "~/.aspool"):
        self.root = Path(root).expanduser().resolve()

    @public_read
    def read_daily(self, *, symbols=None, start=None, end=None, lookback=None, fields=None):
        selected = (
            list(DAILY_FIELDS)
            if fields is None
            else ([fields] if isinstance(fields, str) else list(fields))
        )
        if (
            not selected
            or len(set(selected)) != len(selected)
            or set(selected) - DAILY_FIELDS.keys()
        ):
            raise DataPoolError(
                "FIELD_UNSUPPORTED", "fields must contain unique public daily fields"
            )
        if lookback is not None and (
            isinstance(lookback, bool) or not isinstance(lookback, int) or lookback < 1
        ):
            raise DataPoolError("INVALID_ARGUMENT", "lookback must be a positive integer")
        try:
            start = pd.Timestamp(start).date() if start is not None else None
            end = pd.Timestamp(end).date() if end is not None else None
            if (start is not None and pd.isna(start)) or (end is not None and pd.isna(end)):
                raise ValueError("Missing date boundary")
        except (ValueError, TypeError) as exc:
            raise DataPoolError("INVALID_ARGUMENT", "Invalid date boundary") from exc
        if start and end and start > end:
            raise DataPoolError("INVALID_ARGUMENT", "start must not be after end")
        with pool_lock(self.root):
            files = sorted((self.root / "lake/bars/daily").glob("market=*/symbol=*/bars.parquet"))
            if not files:
                raise DataPoolError("DAILY_NOT_FOUND", "No daily bars in aspool")
            with duckdb.connect() as conn:
                conn.read_parquet(
                    [str(p) for p in files], union_by_name=True, hive_partitioning=True
                ).create_view("bars")
                names = {row[0] for row in conn.execute("describe bars").fetchall()}
                market, code = "market", "symbol"
                asset_filter = '"asset_type" IS NULL OR "asset_type" <> \'etf\''
                rate = (
                    "turnover_rate"
                    if "turnover_rate" in names
                    else "turnover"
                    if "turnover" in names
                    else "NULL"
                )
                if "turnover_rate" in names and "turnover" in names:
                    rate = "coalesce(turnover_rate, turnover)"
                volume = "volume" if "volume" in names else "NULL"
                if "vol" in names:
                    volume = f"coalesce({volume}, vol)"
                clauses, params = [], []
                if "asset_type" in names:
                    clauses.append(f"({asset_filter})")
                if start:
                    clauses.append("trade_date >= ?")
                    params.append(start)
                if end:
                    clauses.append("trade_date <= ?")
                    params.append(end)
                if symbols is not None:
                    # 规范化为存储格式
                    normalized = _normalize_symbols(symbols)
                    clauses.append(f"{market} || '.' || {code} IN (SELECT unnest(?))")
                    params.append(normalized)
                where = " WHERE " + " AND ".join(clauses) if clauses else ""
                # Validate the filtered population before a window can hide duplicates.
                duplicate = conn.execute(
                    f"SELECT {market}, {code}, trade_date FROM bars{where} "
                    f"GROUP BY {market}, {code}, trade_date HAVING count(*) > 1 LIMIT 1",
                    params,
                ).fetchone()
                if duplicate is not None:
                    raise DataPoolError("DAILY_INVALID", "Duplicate daily keys")
                extensions = ", ".join(
                    f'CAST("{key}" AS {dtype}) AS "{key}"'
                    if key in names
                    else f'CAST(NULL AS {dtype}) AS "{key}"'
                    for key, (dtype, _) in OPTIONAL_FIELDS.items()
                )
                # 输出规范格式: code.market (如 000001.SH)
                sql = f"""SELECT {code} || '.' || {market} AS symbol, {market} AS market,
                    {code} AS code, trade_date AS date, open, high, low, close,
                    {volume} AS volume, amount, {rate} AS turnover_rate,
                    {extensions} FROM bars{where}"""
                if lookback:
                    sql += (
                        " QUALIFY row_number() OVER "
                        "(PARTITION BY market, code ORDER BY trade_date DESC) <= ?"
                    )
                    params.append(lookback)
                frame = conn.execute(sql + " ORDER BY symbol, date", params).fetchdf()
        required = ["open", "high", "low", "close", "volume", "amount"]
        if frame[required].isna().any().any():
            raise DataPoolError("DAILY_INVALID", "Daily OHLCV/amount is incomplete; repair aspool")
        if not frame.empty:
            import numpy as np

            if not np.isfinite(frame[required].to_numpy(dtype=float)).all():
                raise DataPoolError("DAILY_INVALID", "Non-finite daily values")
            if (frame[required] < 0).any().any() or (frame.high < frame.low).any():
                raise DataPoolError("DAILY_INVALID", "Invalid daily values")
        frame.attrs.update(
            contract_version=2,
            price_adjustment="raw",
            volume_unit="share",
            amount_unit="CNY",
            turnover_rate_unit="percent",
            available_at=None,
            point_in_time=False,
            dataset_version=None,
        )
        return frame[selected].copy()

    @public_read
    def status(self):
        with pool_lock(self.root):
            files = sorted((self.root / "lake/bars/daily").glob("market=*/symbol=*/bars.parquet"))
            if not files:
                return {"backend": "aspool", "status": "empty", "root": str(self.root)}
            with duckdb.connect() as conn:
                conn.read_parquet(
                    [str(p) for p in files], union_by_name=True, hive_partitioning=True
                ).create_view("bars")
                names = {row[0] for row in conn.execute("describe bars").fetchall()}
                asset_clause = (
                    "WHERE asset_type IS NULL OR asset_type <> 'etf'"
                    if "asset_type" in names
                    else ""
                )
                rows, symbols, start, end = conn.execute(
                    f"""SELECT count(*), count(distinct (market, symbol)),
                    min(trade_date), max(trade_date) FROM bars {asset_clause}"""
                ).fetchone()
                etf_rows = etf_symbols = etf_start = etf_end = 0
                if "asset_type" in names:
                    etf_rows, etf_symbols, etf_start, etf_end = conn.execute(
                        """SELECT count(*), count(distinct (market, symbol)),
                        min(trade_date), max(trade_date) FROM bars WHERE asset_type = 'etf'"""
                    ).fetchone()
        return dict(
            backend="aspool",
            status="available",
            root=str(self.root),
            row_count=rows,
            symbol_count=symbols,
            start=str(start),
            end=str(end),
            price_adjustment="raw",
            etf_row_count=etf_rows,
            etf_symbol_count=etf_symbols,
            etf_start=str(etf_start) if etf_start is not None else None,
            etf_end=str(etf_end) if etf_end is not None else None,
        )

    def _read_fundamentals(self, *, symbols=None, as_of=None):
        """Return the latest low-frequency fundamentals and ratios at a bar close.

        Shares are shares.  PE and PB use the selected daily close with the
        latest available trailing earnings and book value per share, so their
        numerator remains current without a daily quote request.
        """
        path = self.root / "lake/fundamentals/snapshots.parquet"
        if not path.exists():
            raise FileNotFoundError("No fundamentals snapshots in aspool")
        with pool_lock(self.root):
            with duckdb.connect() as conn:
                frame = conn.execute(
                    "select * from read_parquet(?) order by market, code", [str(path)]
                ).fetchdf()
        frame = frame.rename(columns={"total_shares": "total_share", "float_shares": "float_share"})
        # 规范格式 code.market：与 read_daily 输出一致，避免 join 丢失。
        frame["symbol_id"] = frame.code + "." + frame.market
        if symbols is not None:
            requested = [symbols] if isinstance(symbols, str) else list(symbols)
            # _normalize_symbol 返回存储格式 market.code，需还原为规范格式 code.market，
            # 才能与 symbol_id 对齐。
            want = set()
            for value in requested:
                market_part, code_part = _normalize_symbol(value).split(".", 1)
                want.add(f"{code_part}.{market_part}")
            frame = frame[frame.symbol_id.isin(want)]
        prices = self.read_daily(symbols=sorted(frame.symbol_id.unique()), end=as_of)
        closes = prices.groupby("symbol", as_index=False).tail(1).set_index("symbol").close
        frame["close"] = frame.symbol_id.map(closes)
        frame["total_mv"] = frame.close * frame.total_share
        frame["float_mv"] = frame.close * frame.float_share
        frame["pe_ttm"] = frame.close / frame.ttm_eps
        frame.loc[frame.ttm_eps <= 0, "pe_ttm"] = None
        frame["pb"] = frame.close / frame.net_assets
        frame.loc[frame.net_assets <= 0, "pb"] = None
        return frame.drop(columns="symbol_id")

    @public_read
    def read_index_daily(self, *, symbols=None, start=None, end=None, lookback=None, fields=None):
        """Read index daily bars independently of the stock universe."""
        from .index_api import read_index_daily

        return read_index_daily(
            self.root, symbols=symbols, start=start, end=end, lookback=lookback, fields=fields
        )

    @public_read
    def list_indices(self, *, symbols=None):
        """List stored index identities and actual date coverage."""
        from .index_api import list_indices

        return list_indices(self.root, symbols=symbols)

    @public_read
    def read_etf_daily(self, *, symbols=None, start=None, end=None, lookback=None, fields=None):
        """Read ETF daily bars with security (share/amount/turnover) semantics."""
        from .etf_api import read_etf_daily

        return read_etf_daily(
            self.root, symbols=symbols, start=start, end=end, lookback=lookback, fields=fields
        )

    @public_read
    def list_etfs(self, *, symbols=None):
        """List synchronized ETFs and their actual date coverage."""
        from .etf_api import list_etfs

        return list_etfs(self.root, symbols=symbols)

    # ------------------------------------------------------------------ #
    # 日终涨跌停派生（已发布批次）
    # ------------------------------------------------------------------ #

    @public_read
    def read_limit_summary(self, *, start=None, end=None):
        """已发布的日级涨跌停汇总。"""
        from .limit_api import read_limit_summary

        return read_limit_summary(self.root, start=start, end=end)

    @public_read
    def read_limit_events(self, *, trade_date=None, start=None, end=None, symbols=None):
        """已发布的逐股涨跌停事件；输出规范 symbol。"""
        from .limit_api import read_limit_events

        return read_limit_events(
            self.root, trade_date=trade_date, start=start, end=end, symbols=symbols
        )

    @public_read
    def read_limit_exceptions(self, *, trade_date=None, start=None, end=None, symbols=None):
        """已发布的异常/未知/无约束记录。"""
        from .limit_api import read_limit_exceptions

        return read_limit_exceptions(
            self.root, trade_date=trade_date, start=start, end=end, symbols=symbols
        )

    @public_read
    def read_limit_coverage(self, *, start=None, end=None):
        """已发布批次的范围与完成状态；含 stale 标记。"""
        from .limit_api import read_limit_coverage

        return read_limit_coverage(self.root, start=start, end=end)

    @public_read
    def read_limit_scope(self, *, trade_date=None, start=None, end=None):
        """已发布批次实际处理的证券名单。

        在名单内、无事件且无异常 => 确定无涨跌停事件。
        """
        from .limit_api import read_limit_scope

        return read_limit_scope(self.root, trade_date=trade_date, start=start, end=end)

    @public_read
    def read_limit_staleness(self, *, start=None, end=None):
        """被标记为陈旧/失败的日期，供消费者拒绝或降级使用。"""
        from .limit_api import read_limit_staleness

        return read_limit_staleness(self.root, start=start, end=end)

    @public_read
    def describe_limits(self):
        """声明已实现的涨跌停派生能力与字段单位；只读。"""
        from .limit_api import describe_limits

        return describe_limits(self.root)

    def compute_limit_events(
        self, trade_dates, *, scope_id="stock", asset_type="stock", propagate=True
    ):
        """独立入口：重算并发布指定交易日的派生事件（可重试）。

        会自修改日向前传播至连板/事件不再变化；失败则标记 stale。
        """
        from .limit_events import compute_limit_events

        return compute_limit_events(
            self.root,
            list(trade_dates),
            scope_id=scope_id,
            asset_type=asset_type,
            propagate=propagate,
        )

    def describe(self):
        """Return the implemented public contract and explicit capability limits."""
        from .ex_domain import load_ex_categories
        from .index_api import INDEX_FIELDS

        return {
            "contract_version": 2,
            "primary_key": ["symbol", "date"],
            "price_adjustment": "raw",
            "fields": {
                key: {
                    "type": dtype,
                    "unit": unit,
                    "nullable": key in OPTIONAL_FIELDS or key == "turnover_rate",
                }
                for key, (dtype, unit) in DAILY_FIELDS.items()
            },
            "index_fields": {
                key: {"type": dtype, "unit": unit, "nullable": False}
                for key, (dtype, unit) in INDEX_FIELDS.items()
            },
            "ex_categories": load_ex_categories(),
            "etf_fields": {
                key: {
                    "type": dtype,
                    "unit": unit,
                    "nullable": key in OPTIONAL_FIELDS or key == "turnover_rate",
                }
                for key, (dtype, unit) in DAILY_FIELDS.items()
            },
            "capabilities": {
                "index_daily": True,
                "index_listing": True,
                "etf_daily": True,
                "etf_listing": True,
                "daily": True,
                "field_projection": True,
                "immutable_versions": False,
                "point_in_time": False,
                "calendar": False,
                "historical_universe": False,
                "trading_status": False,
                "corporate_actions": False,
                "adjustments": False,
                "minute_bars": False,
            },
        }

    def read_research_daily(
        self, *, symbols=None, start=None, end=None, lookback=None, fields=None
    ):
        """Return the stable daily research contract for Fundwise.

        The internal fundamentals snapshot is deliberately not exposed.  It is
        only used by aspool writers to maintain the primary daily data store.
        """
        return self.read_daily(
            symbols=symbols, start=start, end=end, lookback=lookback, fields=fields
        )
