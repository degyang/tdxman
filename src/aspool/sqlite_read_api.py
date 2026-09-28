"""Projected reads and bounded event joins from one SQLite read snapshot."""

from __future__ import annotations

import re
from datetime import timedelta

import pandas as pd

from .api_contract import DAILY_FIELDS, DataPoolError
from .sqlite_stock_store import stock_connection

EVENT_FIELDS = {
    "trade_date": "f.trade_date",
    "symbol": "f.symbol",
    "close_limit_up": "f.close_limit_up",
    "close_limit_down": "f.close_limit_down",
    "touched_limit_up": "f.touch_limit_up",
    "touched_limit_down": "f.touch_limit_down",
    "limit_up_price": "f.limit_up_price",
    "limit_down_price": "f.limit_down_price",
    "consecutive_up": "f.consecutive_up",
    "prior_consecutive_up": "f.prior_consecutive_up",
    "streak_known": "f.streak_known",
    "is_st": "f.is_st",
    "limit_status": "f.limit_status",
    "amount": "b.amount",
    "updated_at": "max(f.updated_at,coalesce(b.updated_at,0))",
}
FEATURE_FIELDS = {
    "pre_close",
    "pre_close_source",
    "is_st",
    "is_st_source",
    "trading_status",
    "trading_status_source",
}
BOOLEAN_FIELDS = {
    "is_st",
    "close_limit_up",
    "close_limit_down",
    "touched_limit_up",
    "touched_limit_down",
    "streak_known",
}


def _fields(fields, allowed):
    chosen = (
        list(allowed) if fields is None else ([fields] if isinstance(fields, str) else list(fields))
    )
    if not chosen or len(set(chosen)) != len(chosen) or set(chosen) - set(allowed):
        raise DataPoolError("FIELD_UNSUPPORTED", "Expected unique supported fields")
    return chosen


def _bounds(start, end, required=False):
    from .limit_api import _bounds as bounds

    lo, hi = bounds(start, end)
    if required and (lo is None or hi is None):
        raise DataPoolError("INVALID_ARGUMENT", "Explicit start and end are required")
    return lo, hi


def _symbols(symbols):
    from .limit_api import _normalize_requested_symbols

    normalized = _normalize_requested_symbols(symbols)
    return sorted(set(normalized)) if normalized is not None else None


def _frame(rows, fields):
    frame = pd.DataFrame.from_records(rows, columns=fields)
    for name in fields:
        if name in {"date", "trade_date", "period_start", "period_end", "as_of"}:
            frame[name] = pd.to_datetime(frame[name])
        elif name in BOOLEAN_FIELDS:
            frame[name] = frame[name].astype("boolean")
        elif name.endswith("_count") or name in {
            "consecutive_up",
            "prior_consecutive_up",
            "max_consecutive_up",
            "updated_at",
        }:
            frame[name] = frame[name].astype("Int64")
        elif name in DAILY_FIELDS and DAILY_FIELDS[name][0] == "DOUBLE":
            frame[name] = pd.to_numeric(frame[name]).astype("float64")
    frame.attrs.update(
        contract_version=3,
        price_adjustment="raw",
        volume_unit="share",
        amount_unit="CNY",
        point_in_time=False,
        source="aspool.sqlite",
    )
    return frame


