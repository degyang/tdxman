"""Bounded reads of published limit events and same-day stored turnover amount."""

from __future__ import annotations

import tempfile
from datetime import timedelta
from pathlib import Path

import duckdb
import pandas as pd

from .api_contract import DataPoolError
from .daily_access import DailyStorage
from .limit_api import _bounds, _normalize_requested_symbols, _require_ready
from .pool import pool_lock
from .store import read_only_catalog

EVENT_AMOUNT_FIELDS = (
    "trade_date", "symbol", "close_limit_up", "close_limit_down",
    "touched_limit_up", "touched_limit_down", "limit_up_price", "limit_down_price",
    "consecutive_up", "consecutive_gap_sessions", "amount", "batch_id",
    "rule_version", "computed_at", "published_at", "stale", "stale_reason",
)


class EventAmountBatches:
    """One bounded SQL query per date window; no live cursor between batches."""

    def __init__(
        self, root, *, start, end, symbols=None, fields=None,
        close_limit_up=None, min_consecutive_up=None, batch_days=7,
        max_rows=25_000, memory_limit="512MB", threads=2, temp_directory=None,
    ):
        self.root = Path(root).expanduser().resolve()
        self.start, self.end = _bounds(start, end)
        if self.start is None or self.end is None:
            raise DataPoolError("INVALID_ARGUMENT", "start and end are required for bounded reads")
        if close_limit_up is not None and not isinstance(close_limit_up, bool):
            raise DataPoolError("INVALID_ARGUMENT", "close_limit_up must be boolean or None")
        if min_consecutive_up is not None and (
            isinstance(min_consecutive_up, bool) or not isinstance(min_consecutive_up, int)
            or min_consecutive_up < 1
        ):
            raise DataPoolError("INVALID_ARGUMENT", "min_consecutive_up must be positive")
        if (isinstance(batch_days, bool) or not isinstance(batch_days, int)
                or not 1 <= batch_days <= 31):
            raise DataPoolError("INVALID_ARGUMENT", "batch_days must be 1..31")
        if (isinstance(max_rows, bool) or not isinstance(max_rows, int)
                or not 1 <= max_rows <= 100_000):
            raise DataPoolError("INVALID_ARGUMENT", "max_rows must be 1..100000")
        if (isinstance(threads, bool) or not isinstance(threads, int) or not 1 <= threads <= 8):
            raise DataPoolError("INVALID_ARGUMENT", "threads must be 1..8")
        chosen = list(EVENT_AMOUNT_FIELDS) if fields is None else (
            [fields] if isinstance(fields, str) else list(fields)
        )
        if not chosen or len(chosen) != len(set(chosen)) or set(chosen) - set(EVENT_AMOUNT_FIELDS):
            raise DataPoolError("FIELD_UNSUPPORTED", "fields must be unique event amount fields")
        self.fields = chosen
        self.symbols = _normalize_requested_symbols(symbols)
        self.close_limit_up = close_limit_up
        self.min_consecutive_up = min_consecutive_up
        self.batch_days = batch_days
        self.max_rows = max_rows
        self.memory_limit = memory_limit
        self.threads = threads
        self.cursor = self.start
        self.closed = False
        self.completed = False
        _require_ready(self.root)
        with pool_lock(self.root), read_only_catalog(self.root) as conn:
            self._has_gap = any(
                row[0] == "consecutive_gap_sessions"
                for row in conn.execute("describe daily_limit_events").fetchall()
            )
        self._temp = tempfile.TemporaryDirectory(
            prefix="aspool-read-", dir=temp_directory
        )
        try:
            self.version = self._versions()
        except BaseException:
            self._temp.cleanup()
            raise

    def _configure(self, conn):
        try:
            conn.execute("SET memory_limit = ?", [self.memory_limit])
            conn.execute("SET threads = ?", [self.threads])
            conn.execute("SET temp_directory = ?", [self._temp.name])
        except duckdb.Error as exc:
            raise DataPoolError(
                "INVALID_ARGUMENT", f"Invalid DuckDB resource setting: {exc}"
            ) from exc

    def _versions(self):
        try:
            with pool_lock(self.root), read_only_catalog(self.root) as conn:
                return tuple(conn.execute("""
                    select p.trade_date, p.batch_id, p.published_at,
                           b.rule_version, b.computed_at, st.marked_at, st.reason
                    from daily_limit_publication p
                    join daily_limit_batches b on b.batch_id=p.batch_id
                    left join daily_limit_staleness st on st.trade_date=p.trade_date
                    where p.trade_date between ? and ? order by p.trade_date
                """, [self.start, self.end]).fetchall())
        except duckdb.Error as exc:
            raise DataPoolError("LIMIT_INVALID", str(exc)) from exc

    def _check_version(self):
        if self._versions() != self.version:
            raise DataPoolError(
                "LIMIT_REVISION_CHANGED",
                "Published batches or staleness changed during read; discard this run and retry",
            )

    def _event_rows(self, lo, hi):
        select = {
            "trade_date": "e.trade_date", "symbol": "e.symbol",
            "batch_id": "e.batch_id", "rule_version": "b.rule_version",
            "computed_at": "b.computed_at", "published_at": "p.published_at",
            "stale": "(st.trade_date is not null) AS stale",
            "stale_reason": "st.reason AS stale_reason",
            "consecutive_gap_sessions": (
                "e.consecutive_gap_sessions" if self._has_gap
                else "cast(null as integer) AS consecutive_gap_sessions"
            ),
        }
        columns = list(dict.fromkeys([*self.fields, "trade_date", "symbol", "batch_id"]))
        columns = [key for key in columns if key != "amount"]
        projections = ", ".join(select.get(key, f"e.{key}") for key in columns)
        clauses = ["e.trade_date between ? and ?"]
        params = [lo, hi]
        if self.symbols is not None:
            clauses.append("e.symbol IN (SELECT unnest(?))")
            params.append(self.symbols)
        if self.close_limit_up is not None:
            clauses.append("e.close_limit_up = ?")
            params.append(self.close_limit_up)
        if self.min_consecutive_up is not None:
            clauses.append("e.consecutive_up >= ?")
            params.append(self.min_consecutive_up)
        sql = f"""
            select {projections} from daily_limit_events e
            join daily_limit_publication p
              on p.trade_date=e.trade_date and p.batch_id=e.batch_id
            join daily_limit_batches b on b.batch_id=e.batch_id
            left join daily_limit_staleness st on st.trade_date=e.trade_date
            where {' and '.join(clauses)}
            order by e.trade_date, e.symbol limit {self.max_rows + 1}
        """
        with pool_lock(self.root), read_only_catalog(self.root) as conn:
            self._configure(conn)
            frame = conn.execute(sql, params).fetchdf()
        if len(frame) > self.max_rows:
            raise DataPoolError("LIMIT_TOO_LARGE", "Batch exceeds max_rows; reduce batch_days")
        if frame.duplicated(["trade_date", "symbol"]).any():
            raise DataPoolError("LIMIT_INVALID", "Duplicate published event keys")
        return frame

    def _attach_amount(self, frame, lo, hi):
        with pool_lock(self.root), read_only_catalog(self.root) as conn:
            self._configure(conn)
            amount = DailyStorage(self.root).event_amount(conn, frame, lo, hi)
        if amount.empty:
            frame["amount"] = pd.Series(float("nan"), index=frame.index)
            return frame
        return frame.merge(amount, on=["trade_date", "symbol"], how="left", validate="one_to_one")

    def __iter__(self):
        return self

    def __next__(self):
        if self.closed:
            raise StopIteration
        try:
            while self.cursor <= self.end:
                self._check_version()
                lo = self.cursor
                hi = min(self.end, lo + timedelta(days=self.batch_days - 1))
                self.cursor = hi + timedelta(days=1)
                frame = self._event_rows(lo, hi)
                if frame.empty:
                    continue
                if "amount" in self.fields:
                    frame = self._attach_amount(frame, lo, hi)
                self._check_version()
                result = frame[self.fields].copy()
                result.attrs.update(amount_unit="CNY", contract_version=1)
                return result
            self._check_version()
            self.completed = True
            self.close()
            raise StopIteration
        except StopIteration:
            raise
        except duckdb.Error as exc:
            self.close()
            raise DataPoolError("LIMIT_INVALID", str(exc)) from exc
        except BaseException:
            self.close()
            raise

    def close(self):
        if not self.closed:
            self.closed = True
            self._temp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        try:
            if exc_type is None and not self.closed and self.cursor > self.end:
                self._check_version()
                self.completed = True
        finally:
            self.close()
