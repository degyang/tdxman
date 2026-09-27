"""Merge a daily tail without turning unchanged history into Python dictionaries."""

import math
from bisect import bisect_left
from numbers import Real
from time import perf_counter

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .change_protocol import maintenance
from .store import bars_path, record_coverage


def is_missing_value(value) -> bool:
    """Return whether a scalar is a source-level missing value."""
    if value is None:
        return True
    try:
        result = pd.isna(value)
        return bool(result)
    except (TypeError, ValueError):
        return False


def _field_status(field: str, value) -> str:
    if is_missing_value(value):
        return "missing"
    if field == "pre_close":
        if isinstance(value, bool) or not isinstance(value, Real):
            return "invalid"
        try:
            return "valid" if math.isfinite(float(value)) and float(value) > 0 else "invalid"
        except (TypeError, ValueError, OverflowError):
            return "invalid"
    if field == "is_st":
        return "valid" if isinstance(value, bool) else "invalid"
    raise ValueError(f"unsupported quality field: {field}")


def daily_field_quality(row: dict) -> dict[str, str | bool]:
    """Classify optional source fields after daily-row merging."""
    pre_close = _field_status("pre_close", row.get("pre_close"))
    is_st = _field_status("is_st", row.get("is_st"))
    return {
        "pre_close": pre_close,
        "is_st": is_st,
        "joint_valid": pre_close == "valid" and is_st == "valid",
    }


def build_field_quality_report(records: list[dict], scope: str, source: str) -> dict:
    """Aggregate post-merge field quality without creating a statistics store."""
    grouped: dict[object, dict] = {}
    for record in records:
        trade_date = record["trade_date"]
        entry = grouped.setdefault(
            trade_date,
            {
                "trade_date": trade_date.isoformat(),
                "processed_rows": 0,
                "symbol_count": 0,
                "pre_close": {"valid": 0, "missing": 0, "invalid": 0},
                "is_st": {"valid": 0, "missing": 0, "invalid": 0},
                "incoming_invalid": {"pre_close": 0, "is_st": 0},
                "joint_valid": 0,
                "_symbols": set(),
            },
        )
        entry["processed_rows"] += 1
        entry["_symbols"].add(record["symbol"])
        quality = record["quality"]
        entry["pre_close"][quality["pre_close"]] += 1
        entry["is_st"][quality["is_st"]] += 1
        for field in ("pre_close", "is_st"):
            entry["incoming_invalid"][field] += record["incoming_invalid"][field]
        entry["joint_valid"] += int(quality["joint_valid"])
    rows = []
    for trade_date in sorted(grouped):
        entry = grouped[trade_date]
        entry["symbol_count"] = len(entry.pop("_symbols"))
        rows.append(entry)
    result = {
        "scope": scope,
        "source": source,
        "basis": "post_merge_incoming_rows",
        "rows": rows,
    }
    if rows:
        result["start_date"] = rows[0]["trade_date"]
        result["end_date"] = rows[-1]["trade_date"]
    return result


def _same_values(left, right):
    if left is None:
        return False

    for key in left.keys() | right.keys():
        left_value, right_value = left.get(key), right.get(key)
        if is_missing_value(left_value) and is_missing_value(right_value):
            continue
        if is_missing_value(left_value) or is_missing_value(right_value):
            return False
        try:
            if not bool(left_value == right_value):
                return False
        except (TypeError, ValueError):
            return False
    return True