class StockSnapshot:
    """A caller-owned, pinned SQLite snapshot; no publication identities."""

    def __init__(self, root):
        self.root = root
        self.conn = None
        self.feature_table = "daily_features"
        self.summary_table = "market_daily_summary"
        self.factor_table = None

    def __enter__(self):
        if self.conn is not None:
            raise ValueError("Snapshot is already open")
        self._connection = stock_connection(self.root)
        self.conn = self._connection.__enter__()
        from .platform_v2 import layout_version

        if layout_version(self.root) >= 2:
            uri = (self.root / "features.sqlite").resolve().as_uri() + "?mode=ro"
            self.conn.execute("ATTACH DATABASE ? AS features", (uri,))
            adjustment_uri = (self.root / "adjustments.sqlite").resolve().as_uri() + "?mode=ro"
            self.conn.execute("ATTACH DATABASE ? AS adjustments", (adjustment_uri,))
            self.feature_table = "features.stock_daily_features"
            self.summary_table = "features.market_regime_features"
            self.factor_table = "adjustments.stock_adjustment_factors"
        self.conn.execute("BEGIN")
        self.conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
        if self.feature_table != "daily_features":
            raw = self.conn.execute(
                "SELECT revision FROM dataset_state WHERE dataset='stock_raw'"
            ).fetchone()
            states = self.conn.execute(
                "SELECT dataset,raw_revision,factor_revision,status FROM features.feature_state "
                "WHERE scope_key='all'"
            ).fetchall()
            factor = self.conn.execute(
                "SELECT revision FROM adjustments.adjustment_state "
                "WHERE dataset='stock_adjustment_factors'"
            ).fetchone()
            if raw is None or {row[0] for row in states} != {
                "stock_daily_features", "market_regime_features"
            } or factor is None or any(
                row[1] != raw[0] or row[2] != factor[0] or row[3] != "READY"
                for row in states
            ):
                self._connection.__exit__(None, None, None)
                self.conn = None
                raise DataPoolError(
                    "DERIVED_NOT_READY", "Layered feature revisions do not match stock_raw"
                )
        return self

    def __exit__(self, *error):
        if self.conn is not None:
            self._connection.__exit__(*error)
            self.conn = None

    def _require_open(self):
        if self.conn is None:
            raise ValueError("Use the reader inside its snapshot context")

    def _validate_bars(self, where, params, lookback=None):
        columns = ("open", "high", "low", "close", "volume", "amount")
        source = "SELECT " + ",".join(columns) + " FROM daily_bars b" + where
        values = list(params)
        if lookback:
            source += " ORDER BY b.trade_date DESC LIMIT ?"
            values.append(lookback)
        invalid = (
            " OR ".join(
                f"{name} IS NULL OR {name}<0 OR abs({name})>1.7976931348623157e308"
                for name in columns
            )
            + " OR high<low"
        )
        if self.conn.execute(
            "SELECT 1 FROM (" + source + ") WHERE " + invalid + " LIMIT 1", values
        ).fetchone():
            raise DataPoolError("DAILY_INVALID", "Invalid or incomplete daily OHLCV/amount")

    def read_daily(
        self, *, symbols=None, start=None, end=None, lookback=None, fields=None,
        adjust="none", adjustment_base=None,
    ):
        self._require_open()
        if adjust not in {"none", "qfq", "hfq"}:
            raise DataPoolError("INVALID_ARGUMENT", "adjust must be none, qfq or hfq")
        if adjustment_base is not None:
            try:
                adjustment_base = pd.Timestamp(adjustment_base).date().isoformat()
            except (TypeError, ValueError) as exc:
                raise DataPoolError("INVALID_ARGUMENT", "Invalid adjustment_base") from exc
            if adjust == "none":
                raise DataPoolError(
                    "INVALID_ARGUMENT", "adjustment_base requires qfq or hfq"
                )
        requested_fields = _fields(fields, DAILY_FIELDS)
        chosen = list(requested_fields)
        if adjust != "none":
            chosen.extend(name for name in ("symbol", "date") if name not in chosen)
        lo, hi = _bounds(start, end)
        names = _symbols(symbols)
        if lookback is not None and (
            isinstance(lookback, bool) or not isinstance(lookback, int) or lookback < 1
        ):
            raise DataPoolError("INVALID_ARGUMENT", "lookback must be positive")
        expressions = {name: f'b."{name}"' for name in DAILY_FIELDS}
        expressions.update(
            symbol="b.symbol",
            market="substr(b.symbol,8)",
            code="substr(b.symbol,1,6)",
            date="b.trade_date",
        )
        expressions.update({name: f'f."{name}"' for name in FEATURE_FIELDS})
        projection = ",".join(f'{expressions[name]} AS "{name}"' for name in chosen)
        join = (
            f" LEFT JOIN {self.feature_table} f USING(symbol,trade_date)"
            if set(chosen) & FEATURE_FIELDS
            else ""
        )
        clauses, params = [], []
        if lo:
            clauses.append("b.trade_date>=?")
            params.append(lo.isoformat())
        if hi:
            clauses.append("b.trade_date<=?")
            params.append(hi.isoformat())
        if names == []:
            return self._finish_daily(
                _frame([], chosen), adjust, adjustment_base, requested_fields
            )
        rows = []
        if lookback:
            # Seek each symbol's trailing N keys, never window all historical bars.
            if names is None:
                names, previous = [], ""
                while True:
                    item = self.conn.execute(
                        "SELECT symbol FROM daily_bars WHERE symbol>? ORDER BY symbol LIMIT 1",
                        (previous,),
                    ).fetchone()
                    if not item:
                        break
                    previous = item[0]
                    names.append(previous)
            for symbol in names:
                sql = (
                    f"SELECT {projection} FROM daily_bars b{join} WHERE "
                    + " AND ".join(["b.symbol=?", *clauses])
                    + " ORDER BY b.trade_date DESC LIMIT ?"
                )
                chunk = self.conn.execute(
                    sql, [symbol, *params, min(lookback, 500_001 - len(rows))]
                ).fetchall()
                rows.extend(reversed(chunk))
                if chunk and len(rows) <= 500_000:
                    self._validate_bars(
                        " WHERE " + " AND ".join(["b.symbol=?", *clauses]),
                        [symbol, *params],
                        lookback,
                    )
                if len(rows) > 500_000:
                    raise DataPoolError("DAILY_TOO_LARGE", "DataFrame exceeds 500000 rows")
        else:
            if names is not None:
                clauses.append("b.symbol IN (" + ",".join("?" for _ in names) + ")")
                params.extend(names)
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            rows = self.conn.execute(
                f"SELECT {projection} FROM daily_bars b{join}{where} "
                "ORDER BY b.symbol,b.trade_date LIMIT 500001",
                params,
            ).fetchall()
            if len(rows) > 500_000:
                raise DataPoolError("DAILY_TOO_LARGE", "DataFrame exceeds 500000 rows")
            if rows:
                self._validate_bars(where, params)
        return self._finish_daily(
            _frame(rows, chosen), adjust, adjustment_base, requested_fields
        )

    def _finish_daily(self, frame, adjust, adjustment_base, requested_fields):
        result = self._adjust_daily(frame, adjust, adjustment_base)
        if list(result.columns) == list(requested_fields):
            return result
        attrs = dict(result.attrs)
        result = result[list(requested_fields)].copy()
        result.attrs.update(attrs)
        return result

    def _adjust_daily(self, frame, adjust, adjustment_base):
        frame.attrs.update(adjustment=adjust, adjustment_base=adjustment_base)
        if adjust == "none" or frame.empty:
            return frame
        price_fields = [
            name for name in ("open", "high", "low", "close", "pre_close") if name in frame
        ]
        if not price_fields:
            return frame
        if self.factor_table is None or "symbol" not in frame or "date" not in frame:
            raise DataPoolError(
                "ADJUSTMENT_NOT_READY",
                "Adjusted reads require symbol/date fields and the layered factor store",
            )
        result = frame.copy()
        factors_used = {}
        for symbol, positions in result.groupby("symbol", sort=False).groups.items():
            rows = self.conn.execute(
                f"SELECT valid_from,valid_through,cumulative_factor FROM {self.factor_table} "
                "WHERE symbol=? ORDER BY valid_from",
                (symbol,),
            ).fetchall()
            if not rows:
                raise DataPoolError(
                    "ADJUSTMENT_NOT_READY", f"No adjustment factors for {symbol}"
                )
            dates = result.loc[positions, "date"].dt.strftime("%Y-%m-%d")
            values = pd.Series(float("nan"), index=positions, dtype="float64")
            for valid_from, valid_through, factor in rows:
                mask = (dates >= valid_from) & (dates <= valid_through)
                values.loc[dates.index[mask]] = factor
            if values.isna().any():
                raise DataPoolError(
                    "ADJUSTMENT_NOT_READY", f"Factor coverage is incomplete for {symbol}"
                )
            if adjustment_base:
                reference = next(
                    (
                        factor
                        for valid_from, valid_through, factor in rows
                        if valid_from <= adjustment_base <= valid_through
                    ),
                    None,
                )
            elif adjust == "qfq":
                reference = values.loc[dates.idxmax()]
            else:
                reference = rows[0][2]
            if reference is None or reference <= 0:
                raise DataPoolError(
                    "ADJUSTMENT_NOT_READY", f"Adjustment base is uncovered for {symbol}"
                )
            multiplier = values / reference
            for field in price_fields:
                result.loc[positions, field] = (
                    pd.to_numeric(result.loc[positions, field]) * multiplier.loc[positions]
                )
            factors_used[symbol] = {
                "base": adjustment_base or ("query_end" if adjust == "qfq" else "history_start"),
                "factor": reference,
            }
        result.attrs.update(frame.attrs)
        result.attrs.update(
            adjustment=adjust,
            price_adjustment=adjust,
            adjustment_factors=factors_used,
        )
        return result

    def read_daily_features(self, *, symbols=None, start=None, end=None, fields=None):
        self._require_open()
        lo, hi = _bounds(start, end)
        names = _symbols(symbols)
        if "." in self.feature_table:
            allowed = [
                row[1]
                for row in self.conn.execute("PRAGMA features.table_info(stock_daily_features)")
            ]
        else:
            allowed = [row[1] for row in self.conn.execute("PRAGMA table_info(daily_features)")]
        chosen = _fields(fields, allowed)
        clauses, params = [], []
        for bound, sign in ((lo, ">="), (hi, "<=")):
            if bound:
                clauses.append("trade_date" + sign + "?")
                params.append(bound.isoformat())
        if names is not None:
            clauses.append("symbol IN (" + ",".join("?" for _ in names) + ")" if names else "0")
            params.extend(names)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.conn.execute(
            "SELECT "
            + ",".join('"' + name + '"' for name in chosen)
            + f" FROM {self.feature_table}"
            + where
            + " ORDER BY symbol,trade_date LIMIT 500001",
            params,
        ).fetchall()
        if len(rows) > 500_000:
            raise DataPoolError("DAILY_TOO_LARGE", "Use a smaller feature range")
        return _frame(rows, chosen)

    def read_market_summary(
        self, *, start, end, frequency="D", scope="all_stocks", fields=None, closed_only=True
    ):
        self._require_open()
        lo, hi = _bounds(start, end, required=True)
        if frequency != "D":
            raise DataPoolError("FREQUENCY_NOT_READY", "Only daily summaries are implemented")
        if scope not in {"all_stocks", "exclude_known_st"} or not isinstance(closed_only, bool):
            raise DataPoolError("INVALID_ARGUMENT", "Invalid scope or closed_only")
        from .sqlite_market_metadata import describe_fields

        allowed = describe_fields(self.conn, scope=scope, table=self.summary_table)
        chosen = _fields(fields, allowed)
        rows = self.conn.execute(
            "SELECT "
            + ",".join('"' + name + '"' for name in chosen)
            + f" FROM {self.summary_table} WHERE frequency=? AND scope=? "
            "AND period_key BETWEEN ? AND ? ORDER BY period_key",
            (frequency, scope, lo.isoformat(), hi.isoformat()),
        ).fetchall()
        if (
            not rows
            and not self.conn.execute(
                f"SELECT 1 FROM {self.summary_table} WHERE frequency='D' LIMIT 1"
            ).fetchone()
        ):
            raise DataPoolError("FEATURE_NOT_READY", "Daily summary has not been calculated")
        return _frame(rows, chosen)

    def read_market_daily(self, *, start, end, scope="all_stocks", fields=None):
        return self.read_market_summary(start=start, end=end, scope=scope, fields=fields)

    def read_limit_events(
        self, *, trade_date=None, start=None, end=None, symbols=None, fields=None
    ):
        self._require_open()
        if trade_date is not None:
            if start is not None or end is not None:
                raise DataPoolError("INVALID_ARGUMENT", "Choose trade_date or a range")
            start = end = trade_date
        chosen = _fields(fields, EVENT_FIELDS)
        lo, hi = _bounds(start, end, required=True)
        sql, params = _event_query(
            chosen, lo, hi, _symbols(symbols), None, None, self.feature_table
        )
        rows = self.conn.execute(sql + " LIMIT 500001", params).fetchall()
        if len(rows) > 500_000:
            raise DataPoolError("LIMIT_TOO_LARGE", "Use iter_limit_events_with_amount")
        return _frame(rows, chosen)


