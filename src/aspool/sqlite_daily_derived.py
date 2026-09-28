"""Bounded daily limit, streak and adjusted MA20 calculations for the stock store.

Reference prices, dated ST and selected factors must be prepared by the caller.
These functions join its transaction; they do not fetch sources or publish data.
"""

from __future__ import annotations

import math
import re
import sqlite3
import time
from collections import deque
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from tdxman.codec.price_rules import resolve_limit_rule
from tdxman.models.enums import Market

from .api_contract import DataPoolError

DERIVED_COLUMNS = (
    "calc_status",
    "limit_status",
    "limit_reason",
    "limit_up_price",
    "limit_down_price",
    "touch_limit_up",
    "close_limit_up",
    "touch_limit_down",
    "close_limit_down",
    "consecutive_up",
    "prior_consecutive_up",
    "streak_known",
    "ma20",
    "above_ma20",
)
_JOIN_COLUMNS = (
    "f.symbol,f.trade_date,b.trade_date AS bar_date,b.open,b.high,b.low,b.close,"
    "b.volume,b.amount,f.trading_status,f.pre_close,f.is_st,f.updated_at,"
    + ",".join("f." + field for field in DERIVED_COLUMNS)
)


def finite(value, *, positive=False):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and (not positive or value > 0)
    )


def classify_trading(row: Mapping) -> str:
    """Missing bars and confirmed suspension share the same statistical treatment."""
    if row.get("bar_date") is None:
        return "NO_TRADE"
    if row.get("trading_status") in ("SUSPENDED", "停牌"):
        if any(finite(row.get(key)) and row[key] > 0 for key in ("volume", "amount")):
            raise DataPoolError("SOURCE_CONFLICT", "Suspended bar has actual turnover")
        return "NO_TRADE"
    prices = [row.get(key) for key in ("open", "high", "low", "close")]
    if not all(finite(value, positive=True) for value in prices):
        return "INVALID"
    op, hi, lo, cl = prices
    return "TRADED" if lo <= min(op, cl) <= max(op, cl) <= hi else "INVALID"


def derive_daily_row(
    row: Mapping,
    *,
    previous_streak: int | None,
    prior_closes: Sequence[tuple[float, float | None]] = (),
    current_factor: float | None = None,
    listed_days: int | None = None,
    observed_sessions: int | None = None,
) -> tuple[dict, int | None]:
    """Return owned columns and next streak state; no-trade rows freeze that state.

    prior_closes contains at most the preceding 19 valid trading closes and their
    date-specific factors. No adjustment evidence means an unavailable MA20.
    """
    status = classify_trading(row)
    result = dict.fromkeys(DERIVED_COLUMNS)
    result["calc_status"] = status
    if status == "NO_TRADE":
        return result, previous_streak
    if status == "INVALID":
        result.update(limit_status="INVALID", limit_reason="invalid_ohlc", streak_known=0)
        return result, None

    code, market = row["symbol"].split(".")
    raw_st = row.get("is_st")
    rule = resolve_limit_rule(
        Market[market],
        code,
        "",
        date.fromisoformat(row["trade_date"]),
        st_status=bool(raw_st) if raw_st in (0, 1) else None,
        listed_days=listed_days,
        observed_sessions=observed_sessions,
    )
    result["prior_consecutive_up"] = previous_streak
    next_streak = None
    if rule.is_no_limit:
        result.update(
            limit_status="NO_LIMIT",
            touch_limit_up=0,
            close_limit_up=0,
            touch_limit_down=0,
            close_limit_down=0,
            consecutive_up=0,
            streak_known=1,
        )
        next_streak = 0
    elif not rule.is_known:
        reason = rule.reason or ""
        result.update(
            limit_status="UNKNOWN",
            streak_known=0,
            limit_reason=(
                "missing_st"
                if "ST" in reason
                else "missing_listing_date"
                if "上市" in reason
                else "unsupported_rule"
            ),
        )
    elif not finite(row.get("pre_close"), positive=True):
        result.update(limit_status="UNKNOWN", limit_reason="missing_reference", streak_known=0)
    else:

        def cents(value):
            return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        reference = cents(row["pre_close"])
        pct = Decimal(str(rule.rule.limit_pct))
        up, down = cents(reference * (1 + pct)), cents(reference * (1 - pct))
        if not 0 < down < up:
            result.update(
                limit_status="UNKNOWN",
                limit_reason="invalid_reference",
                streak_known=0,
            )
        else:
            close_up, close_down = cents(row["close"]) >= up, cents(row["close"]) <= down
            next_streak = (
                (previous_streak + 1 if previous_streak is not None else None) if close_up else 0
            )
            result.update(
                limit_status="KNOWN",
                limit_up_price=float(up),
                limit_down_price=float(down),
                close_limit_up=int(close_up),
                close_limit_down=int(close_down),
                touch_limit_up=int(cents(row["high"]) >= up),
                touch_limit_down=int(cents(row["low"]) <= down),
                consecutive_up=next_streak,
                streak_known=int(next_streak is not None),
            )

    history = list(prior_closes[-19:]) + [(row["close"], current_factor)]
    if len(history) == 20 and all(finite(factor, positive=True) for _, factor in history):
        adjusted = [close * (factor / current_factor) for close, factor in history]
        if all(finite(value, positive=True) for value in adjusted):
            mean = math.fsum(value / 20 for value in adjusted)
            if finite(mean, positive=True):
                result.update(ma20=mean, above_ma20=int(row["close"] > mean))
    return result, next_streak


