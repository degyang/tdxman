"""JSON-safe daily field deltas and explicitly scoped maintenance counters.

Row fingerprints describe logical row content, not file encodings or a public
dataset revision. These compatibility observations are separate from change_protocol recovery state.
"""

import hashlib
import json
from datetime import date, datetime
from numbers import Integral, Real
from pathlib import Path
from uuid import uuid4

from .daily_storage import _same_values, is_missing_value


def _value(value):
    if is_missing_value(value):
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, bool):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        number = float(value)
        return int(number) if number.is_integer() else number
    return value


def row_version(row):
    if row is None:
        return None
    normalized = {key: _value(value) for key, value in row.items() if not is_missing_value(value)}
    data = json.dumps(normalized, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(data.encode()).hexdigest()


def iter_daily_changes(before, after, symbol, source, reason):
    """Describe inserts/updates, including derived-field invalidations in the tail."""
    for row in after:
        previous = before.get(row["trade_date"])
        if _same_values(previous, row):
            continue
        old = previous or {}
        fields = sorted(
            field
            for field in old.keys() | row.keys()
            if not _same_values({field: old.get(field)}, {field: row.get(field)})
        )
        yield dict(
            symbol=symbol,
            trade_date=row["trade_date"].isoformat(),
            operation="insert" if previous is None else "update",
            fields=fields,
            before={field: _value(old.get(field)) for field in fields},
            after={field: _value(row.get(field)) for field in fields},
            source=source,
            reason=reason,
            input_row_version=row_version(previous),
            output_row_version=row_version(row),
        )


def daily_changes(before, after, symbol, source, reason):
    """Compatibility helper; the durable protocol uses the streaming iterator."""
    return list(iter_daily_changes(before, after, symbol, source, reason))


def add_cost(metrics, **values):
    if metrics is not None:
        for key, value in values.items():
            metrics[key] = metrics.get(key, 0) + value


def empty_cost():
    return dict(
        files_read=0,
        file_bytes_read_proxy=0,
        rows_read=0,
        rows_materialized=0,
        files_rewritten=0,
        file_bytes_rewritten=0,
        rows_rewritten=0,
        changed_rows=0,
        coverage_repairs=0,
        stale_date_marks=0,
        elapsed_seconds=0.0,
    )


def save_changes(root, changes, *, status="applied"):
    """Persist one security's evidence; callers retain only a small reference.

    A crash before this post-write report is saved needs recovery from the pool
    baseline. Do not treat these reports as a transactional redo/undo journal.
    """
    if not changes:
        return None
    path = Path(root) / "reports/changes" / f"{uuid4().hex}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        stream.write(json.dumps({"schema_version": 1, "status": status})[:-1] + ', "changes": [')
        for index, row in enumerate(changes):
            if index:
                stream.write(",")
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False))
        stream.write("]}\n")
    temporary.replace(path)
    return str(path)


class ChangeSpool:
    """Incremental compatibility report evidence; memory holds only one row."""

    def __init__(self, root):
        self.root = Path(root)
        self.path = None
        self.count = 0
        self.start = self.end = None

    def extend(self, rows):
        for row in rows:
            if self.path is None:
                directory = self.root / "reports/changes"
                directory.mkdir(parents=True, exist_ok=True)
                self.path = directory / f".{uuid4().hex}.jsonl"
            with self.path.open("a") as stream:
                stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            self.count += 1
            day = row.get("trade_date")
            if day:
                self.start = min(day, self.start) if self.start else day
                self.end = max(day, self.end) if self.end else day

    def __iter__(self):
        if self.path is not None:
            with self.path.open() as stream:
                for line in stream:
                    yield json.loads(line)

    def __len__(self):
        return self.count

    def close(self):
        if self.path:
            self.path.unlink(missing_ok=True)