def _event_query(
    fields, lo, hi, symbols, close_limit_up, min_consecutive_up,
    feature_table="daily_features",
):
    clauses = [
        "f.trade_date BETWEEN ? AND ?",
        "f.calc_status='TRADED'",
        "(f.touch_limit_up=1 OR f.touch_limit_down=1)",
        # This implied predicate matches the partial-index definition exactly,
        # allowing SQLite to use its event-only covering index after ATTACH.
        "(f.close_limit_up=1 OR f.touch_limit_up=1 OR "
        "f.close_limit_down=1 OR f.touch_limit_down=1)",
    ]
    params = [lo.isoformat(), hi.isoformat()]
    if symbols is not None:
        clauses.append("f.symbol IN (" + ",".join("?" for _ in symbols) + ")" if symbols else "0")
        params.extend(symbols)
    if close_limit_up is not None:
        clauses.append("f.close_limit_up=?")
        params.append(int(close_limit_up))
    if min_consecutive_up is not None:
        clauses.append("f.consecutive_up>=?")
        params.append(min_consecutive_up)
    # A single SQL snapshot joins amount only when the caller asks for it.
    join = (
        " LEFT JOIN daily_bars b USING(symbol,trade_date)"
        if {"amount", "updated_at"} & set(fields)
        else ""
    )
    projection = ",".join(f'{EVENT_FIELDS[name]} AS "{name}"' for name in fields)
    return f"SELECT {projection} FROM {feature_table} f{join} WHERE " + " AND ".join(
        clauses
    ) + " ORDER BY f.trade_date,f.symbol", params