@maintenance
def merge_daily(
    root, market, symbol, incoming, source, coverage=None, changed_dates=None, quality_rows=None,
    _path=None, changes=None, metrics=None,
):
    from .change_observation import add_cost, iter_daily_changes
    from .change_protocol import note_range, publish_file, recover
    from .store import daily_paths, daily_year_path
    from .tdx_online import _fill_close_vol_ratio, _merge_rows

    dates = [row["trade_date"] for row in incoming]
    note_range("candidate", rows=len(incoming), start=min(dates) if dates else None,
               end=max(dates) if dates else None)
    if not incoming or len(dates) != len(set(dates)):
        raise ValueError(f"{symbol}: empty or duplicate incoming dates")
    started = perf_counter()
    for row in incoming:
        if row.get("operation", "update") not in {"insert", "update"}:
            raise ValueError("Explicit deletions/retractions are unsupported")
        values = [row.get(k) for k in ("open", "high", "low", "close", "volume", "amount")]
        if (
            any(v is None or not math.isfinite(float(v)) or float(v) < 0 for v in values)
            or row["high"] < row["low"]
        ):
            raise ValueError(f"{symbol}: invalid incoming OHLCV")
    recover(root)
    paths = daily_paths(root, market, symbol) if _path is None else []
    yearly = bool(paths and paths[0].parent.name.startswith("year="))
    path = _path or bars_path(root, "daily", market, symbol)
    # The rolling baseline and next-close dependencies cross year boundaries.
    # Keep the existing conservative full read until DG-02 supplies bounded access.
    read_paths = paths if yearly else ([path] if path.exists() else [])
    tables = []
    for stored_path in read_paths:
        table_part = pq.ParquetFile(stored_path).read()
        tables.append(table_part)
        note_range("read", rows=len(table_part), path=stored_path,
                   start=table_part["trade_date"][0].as_py() if len(table_part) else None,
                   end=table_part["trade_date"][-1].as_py() if len(table_part) else None,
                   bytes_proxy=stored_path.stat().st_size)
        add_cost(metrics, files_read=1, file_bytes_read_proxy=stored_path.stat().st_size,
                 rows_read=len(table_part))
    table = pa.concat_tables(tables, promote_options="permissive") if tables else None
    days = table["trade_date"].to_pylist() if table is not None else []
    if days != sorted(set(days)):
        raise ValueError(f"{symbol}: duplicate or unordered stored dates")
    # Five preceding bars provide the rolling volume baseline; older rows stay in Arrow.
    offset = max(0, bisect_left(days, min(r["trade_date"] for r in incoming)) - 5)
    prior = table.slice(offset).to_pylist() if table is not None else []
    add_cost(metrics, rows_materialized=len(prior))
    identity = {}
    for row in reversed(prior):
        for field in ("code", "name", "market"):
            if field not in identity and row.get(field) is not None:
                identity[field] = row[field]
    invalid_fields: dict[object, set[str]] = {}
    sanitized = []
    for row in incoming:
        clean = {**identity, **{k: v for k, v in row.items() if k != "operation"}}
        invalid = {
            field
            for field in ("pre_close", "is_st")
            if _field_status(field, row.get(field)) == "invalid"
        }
        if invalid:
            invalid_fields[row["trade_date"]] = invalid
            for field in invalid:
                # Invalid optional values cannot be represented safely in an existing
                # typed parquet column; preserve any prior reliable value instead.
                clean.pop(field, None)
        sanitized.append(clean)
    incoming = sanitized
    merged = _fill_close_vol_ratio(_merge_rows(prior, incoming, "trade_date"))
    merged_by_date = {row["trade_date"]: row for row in merged}
    quality = []
    for trade_date in dict.fromkeys(dates):
        field_quality = daily_field_quality(merged_by_date[trade_date])
        field_quality["joint_valid"] = (
            field_quality["pre_close"] == "valid" and field_quality["is_st"] == "valid"
        )
        quality.append(
            {
                "trade_date": trade_date,
                "symbol": symbol,
                "quality": field_quality,
                "incoming_invalid": {
                    field: int(field in invalid_fields.get(trade_date, set()))
                    for field in ("pre_close", "is_st")
                },
            }
        )
    before = {r["trade_date"]: r for r in prior}
    touched = [r["trade_date"] for r in merged if not _same_values(before.get(r["trade_date"]), r)]
    changed = len(touched)
    if not changed:
        # Retry may follow a committed file replacement whose coverage write
        # failed. Repair only a mismatched extent/count; true no-ops keep timestamps.
        if _path is None:
            from .store import catalog

            extent = (table["trade_date"][0].as_py(), table["trade_date"][-1].as_py(), len(table))
            with catalog(root) as conn:
                stored_extent = conn.execute(
                    "SELECT CAST(start_date AS DATE), CAST(end_date AS DATE), row_count "
                    "FROM coverage WHERE symbol=?", [symbol],
                ).fetchone()
            if stored_extent != extent:
                entry = (symbol, market, *extent, source, "daily")
                if coverage is None:
                    record_coverage(root, *entry)
                else:
                    coverage.append(entry)
                add_cost(metrics, coverage_repairs=1)
        add_cost(metrics, elapsed_seconds=perf_counter() - started)
        if quality_rows is not None:
            quality_rows.extend(quality)
        return 0
    columns = dict.fromkeys(
        [
            *(table.column_names if table is not None else []),
            *(key for row in merged for key in row),
        ]
    )
    tail = pa.Table.from_pylist([{k: row.get(k) for k in columns} for row in merged])
    result = (
        pa.concat_tables([table.slice(0, offset), tail], promote_options="permissive")
        if table is not None and offset
        else tail
    )
    if yearly:
        import pyarrow.compute as pc

        years = result["trade_date"].cast(pa.date32())
        outputs = [
            (daily_year_path(root, market, symbol, year),
             result.filter(pc.equal(pc.year(years), year)))
            for year in sorted({day.year for day in touched})
        ]
    else:
        outputs = [(path, result)]
    try:
        for target, output in outputs:
            file_days = set(output["trade_date"].to_pylist())
            file_touched = [day for day in touched if day in file_days]
            file_rows = (row for row in merged if row["trade_date"] in file_days)
            # Coverage for each atomic file commit describes the actual mixed file
            # set. It is never deferred to a post-write report/batch update.
            actual_days = sorted(set(days) | {day for day in file_touched})
            entry = None if _path is not None else (
                "coverage",
                ["symbol", "market", "start_date", "end_date", "row_count", "source", "updated_at"],
                [symbol, market, actual_days[0], actual_days[-1], len(actual_days), source,
                 __import__("datetime").datetime.now()], ["symbol"])
            try:
                publish_file(
                    root, target, output,
                    iter_daily_changes(
                        before, file_rows, f"{symbol}.{market}", source, "daily_merge"),
                    source=source, reason="daily_merge", coverage=entry,
                    stale_start=min(file_touched)
                    if source not in {"tdxman:etf", "tdxman:etf:offline"} else None,
                    metrics=metrics,
                )
            except Exception as exc:
                if getattr(exc, "committed_change", None):
                    if changed_dates is not None:
                        changed_dates.extend(file_touched)
                    if changes is not None:
                        touched_set = set(file_touched)
                        changes.extend(iter_daily_changes(
                            before, (r for r in merged if r["trade_date"] in touched_set),
                            f"{symbol}.{market}", source, "daily_merge"))
                raise
            days = actual_days
            if changed_dates is not None:
                changed_dates.extend(file_touched)
            if changes is not None:
                touched_set = set(file_touched)
                changes.extend(iter_daily_changes(
                    before, (r for r in merged if r["trade_date"] in touched_set),
                    f"{symbol}.{market}", source, "daily_merge"))
    finally:
        add_cost(metrics, elapsed_seconds=perf_counter() - started)
    if quality_rows is not None:
        quality_rows.extend(quality)
    return changed
