"""Recalculate bounded volume dependencies inside the daily writer transaction."""

import math
import time

from .api_contract import DataPoolError

VOLUME_SOURCE = "derived:five_valid_volumes"
TURNOVER_SOURCE = "derived:dated_float_share"


def recompute_volume_metrics(conn, symbol, days, *, propagate_days=None):
    """A volume repair changes its own turnover and at most five later volume ratios."""
    targets = set(days)
    for day in days if propagate_days is None else propagate_days:
        targets.update(
            r[0]
            for r in conn.execute(
                "SELECT b.trade_date FROM daily_bars b "
                "LEFT JOIN daily_features f USING(symbol,trade_date) "
                "WHERE b.symbol=? AND b.trade_date>? AND b.volume>0 AND b.low>0 "
                "AND b.low<=min(b.open,b.close) AND max(b.open,b.close)<=b.high "
                "AND coalesce(f.trading_status,'') NOT IN ('SUSPENDED','停牌') "
                "ORDER BY b.trade_date LIMIT 5",
                (symbol, day),
            )
        )
    if len(targets) > 60:
        raise DataPoolError("LOCAL_UPDATE_BUDGET_EXCEEDED", "Volume dependency dates exceeded")
    changed, reads = set(), 0
    for day in sorted(targets):
        row = conn.execute(
            "SELECT b.volume,b.float_share,b.float_share_source,b.vol_ratio,b.vol_ratio_source,"
            "b.turnover_rate,b.turnover_rate_source,b.updated_at,b.open,b.high,b.low,b.close,"
            "f.trading_status FROM daily_bars b "
            "LEFT JOIN daily_features f USING(symbol,trade_date) "
            "WHERE b.symbol=? AND b.trade_date=?",
            (symbol, day),
        ).fetchone()
        if row is None:
            continue
        reads += 1
        (
            volume,
            shares,
            shares_source,
            ratio,
            ratio_source,
            turnover,
            turnover_source,
            stamp,
            op,
            hi,
            lo,
            cl,
            status,
        ) = row
        valid = (
            volume is not None
            and volume > 0
            and all(v is not None for v in (op, hi, lo, cl))
            and 0 < lo <= min(op, cl) <= max(op, cl) <= hi
            and status not in ("SUSPENDED", "停牌")
        )
        updates = {}
        if ratio is None or ratio_source == VOLUME_SOURCE:
            previous = conn.execute(
                "SELECT b.volume FROM daily_bars b "
                "LEFT JOIN daily_features f USING(symbol,trade_date) "
                "WHERE b.symbol=? AND b.trade_date<? AND b.volume>0 AND b.low>0 "
                "AND b.low<=min(b.open,b.close) AND max(b.open,b.close)<=b.high "
                "AND coalesce(f.trading_status,'') NOT IN ('SUSPENDED','停牌') "
                "ORDER BY b.trade_date DESC LIMIT 5",
                (symbol, day),
            ).fetchall()
            reads += len(previous)
            value = (
                volume / (math.fsum(r[0] for r in previous) / 5)
                if valid and len(previous) == 5
                else None
            )
            if value != ratio or (value is not None and ratio_source != VOLUME_SOURCE):
                updates.update(vol_ratio=value, vol_ratio_source=VOLUME_SOURCE)
        if turnover is None or turnover_source == TURNOVER_SOURCE:
            reliable_shares = shares_source and not shares_source.startswith(
                ("raw_fallback:", "unknown:")
            )
            value = (
                volume / shares * 100
                if valid and reliable_shares and shares and shares > 0
                else None
            )
            if value != turnover or (value is not None and turnover_source != TURNOVER_SOURCE):
                updates.update(turnover_rate=value, turnover_rate_source=TURNOVER_SOURCE)
        if updates:
            updates["updated_at"] = max(time.time_ns() // 1000, stamp + 1)
            conn.execute(
                "UPDATE daily_bars SET "
                + ",".join(k + "=?" for k in updates)
                + " WHERE symbol=? AND trade_date=?",
                (*updates.values(), symbol, day),
            )
            changed.add(day)
    return changed, reads