class EventAmountBatches:
    def __init__(
        self,
        root,
        *,
        start,
        end,
        symbols=None,
        fields=None,
        close_limit_up=None,
        min_consecutive_up=None,
        batch_days=7,
        max_rows=25_000,
        memory_limit="512MB",
        threads=2,
        temp_directory=None,
    ):
        # SQLite uses one read connection. The compatibility memory hint limits
        # its page cache; Python/frame overhead is measured separately by RSS.
        match = re.fullmatch(r"([1-9][0-9]*)(KB|MB|GB)", str(memory_limit).upper())
        if match is None:
            raise DataPoolError("INVALID_ARGUMENT", "Invalid memory_limit")
        kib = int(match[1]) * {"KB": 1, "MB": 1024, "GB": 1024 * 1024}[match[2]]
        if not 8192 <= kib <= 1024 * 1024:
            raise DataPoolError("INVALID_ARGUMENT", "memory_limit must be 8MiB..1GiB")
        if isinstance(threads, bool) or not isinstance(threads, int) or not 1 <= threads <= 8:
            raise DataPoolError("INVALID_ARGUMENT", "threads must be 1..8")
        self.cache_kib = kib // 2
        self.root = root
        self.lo, self.hi = _bounds(start, end, required=True)
        self.fields = _fields(fields, EVENT_FIELDS)
        self.symbols = _symbols(symbols)
        if (
            isinstance(max_rows, bool)
            or not isinstance(max_rows, int)
            or not 1 <= max_rows <= 100_000
        ):
            raise DataPoolError("INVALID_ARGUMENT", "max_rows must be 1..100000")
        if (
            isinstance(batch_days, bool)
            or not isinstance(batch_days, int)
            or not 1 <= batch_days <= 31
        ):
            raise DataPoolError("INVALID_ARGUMENT", "batch_days must be 1..31")
        if close_limit_up is not None and not isinstance(close_limit_up, bool):
            raise DataPoolError("INVALID_ARGUMENT", "close_limit_up must be boolean")
        if min_consecutive_up is not None and (
            isinstance(min_consecutive_up, bool)
            or not isinstance(min_consecutive_up, int)
            or min_consecutive_up < 1
        ):
            raise DataPoolError("INVALID_ARGUMENT", "min_consecutive_up must be positive")
        self.close_limit_up, self.min_consecutive_up = close_limit_up, min_consecutive_up
        self.max_rows, self.batch_days = max_rows, batch_days
        self.reader = None
        self.cursor = None
        self.next_date = self.lo
        self.closed = False
        self.completed = False

    def __enter__(self):
        if self.reader is not None or self.closed:
            raise ValueError("Iterator context can only be entered once")
        self.reader = StockSnapshot(self.root).__enter__()
        self.reader.conn.execute(f"PRAGMA cache_size=-{self.cache_kib}")
        return self

    def __iter__(self):
        return self

    def __next__(self):
        if self.closed:
            raise StopIteration
        if self.reader is None:
            raise ValueError("Use the iterator as a context manager")
        while True:
            if self.cursor is None:
                if self.next_date > self.hi:
                    self.completed = True
                    self.close()
                    raise StopIteration
                end = min(self.hi, self.next_date + timedelta(days=self.batch_days - 1))
                sql, params = _event_query(
                    self.fields,
                    self.next_date,
                    end,
                    self.symbols,
                    self.close_limit_up,
                    self.min_consecutive_up,
                    self.reader.feature_table,
                )
                self.cursor = self.reader.conn.execute(sql, params)
                self.next_date = end + timedelta(days=1)
            rows = self.cursor.fetchmany(self.max_rows)
            if rows:
                return _frame(rows, self.fields)
            self.cursor.close()
            self.cursor = None

    def close(self):
        if self.cursor is not None:
            self.cursor.close()
            self.cursor = None
        if self.reader is not None:
            self.reader.__exit__(None, None, None)
            self.reader = None
        self.closed = True

    def __exit__(self, *error):
        self.close()


