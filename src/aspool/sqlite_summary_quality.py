"""Optional additive summary quality fields; maintenance upgrades are explicit."""

import json
from collections import Counter

from .api_contract import DataPoolError

QUALITY_FIELDS = ("limit_reason_counts_json", "promotion_quality_json")
PROMOTION_KEYS = (
    "candidate_count",
    "excluded_no_trade_count",
    "excluded_invalid_count",
    "excluded_limit_unknown_count",
    "excluded_predecessor_unknown_count",
    "unresolved_predecessor_count",
)


def quality_columns(conn):
    return {row[1] for row in conn.execute("PRAGMA table_info(market_daily_summary)")}


def upgrade_quality_columns(conn):
    """Add nullable columns in an explicit caller-owned maintenance transaction."""
    if not conn.in_transaction:
        raise ValueError("An outer maintenance transaction is required")
    columns = quality_columns(conn)
    for name in QUALITY_FIELDS:
        if name not in columns:
            conn.execute(
                f"ALTER TABLE market_daily_summary ADD COLUMN {name} TEXT "
                f"CHECK({name} IS NULL OR (json_valid({name}) AND json_type({name})='object'))"
            )


def promotion_quality(conn, day, current, *, max_rows):
    """Visit stock keys and only necessary preceding observations, with a hard budget.

    The latest non-NO_TRADE observation establishes candidate identity. Missing
    current rows and NO_TRADE both exclude the candidate for this session. A
    stored known predecessor streak avoids rereading its historical observation.
    """
    result = {
        scope: Counter(dict.fromkeys(PROMOTION_KEYS, 0))
        for scope in ("all_stocks", "exclude_known_st")
    }
    visited = 0

    def charge():
        nonlocal visited
        visited += 1
        if visited > max_rows:
            raise DataPoolError(
                "LOCAL_UPDATE_BUDGET_EXCEEDED", "Promotion predecessor budget exceeded"
            )

    symbol = ""
    while True:
        found = conn.execute(
            "SELECT symbol FROM daily_features WHERE symbol>? ORDER BY symbol LIMIT 1", (symbol,)
        ).fetchone()
        if found is None:
            break
        charge()
        symbol = found[0]
        row = current.get(symbol)
        streak = row["prior_consecutive_up"] if row else None
        candidate = streak is not None and streak > 0
        if streak is None:
            cursor = conn.execute(
                "SELECT calc_status,close_limit_up,consecutive_up,streak_known "
                "FROM daily_features WHERE symbol=? AND trade_date<? ORDER BY trade_date DESC",
                (symbol, day),
            )
            try:
                for previous in cursor:
                    charge()
                    if previous[0] != "NO_TRADE":
                        candidate = previous[1] == 1
                        break
            finally:
                cursor.close()
        counts = {key: 0 for key in PROMOTION_KEYS}
        status = row["calc_status"] if row else "NO_TRADE"
        if candidate:
            counts["candidate_count"] = 1
            if status == "NO_TRADE":
                counts["excluded_no_trade_count"] = 1
            elif status == "INVALID":
                counts["excluded_invalid_count"] = 1
            elif row["limit_status"] not in ("KNOWN", "NO_LIMIT"):
                counts["excluded_limit_unknown_count"] = 1
            elif streak is None:
                counts["excluded_predecessor_unknown_count"] = 1
        elif status == "TRADED" and streak is None:
            # Unresolved identities are outside the known candidate partition.
            counts["unresolved_predecessor_count"] = 1
        result["all_stocks"].update(counts)
        if row is None or row["is_st"] != 1:
            result["exclude_known_st"].update(counts)
    return {scope: dict(counts) for scope, counts in result.items()}, visited


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
