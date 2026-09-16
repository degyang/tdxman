"""Read-only daily market data contract: shares, CNY, percentage points."""

from __future__ import annotations

import fcntl
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

import duckdb
import pandas as pd


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
        with pool_lock(root, write=True):
            return function(root, *args, **kwargs)

    return wrapped


class DataPool:
    """Read raw daily bars without network access or catalog mutations.

    volume is shares; amount is CNY; turnover_rate is percentage points
    (0.43 means 0.43%). Unknown source fields stay null.
    """

    def __init__(self, root: str | Path = "~/.aspool"):
        self.root = Path(root).expanduser().resolve()

    def read_daily(self, *, symbols=None, start=None, end=None, lookback=None):
        if lookback is not None and (not isinstance(lookback, int) or lookback < 1):
            raise ValueError("lookback must be a positive integer")
        start = pd.Timestamp(start).date() if start is not None else None
        end = pd.Timestamp(end).date() if end is not None else None
        if start and end and start > end:
            raise ValueError("start must not be after end")
        with pool_lock(self.root):
            files = sorted((self.root / "lake/bars/daily").glob("market=*/symbol=*/bars.parquet"))
            if not files:
                raise FileNotFoundError("No daily bars in aspool")
            with duckdb.connect() as conn:
                conn.read_parquet(
                    [str(p) for p in files], union_by_name=True, hive_partitioning=True
                ).create_view("bars")
                names = {row[0] for row in conn.execute("describe bars").fetchall()}
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
                if start:
                    clauses.append("trade_date >= ?")
                    params.append(start)
                if end:
                    clauses.append("trade_date <= ?")
                    params.append(end)
                if symbols is not None:
                    symbols = [symbols] if isinstance(symbols, str) else list(symbols)
                    clauses.append("market || '.' || symbol IN (SELECT unnest(?))")
                    params.append(symbols)
                where = " WHERE " + " AND ".join(clauses) if clauses else ""
                sql = f"""SELECT market || '.' || symbol AS symbol, market,
                    symbol AS code, trade_date AS date, open, high, low, close,
                    {volume} AS volume, amount, {rate} AS turnover_rate FROM bars{where}"""
                if lookback:
                    sql += (
                        " QUALIFY row_number() OVER "
                        "(PARTITION BY market, code ORDER BY trade_date DESC) <= ?"
                    )
                    params.append(lookback)
                frame = conn.execute(sql + " ORDER BY symbol, date", params).fetchdf()
        required = ["open", "high", "low", "close", "volume", "amount"]
        if frame[required].isna().any().any():
            raise ValueError("Daily OHLCV/amount is incomplete; repair aspool before screening")
        if not frame.empty:
            import numpy as np

            if not np.isfinite(frame[required].to_numpy(dtype=float)).all():
                raise ValueError("Non-finite daily values")
            if (frame[required] < 0).any().any() or (frame.high < frame.low).any():
                raise ValueError("Invalid daily values")
            if frame.duplicated(["symbol", "date"]).any():
                raise ValueError("Duplicate daily keys")
        frame.attrs.update(
            contract_version=1,
            price_adjustment="raw",
            volume_unit="share",
            amount_unit="CNY",
            turnover_rate_unit="percent",
            available_at=None,
        )
        return frame

    def status(self):
        with pool_lock(self.root):
            files = sorted((self.root / "lake/bars/daily").glob("market=*/symbol=*/bars.parquet"))
            if not files:
                return {"backend": "aspool", "status": "empty", "root": str(self.root)}
            with duckdb.connect() as conn:
                conn.read_parquet(
                    [str(p) for p in files], union_by_name=True, hive_partitioning=True
                ).create_view("bars")
                rows, symbols, start, end = conn.execute("""SELECT count(*),
                    count(distinct (market, symbol)), min(trade_date), max(trade_date)
                    FROM bars""").fetchone()
        return dict(
            backend="aspool",
            status="available",
            root=str(self.root),
            row_count=rows,
            symbol_count=symbols,
            start=str(start),
            end=str(end),
            price_adjustment="raw",
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
        if symbols is not None:
            requested = {symbols} if isinstance(symbols, str) else set(symbols)
            frame = frame[frame.apply(lambda row: f"{row.market}.{row.code}" in requested, axis=1)]
        prices = self.read_daily(
            symbols=[f"{row.market}.{row.code}" for row in frame.itertuples()], end=as_of
        )
        closes = prices.groupby("symbol", as_index=False).tail(1).set_index("symbol").close
        frame["symbol_id"] = frame.market + "." + frame.code
        frame["close"] = frame.symbol_id.map(closes)
        frame["total_mv"] = frame.close * frame.total_share
        frame["float_mv"] = frame.close * frame.float_share
        frame["pe_ttm"] = frame.close / frame.ttm_eps
        frame.loc[frame.ttm_eps <= 0, "pe_ttm"] = None
        frame["pb"] = frame.close / frame.net_assets
        frame.loc[frame.net_assets <= 0, "pb"] = None
        return frame.drop(columns="symbol_id")

    def read_research_daily(self, *, symbols=None, start=None, end=None, lookback=None):
        """Return the stable daily research contract for Fundwise.

        The internal fundamentals snapshot is deliberately not exposed.  It is
        only used by aspool writers to maintain the primary daily data store.
        """
        bars = self.read_daily(symbols=symbols, start=start, end=end, lookback=lookback)
        bars.attrs.update(bars.attrs, contract_version=1)
        return bars