def describe(root):
    with StockSnapshot(root) as reader:
        from .sqlite_market_metadata import describe_fields

        market_fields = describe_fields(reader.conn, table=reader.summary_table)
        first, last, days = reader.conn.execute(
            f"SELECT min(period_key),max(period_key),count(*) FROM {reader.summary_table} "
            "WHERE frequency='D' AND scope='all_stocks'"
        ).fetchone()
    return dict(
        contract_version=3,
        backend="sqlite",
        ready=bool(days),
        start=first,
        end=last,
        market_frequencies=["D"],
        scopes=["all_stocks", "exclude_known_st"],
        event_fields=list(EVENT_FIELDS),
        market_fields=market_fields,
        point_in_time=False,
        capabilities=dict(
            daily_limit_summary=True,
            daily_limit_events=True,
            event_amount_join=True,
            read_snapshot=True,
            daily_limit_coverage=True,
            daily_limit_exceptions=True,
        ),
    )


def read_limit_summary(root, *, start=None, end=None, scope="all_stocks"):
    with StockSnapshot(root) as reader:
        bounds = reader.conn.execute(
            f"SELECT min(period_key),max(period_key) FROM {reader.summary_table} "
            "WHERE frequency='D'"
        ).fetchone()
        if bounds[0] is None:
            raise DataPoolError("FEATURE_NOT_READY", "Daily summary has not been calculated")
        result = reader.read_market_daily(
            start=start or bounds[0], end=end or bounds[1], scope=scope
        )
    result = result.rename(
        columns={
            "period_key": "trade_date",
            "trading_count": "symbol_count",
            "limit_known_count": "known_count",
            "limit_unknown_count": "unknown_count",
            "limit_invalid_count": "invalid_count",
            "touch_limit_up_count": "touched_limit_up_count",
            "touch_limit_down_count": "touched_limit_down_count",
        }
    )

    result["trade_date"] = pd.to_datetime(result["trade_date"])
    return result


