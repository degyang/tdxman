"""Explicit repair of absent ST selections, retaining original source evidence."""

import time
from bisect import bisect_right

from .sqlite_daily_derived import recompute_symbol_features
from .sqlite_reference_factors import select_is_st


def repair_symbol_st(conn, *, symbol, start, end, recompute_existing=False):
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
    reference_changed_dates = []
    if recompute_existing:
        reference_changed_dates = [
            row[0]
            for row in conn.execute(
                "SELECT trade_date FROM daily_features WHERE symbol=? "
                "AND trade_date BETWEEN ? AND ? AND pre_close IS NOT NULL "
                "AND pre_close_source='raw_fallback:legacy_unspecified'",
                (symbol, start, end),
            )
        ]
        if reference_changed_dates:
            conn.execute(
                "UPDATE daily_features SET pre_close=NULL,pre_close_source=NULL,"
                "limit_status=CASE WHEN calc_status='TRADED' THEN 'UNKNOWN' ELSE limit_status END,"
                "limit_reason=CASE WHEN calc_status='TRADED' THEN 'missing_reference' "
                "ELSE limit_reason END,limit_up_price=NULL,limit_down_price=NULL,"
                "touch_limit_up=NULL,close_limit_up=NULL,touch_limit_down=NULL,"
                "close_limit_down=NULL,consecutive_up=NULL,"
                "streak_known=CASE WHEN calc_status='TRADED' THEN 0 ELSE streak_known END,"
                "updated_at=? WHERE symbol=? AND trade_date BETWEEN ? AND ? "
                "AND pre_close IS NOT NULL "
                "AND pre_close_source='raw_fallback:legacy_unspecified'",
                (time.time_ns() // 1000, symbol, start, end),
            )
    if not count and not recompute_existing:
        return {"selected_rows": 0, "changed_dates": []}
    if count:
        conn.execute(
            "UPDATE daily_features SET is_st=0,is_st_source='assumed:not_st',"
            "is_st_name_date=NULL,updated_at=? WHERE symbol=? AND trade_date BETWEEN ? AND ? "
            "AND is_st IS NULL",
            (time.time_ns() // 1000, symbol, start, end),
        )
    membership_changed_dates = []
    # Existing dated evidence retains precedence over the new default.
    for day, value, source, name, name_day in evidence:
        selected, origin, name_date = select_is_st(
            dated=[(value, source)],
            name=name,
            name_as_of=name_day,
            trade_date=day,
        )
        if selected:
            membership_changed_dates.append(day)
        if origin != "assumed:not_st":
            conn.execute(
                "UPDATE daily_features SET is_st=?,is_st_source=?,is_st_name_date=? "
                "WHERE symbol=? AND trade_date=?",
                (selected, origin, name_date, symbol, day),
            )
    factor_lookup = None
    if recompute_existing:
        factors = conn.execute(
            "SELECT effective_date,cumulative_factor,valid_from,valid_through "
            "FROM corporate_actions WHERE symbol=? AND record_kind='factor' "
            "AND effective_date<=? ORDER BY effective_date",
            (symbol, end),
        ).fetchall()
        factor_dates = [row[0] for row in factors]

        def factor_lookup(day):
            position = bisect_right(factor_dates, day) - 1
            if position >= 0:
                _, factor, valid_from, valid_through = factors[position]
                if valid_from <= day <= valid_through:
                    return factor
            return None

    result = recompute_symbol_features(
        conn,
        symbol=symbol,
        start=start,
        end=end,
        limits_only=not recompute_existing,
        factor_lookup=factor_lookup,
        propagate=not recompute_existing,
        successor_window=0,
        max_affected_dates=260,
    )
    result["changed_dates"] = sorted(
        set(result["changed_dates"]) | {r[0] for r in evidence} | set(reference_changed_dates)
    )
    board_changed_dates = sorted(
        set(membership_changed_dates)
        | set(result.get("trading_changed_dates", []))
        | set(reference_changed_dates)
    )
    return dict(
        result,
        selected_rows=count,
        reference_rows=len(reference_changed_dates),
        board_changed_dates=board_changed_dates,
    )
