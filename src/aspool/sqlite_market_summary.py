"""One-date stock breadth summaries, computed once for both supported scopes."""

from __future__ import annotations

import json
import math
import statistics
import time
from collections import Counter
from datetime import date
from decimal import Decimal

from .api_contract import DataPoolError
from .sqlite_daily_derived import finite, recompute_symbol_features

BUCKETS = (
    "limit_up",
    "limit_down",
    "pos_ge_10",
    "pos_08_10",
    "pos_06_08",
    "pos_04_06",
    "pos_02_04",
    "pos_00_02",
    "flat",
    "neg_00_02",
    "neg_02_04",
    "neg_04_06",
    "neg_06_08",
    "neg_08_10",
    "neg_le_10",
)
SCOPES = ("all_stocks", "exclude_known_st")


def return_bucket(close, pre_close, *, limit_up=False, limit_down=False):
    """Use exact decimal price comparisons; limit buckets take exclusive priority."""
    if not finite(close, positive=True) or not finite(pre_close, positive=True):
        raise ValueError("Return distribution requires positive finite prices")
    if limit_up and limit_down:
        raise ValueError("Both closing limit flags cannot be true")
    if limit_up:
        return "limit_up"
    if limit_down:
        return "limit_down"
    current, previous = Decimal(str(close)), Decimal(str(pre_close))
    delta = current - previous
    if delta == 0:
        return "flat"
    prefix = "pos" if delta > 0 else "neg"
    magnitude = abs(delta) * 100
    if magnitude >= 10 * previous:
        return "pos_ge_10" if delta > 0 else "neg_le_10"
    for edge in (8, 6, 4, 2):
        if magnitude >= edge * previous:
            return f"{prefix}_{edge:02d}_{edge + 2:02d}"
    return f"{prefix}_00_02"


