"""Merge a daily tail without turning unchanged history into Python dictionaries."""

import math
from bisect import bisect_left
from numbers import Real
from time import perf_counter
from uuid import uuid4

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

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


def merge_daily(
    root, market, symbol, incoming, source, coverage=None, changed_dates=None, quality_rows=None,
    _path=None, changes=None, metrics=None,
):
    from .change_observation import add_cost, daily_changes
    from .store import daily_paths, daily_year_path, read_daily_table
    from .tdx_online import _fill_close_vol_ratio, _merge_rows

    dates = [row["trade_date"] for row in incoming]
    if not incoming or len(dates) != len(set(dates)):
        raise ValueError(f"{symbol}: empty or duplicate incoming dates")
    paths = daily_paths(root, market, symbol) if _path is None else []
    yearly = bool(paths and paths[0].parent.name.startswith("year="))
    started = perf_counter()
    for row in incoming:
        values = [row.get(k) for k in ("open", "high", "low", "close", "volume", "amount")]
        if (
            any(v is None or not math.isfinite(float(v)) or float(v) < 0 for v in values)
            or row["high"] < row["low"]
        ):
            raise ValueError(f"{symbol}: invalid incoming OHLCV")
    path = _path or bars_path(root, "daily", market, symbol)
    # The rolling baseline and next-close dependencies cross year boundaries.
    # Keep the existing conservative full read until DG-02 supplies bounded access.
    read_paths = paths if yearly else ([path] if path.exists() else [])
    tables = []
    for stored_path in read_paths:
        table_part = pq.ParquetFile(stored_path).read()
        tables.append(table_part)
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
        clean = {**identity, **row}
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
    delta = (daily_changes(before, merged, f"{symbol}.{market}", source, "daily_merge")
             if changes is not None else [])
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
    committed = 0
    try:
        for target, output in outputs:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(f".{uuid4().hex}.part")
            file_days = set(output["trade_date"].to_pylist())
            file_touched = [day for day in touched if day in file_days]
            try:
                pq.write_table(output, temporary, compression="zstd")
                if source not in {"tdxman:etf", "tdxman:etf:offline"}:
                    from .limit_events import _mark_stale, _published_dates_from
                    from .store import existing_tables

                    if "daily_limit_publication" in existing_tables(root):
                        stale_dates = _published_dates_from(root, min(file_touched))
                        _mark_stale(root, stale_dates, "日线字段变化，等待补齐和重算")
                        add_cost(metrics, stale_date_marks=len(stale_dates))
                written_bytes = temporary.stat().st_size
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            committed += 1
            if changed_dates is not None:
                changed_dates.extend(file_touched)
            if changes is not None:
                changed_iso = {d.isoformat() for d in file_touched}
                changes.extend(item for item in delta if item["trade_date"] in changed_iso)
            add_cost(metrics, files_rewritten=1, file_bytes_rewritten=written_bytes,
                     rows_rewritten=len(output), changed_rows=len(file_touched))
    finally:
        # A later partition failure must still publish truthful coverage for the
        # already committed files. No-op retry must not leave old coverage behind.
        if committed:
            actual = result
            if committed != len(outputs):
                actual = read_daily_table(root, market, symbol)
                all_paths = daily_paths(root, market, symbol)
                add_cost(metrics, files_read=len(all_paths), rows_read=len(actual),
                         file_bytes_read_proxy=sum(p.stat().st_size for p in all_paths))
            entry = (symbol, market, actual["trade_date"][0].as_py(),
                     actual["trade_date"][-1].as_py(), len(actual), source, "daily")
            if coverage is None:
                record_coverage(root, *entry)
            else:
                coverage.append(entry)
        add_cost(metrics, elapsed_seconds=perf_counter() - started)
    if quality_rows is not None:
        quality_rows.extend(quality)
    return changed
