"""Metadata for the implemented daily summary fields, without scanning history."""

from .api_contract import DataPoolError
from .sqlite_summary_quality import QUALITY_FIELDS


def daily_columns(conn):
    columns = [row[1] for row in conn.execute("PRAGMA table_info(market_daily_summary)")]
    return columns[: columns.index("above_ma20_pct") + 1] + ["updated_at"]


def describe_fields(conn, *, fields=None, frequency="D", scope="all_stocks"):
    if frequency != "D":
        raise DataPoolError("FREQUENCY_NOT_READY", "Only daily summaries are implemented")
    if scope not in ("all_stocks", "exclude_known_st"):
        raise DataPoolError("INVALID_ARGUMENT", "Unsupported market scope")
    schema = {row[1]: row for row in conn.execute("PRAGMA table_info(market_daily_summary)")}
    # Optional columns may have been appended by maintenance ALTER TABLE.
    allowed = list(dict.fromkeys(daily_columns(conn) + [k for k in QUALITY_FIELDS if k in schema]))
    chosen = allowed if fields is None else ([fields] if isinstance(fields, str) else list(fields))
    if not chosen or len(chosen) != len(set(chosen)) or set(chosen) - set(allowed):
        raise DataPoolError("FIELD_UNSUPPORTED", "Unknown or unavailable daily summary field")
    result = {}
    for name in chosen:
        dtype = schema[name][2]
        if name in ("period_key", "period_start", "period_end", "as_of"):
            unit = "date"
        elif name == "amount_sum":
            unit = "CNY"
        elif name in ("up_pct", "strong_up_pct", "strong_down_pct", "avg_turnover"):
            unit = "percent_points"
        elif name in (
            "avg_return",
            "median_return",
            "sealed_ratio",
            "promotion_ratio",
            "above_ma20_pct",
        ):
            unit = "ratio"
        elif name == "updated_at":
            unit = "unix_microseconds"
        elif name.endswith("_json"):
            unit = "count_map"
        elif name in ("max_consecutive_up", "session_count"):
            unit = "sessions"
        elif dtype == "INTEGER":
            unit = "stocks"
        else:
            unit = "label"
        null_meaning = (
            "not_computed"
            if name in QUALITY_FIELDS
            else (
                "unknown_streak"
                if name == "max_consecutive_up"
                else "no_valid_sample"
                if dtype == "REAL"
                else "not_applicable"
            )
        )
        result[name] = dict(
            type=dtype,
            unit=unit,
            nullable=not bool(schema[name][3]),
            frequency="D",
            scope=scope,
            readable=True,
            null_meaning=null_meaning,
        )
    if "limit_reason_counts_json" in result:
        result["limit_reason_counts_json"].update(
            definition="Primary reasons partition UNKNOWN/INVALID; NO_TRADE excluded",
            reconciliation={"UNKNOWN": "limit_unknown_count", "INVALID": "limit_invalid_count"},
            missing_reason="unclassified",
        )
    if "promotion_quality_json" in result:
        result["promotion_quality_json"].update(
            candidate="Latest non-NO_TRADE observation closed limit-up; candidates carry over",
            exclusion_priority=["no_trade", "invalid", "limit_unknown", "predecessor_unknown"],
            reconciliation="candidate_count = promotion_eligible_count + sum(excluded_*_count)",
            unresolved_predecessor_count=(
                "Unresolved candidate identities, separate from candidate_count"
            ),
            absent_current_scope="Absent current ST stays unknown and is retained; no forward fill",
        )
    return result