class _Summary:
    def __init__(self):
        self.counts = Counter(
            {
                key: 0
                for key in (
                    "trading_count",
                    "st_unknown_count",
                    "valid_return_count",
                    "invalid_return_count",
                    "up_count",
                    "down_count",
                    "flat_count",
                    "strong_up_count",
                    "strong_down_count",
                    "amount_valid_count",
                    "turnover_valid_count",
                    "limit_known_count",
                    "no_limit_count",
                    "limit_unknown_count",
                    "limit_invalid_count",
                    "close_limit_up_count",
                    "touch_limit_up_count",
                    "close_limit_down_count",
                    "touch_limit_down_count",
                    "streak_unknown_count",
                    "first_board_count",
                    "second_plus_count",
                    "third_plus_count",
                    "fifth_plus_count",
                    "promotion_eligible_count",
                    "promotion_success_count",
                    "ma20_valid_count",
                    "above_ma20_count",
                )
            }
        )
        self.distribution = dict.fromkeys(BUCKETS, 0)
        self.ladder = Counter()
        self.returns, self.amounts, self.turnovers = [], [], []

    def add(self, row):
        counts = self.counts
        status = row["calc_status"]
        if status == "NO_TRADE":
            return
        if status == "INVALID":
            counts["limit_invalid_count"] += 1
            return
        if status != "TRADED" or row["limit_status"] not in ("KNOWN", "NO_LIMIT", "UNKNOWN"):
            raise DataPoolError("FEATURE_NOT_READY", "Daily limits have not been computed")
        if not finite(row["close"], positive=True):
            raise DataPoolError("DAILY_INVALID", "Traded feature has no valid close")
        counts["trading_count"] += 1
        counts["st_unknown_count"] += row["is_st"] is None
        counts[
            {
                "KNOWN": "limit_known_count",
                "NO_LIMIT": "no_limit_count",
                "UNKNOWN": "limit_unknown_count",
            }[row["limit_status"]]
        ] += 1
        for flag in ("close_limit_up", "touch_limit_up", "close_limit_down", "touch_limit_down"):
            counts[flag + "_count"] += row[flag] == 1
        if row["close_limit_up"] == 1:
            streak = row["consecutive_up"] if row["streak_known"] == 1 else None
            if streak is None:
                counts["streak_unknown_count"] += 1
            else:
                self.ladder[streak] += 1
                counts["first_board_count"] += streak == 1
                for threshold, name in ((2, "second"), (3, "third"), (5, "fifth")):
                    counts[name + "_plus_count"] += streak >= threshold
        previous = row["prior_consecutive_up"]
        if row["limit_status"] in ("KNOWN", "NO_LIMIT") and previous is not None and previous > 0:
            counts["promotion_eligible_count"] += 1
            counts["promotion_success_count"] += row["close_limit_up"] == 1
        if row["ma20"] is not None:
            counts["ma20_valid_count"] += 1
            counts["above_ma20_count"] += row["above_ma20"] == 1

        if finite(row["pre_close"], positive=True):
            current, previous = Decimal(str(row["close"])), Decimal(str(row["pre_close"]))
            value = float((current - previous) / previous)
            if not math.isfinite(value):
                raise DataPoolError("DAILY_INVALID", "Non-finite derived return")
            self.returns.append(value)
            counts["valid_return_count"] += 1
            counts["up_count"] += current > previous
            counts["down_count"] += current < previous
            counts["flat_count"] += current == previous
            counts["strong_up_count"] += value >= 0.03 - 1e-12
            counts["strong_down_count"] += value <= -0.03 + 1e-12
            bucket = return_bucket(
                row["close"],
                row["pre_close"],
                limit_up=row["close_limit_up"] == 1,
                limit_down=row["close_limit_down"] == 1,
            )
            self.distribution[bucket] += 1
        else:
            if row["close_limit_up"] == 1 or row["close_limit_down"] == 1:
                raise DataPoolError("DAILY_INVALID", "Closing limit has no valid reference")
            counts["invalid_return_count"] += 1
        for field, values in (("amount", self.amounts), ("turnover_rate", self.turnovers)):
            if finite(row[field]) and row[field] >= 0:
                values.append(row[field])

    def result(self):
        counts = dict(self.counts)
        counts["amount_valid_count"] = len(self.amounts)
        counts["turnover_valid_count"] = len(self.turnovers)

        def ratio(a, b, scale=1):
            return counts[a] / counts[b] * scale if counts[b] else None

        result = dict(
            counts,
            return_distribution_json=json.dumps(self.distribution, separators=(",", ":")),
            ladder_json=json.dumps(dict(sorted(self.ladder.items())), separators=(",", ":")),
            avg_return=statistics.fmean(self.returns) if self.returns else None,
            median_return=statistics.median(self.returns) if self.returns else None,
            amount_sum=math.fsum(self.amounts) if self.amounts else None,
            avg_turnover=statistics.fmean(self.turnovers) if self.turnovers else None,
            up_pct=ratio("up_count", "valid_return_count", 100),
            strong_up_pct=ratio("strong_up_count", "valid_return_count", 100),
            strong_down_pct=ratio("strong_down_count", "valid_return_count", 100),
            sealed_ratio=ratio("close_limit_up_count", "touch_limit_up_count"),
            promotion_ratio=ratio("promotion_success_count", "promotion_eligible_count"),
            above_ma20_pct=ratio("above_ma20_count", "ma20_valid_count"),
            max_consecutive_up=(
                max(self.ladder) if self.ladder else None if counts["close_limit_up_count"] else 0
            ),
        )
        assert sum(self.distribution.values()) == counts["valid_return_count"]
        assert self.distribution["limit_up"] == counts["close_limit_up_count"]
        assert self.distribution["limit_down"] == counts["close_limit_down_count"]
        return result