def _factor_at(conn, symbol, day):
    row = conn.execute(
        "SELECT cumulative_factor,valid_from,valid_through FROM corporate_actions "
        "WHERE symbol=? AND record_kind='factor' AND effective_date<=? "
        "ORDER BY effective_date DESC LIMIT 1",
        (symbol, day),
    ).fetchone()
    return row[0] if row and row[1] <= day <= row[2] else None


def _rows(cursor):
    fields = [item[0] for item in cursor.description]
    try:
        for row in cursor:
            yield dict(zip(fields, row))
    finally:
        cursor.close()


def recompute_symbol_features(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    start: str,
    end: str,
    listed_days: Mapping[str, int] | None = None,
    max_rows: int = 250_000,
    propagate: bool = False,
    successor_window: int = 20,
    max_affected_dates: int = 60,
) -> dict:
    """Recompute an explicit range, using bounded warmup and the caller's transaction.

    With propagate=True, start/end bound changed inputs. Continue until the
    successor window and streak state reconverge, subject to hard budgets.
    Use successor_window=0 only for changes that cannot affect MA20 membership
    or adjustment. References/factors must already reflect their dependencies.
    Without propagation, only the explicit range is written (maintenance use).
    """
    if not conn.in_transaction:
        raise ValueError("An outer write transaction is required")
    if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", symbol):
        raise ValueError("Expected canonical stock symbol")
    if (
        date.fromisoformat(start).isoformat() != start
        or date.fromisoformat(end).isoformat() != end
        or start > end
        or max_rows <= 0
        or max_affected_dates <= 0
        or successor_window not in (0, 20)
    ):
        raise ValueError("Invalid range or row budget")
    visited = 0

    def counted(rows):
        nonlocal visited
        for row in rows:
            visited += 1
            if visited > max_rows:
                raise DataPoolError("LOCAL_UPDATE_BUDGET_EXCEEDED", "Feature row budget exceeded")
            yield row

    conn.execute("SAVEPOINT daily_features_recompute")
    try:
        history = deque(maxlen=19)
        previous_streak, found_predecessor = None, False
        warm = _rows(
            conn.execute(
                f"SELECT {_JOIN_COLUMNS} FROM daily_features f LEFT JOIN daily_bars b "
                "USING(symbol,trade_date) WHERE f.symbol=? AND f.trade_date<? "
                "ORDER BY f.trade_date DESC",
                (symbol, start),
            )
        )
        try:
            for row in counted(warm):
                status = classify_trading(row)
                if not found_predecessor and status != "NO_TRADE":
                    found_predecessor = True
                    if status == "TRADED" and row["streak_known"] == 1:
                        previous_streak = row["consecutive_up"]
                if status == "TRADED":
                    history.appendleft((row["close"], _factor_at(conn, symbol, row["trade_date"])))
                    if len(history) == 19:
                        break
        finally:
            warm.close()
        # Six observed trading rows are sufficient to exclude every supported
        # IPO window. A smaller number is never promoted to an exact listing age.
        observed = min(len(history), 6)
        changed_dates = []
        processed = 0
        successors = 0
        predicate = "f.trade_date>=?" if propagate else "f.trade_date BETWEEN ? AND ?"
        parameters = (symbol, start) if propagate else (symbol, start, end)
        rows = _rows(
            conn.execute(
                f"SELECT {_JOIN_COLUMNS} FROM daily_features f LEFT JOIN daily_bars b "
                f"USING(symbol,trade_date) WHERE f.symbol=? AND {predicate} "
                "ORDER BY f.trade_date LIMIT ?",
                (*parameters, max_rows + 1),
            )
        )
        try:
            for row in counted(rows):
                day = row["trade_date"]
                factor = _factor_at(conn, symbol, day)
                status = classify_trading(row)
                if status == "TRADED":
                    observed = min(observed + 1, 6)
                entering_streak = previous_streak
                values, previous_streak = derive_daily_row(
                    row,
                    previous_streak=previous_streak,
                    prior_closes=list(history),
                    current_factor=factor,
                    observed_sessions=observed,
                    listed_days=(listed_days or {}).get(day),
                )
                processed += 1
                if status == "TRADED":
                    history.append((row["close"], factor))
                    successors += day > end
                different = any(values[field] != row[field] for field in DERIVED_COLUMNS)
                if different:
                    if propagate and len(changed_dates) >= max_affected_dates:
                        raise DataPoolError(
                            "LOCAL_UPDATE_BUDGET_EXCEEDED", "Propagation date budget exceeded"
                        )
                    stamp = max(time.time_ns() // 1000, row["updated_at"] + 1)
                    conn.execute(
                        "UPDATE daily_features SET "
                        + ",".join(field + "=?" for field in DERIVED_COLUMNS)
                        + ",updated_at=? WHERE symbol=? AND trade_date=?",
                        (*(values[field] for field in DERIVED_COLUMNS), stamp, symbol, day),
                    )
                    changed_dates.append(day)
                if (
                    propagate
                    and day > end
                    and status == "TRADED"
                    and successors >= successor_window
                    and not different
                    and entering_streak == row["prior_consecutive_up"]
                ):
                    break
        finally:
            rows.close()
        conn.execute("RELEASE daily_features_recompute")
    except Exception:
        conn.execute("ROLLBACK TO daily_features_recompute")
        conn.execute("RELEASE daily_features_recompute")
        raise
    return {
        "processed_rows": processed,
        "read_rows": visited,
        "changed_rows": len(changed_dates),
        "changed_dates": changed_dates,
    }
