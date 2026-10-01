"""Explicit repair of absent ST selections, retaining original source evidence."""

import time

from .sqlite_daily_derived import recompute_symbol_features
from .sqlite_reference_factors import select_is_st


def repair_symbol_st(conn, *, symbol, start, end):
    """Join a caller-owned transaction and update only selected ST and limit fields."""
    if not conn.in_transaction:
        raise ValueError("An outer write transaction is required")
    evidence = conn.execute(
        "SELECT f.trade_date,f.source_is_st,f.source_is_st_source,b.name,b.name_as_of "
        "FROM daily_features f LEFT JOIN daily_bars b USING(symbol,trade_date) "
        "WHERE f.symbol=? AND f.trade_date BETWEEN ? AND ? AND f.is_st IS NULL",
        (symbol, start, end),
    ).fetchall()
    count = len(evidence)
    if not count:
        return {"selected_rows": 0, "changed_dates": []}
    conn.execute(
        "UPDATE daily_features SET is_st=0,is_st_source='assumed:not_st',"
        "is_st_name_date=NULL,updated_at=? WHERE symbol=? AND trade_date BETWEEN ? AND ? "
        "AND is_st IS NULL", (time.time_ns() // 1000, symbol, start, end),
    )
    # Existing dated evidence retains precedence over the new default.
    for day, value, source, name, name_day in evidence:
        selected, origin, name_date = select_is_st(
            dated=[(value, source)], name=name, name_as_of=name_day, trade_date=day,
        )
        if origin != "assumed:not_st":
            conn.execute(
                "UPDATE daily_features SET is_st=?,is_st_source=?,is_st_name_date=? "
                "WHERE symbol=? AND trade_date=?",
                (selected, origin, name_date, symbol, day),
            )
    result = recompute_symbol_features(
        conn, symbol=symbol, start=start, end=end, limits_only=True,
        propagate=True, successor_window=0, max_affected_dates=260,
    )
    return dict(result, selected_rows=count)