def recompute_daily_summary(conn, *, trade_date: str, inputs_changed=False, max_rows=100_000):
    """Aggregate one explicit market session in the caller's write transaction.

    The caller supplies only confirmed market sessions. inputs_changed=True
    touches the date's cache stamp even when changed constituents net to the
    same aggregate. Repeated calculation alone leaves timestamps unchanged.
    """
    if not conn.in_transaction:
        raise ValueError("An outer write transaction is required")
    date.fromisoformat(trade_date)
    if max_rows <= 0:
        raise ValueError("Invalid row budget")
    summaries = {scope: _Summary() for scope in SCOPES}
    cursor = conn.execute(
        "SELECT f.calc_status,f.limit_status,f.is_st,f.pre_close,"
        "f.close_limit_up,f.touch_limit_up,f.close_limit_down,f.touch_limit_down,"
        "f.consecutive_up,f.prior_consecutive_up,f.streak_known,f.ma20,f.above_ma20,"
        "b.close,b.amount,b.turnover_rate,f.updated_at AS feature_stamp,"
        "b.updated_at AS bar_stamp FROM daily_features f LEFT JOIN daily_bars b "
        "USING(symbol,trade_date) WHERE f.trade_date=?",
        (trade_date,),
    )
    fields = [column[0] for column in cursor.description]
    read_rows = 0
    input_stamp = 0
    try:
        for raw in cursor:
            read_rows += 1
            if read_rows > max_rows:
                raise DataPoolError("LOCAL_UPDATE_BUDGET_EXCEEDED", "Summary row budget exceeded")
            row = dict(zip(fields, raw))
            input_stamp = max(input_stamp, row["feature_stamp"], row["bar_stamp"] or 0)
            summaries["all_stocks"].add(row)
            if row["is_st"] != 1:
                summaries["exclude_known_st"].add(row)
    finally:
        cursor.close()
    conn.execute("SAVEPOINT daily_summary_recompute")
    changed = 0
    try:
        for scope, summary in summaries.items():
            values = dict(
                frequency="D",
                period_key=trade_date,
                scope=scope,
                period_start=trade_date,
                period_end=trade_date,
                as_of=trade_date,
                session_count=1,
                **summary.result(),
            )
            columns = list(values)
            old = conn.execute(
                "SELECT " + ",".join(columns) + ",updated_at FROM market_daily_summary "
                "WHERE frequency='D' AND period_key=? AND scope=?",
                (trade_date, scope),
            ).fetchone()
            if old is not None and tuple(values.values()) == old[:-1] and not inputs_changed:
                continue
            stamp = max(time.time_ns() // 1000, input_stamp + 1, old[-1] + 1 if old else 1)
            values["updated_at"] = stamp
            columns.append("updated_at")
            conn.execute(
                "INSERT INTO market_daily_summary ("
                + ",".join(columns)
                + ") VALUES ("
                + ",".join("?" for _ in columns)
                + ") "
                "ON CONFLICT(frequency,period_key,scope) DO UPDATE SET "
                + ",".join(column + "=excluded." + column for column in columns[3:]),
                tuple(values.values()),
            )
            changed += 1
        conn.execute("RELEASE daily_summary_recompute")
    except Exception:
        conn.execute("ROLLBACK TO daily_summary_recompute")
        conn.execute("RELEASE daily_summary_recompute")
        raise
    return {"read_rows": read_rows, "changed_rows": changed}


def recompute_market_window(
    conn,
    *,
    symbols,
    market_sessions,
    listed_days=None,
    max_rows=250_000,
):
    """Atomically calculate an explicit prepared-source maintenance window.

    Source fetch/upsert, reference preparation and market calendar resolution
    belong to the caller. This function does not infer market days or source
    completeness. Each supplied market date is aggregated once for both scopes.
    """
    if not conn.in_transaction:
        raise ValueError("An outer write transaction is required")
    days = sorted(set(market_sessions))
    if not days:
        return {"feature_rows": 0, "summary_rows": 0, "read_rows": 0}
    if any(date.fromisoformat(day).isoformat() != day for day in days):
        raise ValueError("Expected ISO market session dates")
    if max_rows <= 0:
        raise ValueError("Invalid row budget")
    feature_rows = summary_rows = read_rows = 0
    changed_days = set()
    conn.execute("SAVEPOINT daily_market_window")
    try:
        for symbol in sorted(set(symbols)):
            if read_rows >= max_rows:
                raise DataPoolError("LOCAL_UPDATE_BUDGET_EXCEEDED", "Window row budget exceeded")
            result = recompute_symbol_features(
                conn,
                symbol=symbol,
                start=days[0],
                end=days[-1],
                listed_days=(listed_days or {}).get(symbol),
                max_rows=max_rows - read_rows,
            )
            feature_rows += result["changed_rows"]
            read_rows += result["read_rows"]
            changed_days.update(result["changed_dates"])
        if not changed_days.issubset(days):
            raise DataPoolError("SOURCE_CONFLICT", "Changed feature date is not a market session")
        for day in days:
            if read_rows >= max_rows:
                raise DataPoolError("LOCAL_UPDATE_BUDGET_EXCEEDED", "Window row budget exceeded")
            result = recompute_daily_summary(
                conn,
                trade_date=day,
                inputs_changed=day in changed_days,
                max_rows=max_rows - read_rows,
            )
            summary_rows += result["changed_rows"]
            read_rows += result["read_rows"]
        conn.execute("RELEASE daily_market_window")
    except Exception:
        conn.execute("ROLLBACK TO daily_market_window")
        conn.execute("RELEASE daily_market_window")
        raise
    return {"feature_rows": feature_rows, "summary_rows": summary_rows, "read_rows": read_rows}