def read_limit_coverage(root, *, start=None, end=None):
    """Project the v2 coverage shape from current daily feature denominators."""
    with StockSnapshot(root) as reader:
        lo, hi = _bounds(start, end)
        clauses = ["frequency='D'", "scope='all_stocks'"]
        params = []
        if lo:
            clauses.append("period_key>=?")
            params.append(lo.isoformat())
        if hi:
            clauses.append("period_key<=?")
            params.append(hi.isoformat())
        rows = reader.conn.execute(
            "SELECT period_key,NULL,'all_stocks',NULL,NULL,"
            "coalesce(limit_known_count,0)+coalesce(no_limit_count,0)+"
            "coalesce(limit_unknown_count,0)+coalesce(limit_invalid_count,0),"
            "limit_known_count,limit_unknown_count,no_limit_count,limit_invalid_count,"
            f"'ready',0,NULL FROM {reader.summary_table} WHERE "
            + " AND ".join(clauses)
            + " ORDER BY period_key",
            params,
        ).fetchall()
    fields = [
        "trade_date",
        "batch_id",
        "scope_id",
        "rule_version",
        "computed_at",
        "processed_count",
        "known_count",
        "unknown_count",
        "no_limit_count",
        "invalid_count",
        "status",
        "stale",
        "stale_reason",
    ]
    return _frame(rows, fields)


def read_limit_exceptions(
    root, *, trade_date=None, start=None, end=None, symbols=None
):
    """Project unresolved daily rows without reviving publication tables."""
    if trade_date is not None:
        if start is not None or end is not None:
            raise DataPoolError("INVALID_ARGUMENT", "Choose trade_date or a range")
        start = end = trade_date
    lo, hi = _bounds(start, end)
    names = _symbols(symbols)
    clauses = [
        "(limit_status IN ('UNKNOWN','INVALID') OR "
        "trading_status IN ('MISSING','INVALID'))"
    ]
    params = []
    if lo:
        clauses.append("trade_date>=?")
        params.append(lo.isoformat())
    if hi:
        clauses.append("trade_date<=?")
        params.append(hi.isoformat())
    if names is not None:
        clauses.append("symbol IN (" + ",".join("?" for _ in names) + ")" if names else "0")
        params.extend(names)
    with StockSnapshot(root) as reader:
        rows = reader.conn.execute(
            "SELECT trade_date,symbol,CASE WHEN limit_status='INVALID' OR "
            "trading_status='INVALID' THEN 'INVALID' ELSE 'UNKNOWN' END,"
            "coalesce(limit_reason,lower(trading_status),'unclassified'),"
            f"'limit_status',NULL FROM {reader.feature_table} WHERE "
            + " AND ".join(clauses)
            + " ORDER BY trade_date,symbol",
            params,
        ).fetchall()
    return _frame(
        rows,
        ["trade_date", "symbol", "kind", "reason", "affected_fields", "batch_id"],
    )
