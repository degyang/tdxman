"""Local daily-domain commit/recovery protocol; not a public cache revision API.

Files are prepared durably, then rolled forward under the pool write lock.
Catalog-only edits, row evidence, logical counters and stale marks share a
DuckDB transaction. Immutable new objects are hardlinked when published, so
keeping recovery references does not duplicate unrelated cold history.
"""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime
from functools import wraps
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import pyarrow.parquet as pq

from .store import catalog


def _json(value):
    from .change_observation import _value

    return json.dumps(value, default=_value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _mkdir_durable(path):
    missing = []
    item = Path(path)
    while not item.exists():
        missing.append(item)
        item = item.parent
    path.mkdir(parents=True, exist_ok=True)
    for item in reversed(missing):
        _sync_directory(item.parent)
        _sync_directory(item)


def _durable_json(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        stream.write(_json(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    _sync_directory(path.parent)


def _manifest_digest(manifest):
    payload = {k: v for k, v in manifest.items() if k != "manifest_sha256"}
    return hashlib.sha256(_json(payload).encode()).hexdigest()


def _write_manifest(directory, manifest):
    manifest["manifest_sha256"] = _manifest_digest(manifest)
    _durable_json(directory / "manifest.json", manifest)


def _digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    note_range("integrity_read", path=path, bytes_proxy=path.stat().st_size)
    return digest.hexdigest()


def _initialize(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS maintenance_runs (
        run_id VARCHAR PRIMARY KEY, command VARCHAR, state VARCHAR, metrics VARCHAR)""")
    run = _current_run.get()
    if run is not None:
        conn.execute(
            "INSERT INTO maintenance_runs VALUES (?, ?, 'running', ?) "
            "ON CONFLICT(run_id) DO NOTHING",
            [run["run_id"], run["command"], _json(run)],
        )
    conn.execute("""CREATE TABLE IF NOT EXISTS maintenance_totals (
        run_id VARCHAR PRIMARY KEY, changed_rows BIGINT, changed_fields BIGINT,
        applied_operations BIGINT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS coverage_totals (
        run_id VARCHAR PRIMARY KEY, changed_rows BIGINT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS business_revisions (
        object_key VARCHAR PRIMARY KEY, revision BIGINT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS business_changes (
        operation_id VARCHAR PRIMARY KEY, object_key VARCHAR, input_revision BIGINT,
        output_revision BIGINT, source VARCHAR, reason VARCHAR, changed_rows BIGINT,
        changed_fields BIGINT, evidence VARCHAR, state VARCHAR,
        applied_at TIMESTAMP, run_id VARCHAR)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS fetch_observations (
        object_key VARCHAR PRIMARY KEY, observed_at TIMESTAMP, status VARCHAR)""")


def _revision(conn, key):
    row = conn.execute(
        "SELECT revision FROM business_revisions WHERE object_key=?", [key]
    ).fetchone()
    return row[0] if row else 0


def observe(conn, key, status="ok"):
    _initialize(conn)
    conn.execute(
        "INSERT OR REPLACE INTO fetch_observations "
        "VALUES (?, current_timestamp AT TIME ZONE 'UTC', ?)",
        [key, status],
    )


def _stale(conn, start, reason):
    if start is None:
        return 0
    tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
    if {"daily_limit_publication", "daily_limit_staleness"} <= tables:
        result = conn.execute(
            """INSERT OR REPLACE INTO daily_limit_staleness
            SELECT trade_date, ?, current_timestamp FROM daily_limit_publication
            WHERE trade_date >= ?""",
            [reason, start],
        ).fetchone()
        count = result[0] if result else 0
        return count
    return 0


def _record(conn, manifest):
    current = _revision(conn, manifest["object_key"])
    if current != manifest["input_revision"]:
        raise RuntimeError("Logical revision changed before commit; preserve pending evidence")
    conn.execute(
        "INSERT OR REPLACE INTO business_revisions VALUES (?, ?)",
        [manifest["object_key"], manifest["output_revision"]],
    )
    conn.execute(
        "INSERT INTO business_changes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, current_timestamp, ?)",
        [
            manifest[k]
            for k in (
                "operation_id",
                "object_key",
                "input_revision",
                "output_revision",
                "source",
                "reason",
                "changed_rows",
                "changed_fields",
                "evidence",
            )
        ]
        + ["applied", manifest.get("run_id")],
    )

    if manifest.get("run_id"):
        conn.execute(
            """INSERT INTO maintenance_totals VALUES (?, ?, ?, 1)
            ON CONFLICT(run_id) DO UPDATE SET
            changed_rows=maintenance_totals.changed_rows+excluded.changed_rows,
            changed_fields=maintenance_totals.changed_fields+excluded.changed_fields,
            applied_operations=maintenance_totals.applied_operations+1""",
            [manifest["run_id"], manifest["changed_rows"], manifest["changed_fields"]],
        )


def _coverage(conn, entry):
    if not entry:
        return
    table, columns, values, keys = entry
    if table in {"coverage", "index_coverage"}:
        coverage_change(
            conn, table, columns, values, keys, reason="file_commit_coverage", force=True
        )
    else:
        _upsert(conn, table, columns, values, keys)


def _upsert(conn, table, columns, values, keys):
    placeholders = ",".join("?" for _ in values)
    updates = ",".join(f"{c}=excluded.{c}" for c in columns if c not in keys)
    conn.execute(
        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({','.join(keys)}) DO UPDATE SET {updates}",
        values,
    )


def coverage_change(conn, table, columns, values, keys, *, reason, force=False):
    """Journal derived extent/provenance metadata without changing business revisions.

    Caller owns the catalog transaction. Only these two existing coverage schemas
    are accepted; this is not an arbitrary catalog mutation interface.
    """
    from .daily_storage import _same_values

    if table not in {"coverage", "index_coverage"}:
        raise ValueError("Unsupported coverage schema")
    incoming = dict(zip(columns, values))
    if table == "coverage":
        for key in ("start_date", "end_date"):
            if isinstance(incoming[key], date) and not isinstance(incoming[key], datetime):
                incoming[key] = datetime.combine(incoming[key], datetime.min.time())
    cursor = conn.execute(
        f"SELECT * FROM {table} WHERE " + " AND ".join(f"{k}=?" for k in keys),
        [incoming[k] for k in keys],
    )
    row = cursor.fetchone()
    old = dict(zip([c[0] for c in cursor.description], row)) if row else None
    old_business = {k: v for k, v in (old or {}).items() if k != "updated_at"}
    new_business = {k: v for k, v in incoming.items() if k != "updated_at"}
    if not force and old is not None and _same_values(old_business, new_business):
        return False
    conn.execute("""CREATE TABLE IF NOT EXISTS metadata_revisions (
        object_key VARCHAR PRIMARY KEY, revision BIGINT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS coverage_changes (
        operation_id VARCHAR PRIMARY KEY, object_key VARCHAR, input_revision BIGINT,
        output_revision BIGINT, source VARCHAR, reason VARCHAR, fields VARCHAR,
        before_values VARCHAR, after_values VARCHAR, state VARCHAR, run_id VARCHAR)""")
    key = table + ":" + ":".join(str(incoming[k]) for k in keys)
    prior = conn.execute(
        "SELECT revision FROM metadata_revisions WHERE object_key=?", [key]
    ).fetchone()
    revision = prior[0] if prior else 0
    fields = sorted(
        k
        for k in old_business.keys() | new_business.keys()
        if not _same_values({k: old_business.get(k)}, {k: new_business.get(k)})
    )
    _upsert(conn, table, list(incoming), list(incoming.values()), keys)
    conn.execute("INSERT OR REPLACE INTO metadata_revisions VALUES (?, ?)", [key, revision + 1])
    conn.execute(
        "INSERT INTO coverage_changes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'applied', ?)",
        [
            uuid4().hex,
            key,
            revision,
            revision + 1,
            incoming["source"],
            reason,
            _json(fields),
            _json(old),
            _json(incoming),
            (_current_run.get() or {}).get("run_id"),
        ],
    )
    if (run := _current_run.get()) is not None:
        conn.execute("""CREATE TABLE IF NOT EXISTS coverage_totals (
            run_id VARCHAR PRIMARY KEY, changed_rows BIGINT)""")
        conn.execute(
            "INSERT INTO coverage_totals VALUES (?, 1) ON CONFLICT(run_id) DO UPDATE "
            "SET changed_rows=coverage_totals.changed_rows+1",
            [run["run_id"]],
        )
    return True


def _fault(phase):
    """Named seams used by exception/process-exit fault injection tests."""


def pending(root):
    path = Path(root) / "change-state/pending"
    return path.exists() and next(path.iterdir(), None) is not None


def assert_readable(root):
    if pending(root):
        from .api_contract import DataPoolError

        raise DataPoolError(
            "RECOVERY_REQUIRED", "Daily-domain commit pending; recover under write lock"
        )


def _apply(root, directory, manifest, *, recovery=False):
    if manifest.get("manifest_sha256") != _manifest_digest(manifest):
        raise RuntimeError("Recovery manifest missing checksum or corrupt")
    relative = Path(manifest["target"])
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or str(relative) != manifest["object_key"]
        or manifest["schema_version"] != 1
        or manifest["output_revision"] != manifest["input_revision"] + 1
        or manifest["changed_rows"] < 1
        or manifest["operation_id"] != directory.name
        or manifest["evidence"] != f"change-state/applied/{directory.name}/rows.jsonl"
    ):
        raise RuntimeError("Invalid prepared change manifest")
    target = Path(root) / manifest["target"]
    new = directory / "new.parquet"
    evidence = directory / "rows.jsonl"
    if not evidence.is_file() or _digest(evidence) != manifest["evidence_sha256"]:
        raise RuntimeError(f"Recovery evidence missing/corrupt: {directory}")
    with catalog(root) as conn:
        _initialize(conn)
        recorded_fields = (
            "object_key",
            "input_revision",
            "output_revision",
            "source",
            "reason",
            "changed_rows",
            "changed_fields",
            "evidence",
            "run_id",
        )
        applied = conn.execute(
            "SELECT " + ",".join(recorded_fields) + ",state FROM business_changes "
            "WHERE operation_id=?",
            [manifest["operation_id"]],
        ).fetchone()
        expected_revision = manifest["output_revision"] if applied else manifest["input_revision"]
        if _revision(conn, manifest["object_key"]) != expected_revision:
            raise RuntimeError(
                "Logical revision changed before recovery; preserve pending evidence"
            )
        if applied and applied != (*[manifest.get(k) for k in recorded_fields], "applied"):
            raise RuntimeError("Applied catalog record differs from recovery manifest")
        if new.exists() and _digest(new) != manifest["physical_sha256"]:
            raise RuntimeError(f"Recovery object corrupt: {directory}")
        if not applied and not new.is_file():
            raise RuntimeError(f"Recovery object missing: {directory}")
        if applied:
            if not target.is_file() or _digest(target) != manifest["physical_sha256"]:
                raise RuntimeError("Applied target differs from committed recovery object")
        else:
            _mkdir_durable(target.parent)
            temporary = target.with_suffix(".commit.part")
            try:
                temporary.unlink(missing_ok=True)
                os.link(new, temporary)
                _fault("before_replace")
                if not (
                    recovery and target.is_file() and _digest(target) == manifest["physical_sha256"]
                ):
                    temporary.replace(target)
                    _sync_directory(target.parent)
                    note_range(
                        "physical_replace",
                        rows=manifest["rows_rewritten"],
                        path=target,
                        bytes_proxy=manifest["bytes_rewritten"],
                    )
                manifest["state"] = "file_replaced"
                _write_manifest(directory, manifest)
                _fault("after_replace")
            finally:
                temporary.unlink(missing_ok=True)
            conn.execute("BEGIN")
            try:
                _coverage(conn, manifest.get("coverage"))
                _fault("after_coverage")
                marks = _stale(conn, manifest.get("stale_start"), manifest["reason"])
                _record(conn, manifest)
                _fault("before_catalog_commit")
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            manifest["stale_date_marks"] = marks
            note_range("stale_suffix", rows=marks, start=manifest.get("stale_start"))
            _fault("after_catalog_commit")
    manifest.update(state="applied", error=None)
    _write_manifest(directory, manifest)
    _fault("after_applied")
    # Only active preparations keep a complete redo file. Row deltas retain
    # recoverable old/new values after a durable commit; no cold generations.
    new.unlink(missing_ok=True)
    _sync_directory(directory)
    _fault("after_redo_release")
    archive = Path(root) / "change-state/applied"
    _mkdir_durable(archive)
    _sync_directory(archive.parent)
    directory.replace(archive / directory.name)
    _sync_directory(archive)
    _sync_directory(directory.parent)
    manifest["recovery_was_already_applied"] = bool(applied)
    return manifest


def recover(root):
    """Roll forward only active preparations; caller owns the pool write lock."""
    preparing = Path(root) / "change-state/preparing"
    if preparing.exists():
        # Preparations cannot reach a target before the durable move to pending.
        # Keep row evidence, release only the unpublished temporary redo object.
        for directory in sorted(preparing.iterdir()):
            (directory / "new.parquet").unlink(missing_ok=True)
            _durable_json(
                directory / "aborted.json",
                {
                    "state": "aborted_before_publication",
                    "business_changes": 0,
                    "reason": "preparation never entered the durable pending queue",
                },
            )
            orphan = Path(root) / "change-state/unprepared"
            _mkdir_durable(orphan)
            directory.replace(orphan / directory.name)
            _sync_directory(preparing)
            _sync_directory(orphan)
    folder = Path(root) / "change-state/pending"
    recovered = []
    if folder.exists():
        for directory in sorted(folder.iterdir()):
            path = directory / "manifest.json"
            if not path.exists():
                raise RuntimeError(f"Pending manifest missing; preserve failed state: {directory}")
            manifest = json.loads(path.read_text())
            tick = perf_counter()
            recovered.append(_apply(root, directory, manifest, recovery=True))
            _reconcile_recovered_run(root, manifest)
            note_range(
                "recovery",
                rows=manifest["changed_rows"],
                path=manifest["target"],
                start=manifest.get("changed_start"),
                end=manifest.get("changed_end"),
            )
            if (run := _current_run.get()) is not None:
                run["recovery_seconds"] = run.get("recovery_seconds", 0) + perf_counter() - tick
    return recovered


def _reconcile_recovered_run(root, manifest):
    old_run = manifest.get("run_id")
    if not old_run:
        return
    with catalog(root) as conn:
        total = conn.execute(
            "SELECT changed_rows, changed_fields, applied_operations "
            "FROM maintenance_totals WHERE run_id=?",
            [old_run],
        ).fetchone()
        row = conn.execute(
            "SELECT metrics FROM maintenance_runs WHERE run_id=?", [old_run]
        ).fetchone()
        if row and total:
            metrics = json.loads(row[0])
            metrics.setdefault("state_at_original_end", metrics["state"])
            if metrics["state"] == "running":
                metrics.update(
                    metrics_complete=False,
                    elapsed_seconds=None,
                    peak_rss_process_high_water_bytes=None,
                    lock_wait_seconds=None,
                    temporary_peak_bytes_proxy=None,
                    interrupted_metrics="unavailable after process exit; initial snapshot only",
                )
            metrics.update(
                state="recovered",
                changed_row_events=total[0],
                changed_field_events=total[1],
                applied_operations=total[2],
                recovery_run_id=(_current_run.get() or {}).get("run_id"),
                recovery_note="Elapsed/RSS describe original run; recovery costs are separate",
            )
            conn.execute(
                "UPDATE maintenance_runs SET state='recovered', metrics=? WHERE run_id=?",
                [_json(metrics), old_run],
            )
    if (run := _current_run.get()) is not None:
        run["recovered_row_events"] = run.get("recovered_row_events", 0) + manifest["changed_rows"]
        run["recovered_field_events"] = (
            run.get("recovered_field_events", 0) + manifest["changed_fields"]
        )
        if not manifest["recovery_was_already_applied"]:
            run["recovery_committed_row_events"] = (
                run.get("recovery_committed_row_events", 0) + manifest["changed_rows"]
            )


def publish_file(
    root, target, table, deltas, *, source, reason, coverage=None, stale_start=None, metrics=None
):
    """Prepare one changed file. Deltas are consumed incrementally, never retained here."""
    started = perf_counter()
    root, target = Path(root), Path(target)
    key = str(target.relative_to(root))
    with catalog(root) as conn:
        _initialize(conn)
        revision = _revision(conn, key)
    operation = uuid4().hex
    directory = root / "change-state/preparing" / operation
    _mkdir_durable(directory)
    for parent in (root, directory.parent.parent, directory.parent, directory):
        _sync_directory(parent)
    evidence = directory / "rows.jsonl"
    count = fields = 0
    changed_start = changed_end = None
    with evidence.open("w") as stream:
        for delta in deltas:
            if delta["operation"] not in {"insert", "update"}:
                raise ValueError(
                    "Explicit deletions/retractions require a separate supported contract"
                )
            stream.write(_json(delta) + "\n")
            count += 1
            fields += len(delta["fields"])
            day = delta.get("trade_date")
            if day is not None:
                changed_start = min(day, changed_start) if changed_start else day
                changed_end = max(day, changed_end) if changed_end else day
        stream.flush()
        os.fsync(stream.fileno())
    if not count:
        raise ValueError("publish_file requires actual business changes")
    new = directory / "new.parquet"
    pq.write_table(table, new, compression="zstd")
    with new.open("rb") as stream:
        os.fsync(stream.fileno())
    _sync_directory(directory)
    manifest = dict(
        schema_version=1,
        operation_id=operation,
        run_id=(_current_run.get() or {}).get("run_id"),
        object_key=key,
        target=key,
        input_revision=revision,
        output_revision=revision + 1,
        source=source,
        reason=reason,
        changed_start=changed_start,
        changed_end=changed_end,
        changed_rows=count,
        changed_fields=fields,
        evidence=f"change-state/applied/{operation}/rows.jsonl",
        physical_sha256=_digest(new),
        evidence_sha256=_digest(evidence),
        coverage=coverage,
        stale_start=stale_start,
        state="prepared",
        bytes_rewritten=new.stat().st_size,
        rows_rewritten=len(table),
    )
    evidence_bytes = evidence.stat().st_size
    note_temporary(manifest["bytes_rewritten"] + evidence_bytes)
    _fault("before_prepare_manifest")
    _write_manifest(directory, manifest)
    evidence_bytes = evidence.stat().st_size
    _fault("before_pending")
    pending_folder = root / "change-state/pending"
    _mkdir_durable(pending_folder)
    preparing_folder = directory.parent
    directory.replace(pending_folder / operation)
    directory = pending_folder / operation
    _sync_directory(pending_folder)
    _sync_directory(preparing_folder)
    _fault("after_prepared")
    try:
        result = _apply(root, directory, manifest)
    except BaseException as exc:
        if (run := _current_run.get()) is not None:
            run["failed_operations"] = run.get("failed_operations", 0) + 1
        with catalog(root) as conn:
            applied = conn.execute(
                "SELECT state FROM business_changes WHERE operation_id=?", [operation]
            ).fetchone()
        if applied:
            _account_file(metrics, manifest, table, evidence_bytes)
            exc.committed_change = manifest
        # A failed report must not replace the original error or imply rollback.
        try:
            manifest.update(state="recovery_required", error=f"{type(exc).__name__}: {exc}")
            _write_manifest(directory, manifest)
        except Exception:
            pass
        raise
    _account_file(metrics, manifest, table, evidence_bytes)
    note_temporary(manifest["bytes_rewritten"] + evidence_bytes, release=True)
    note_range(
        "rewrite",
        rows=len(table),
        path=target,
        start=manifest.get("changed_start"),
        end=manifest.get("changed_end"),
        bytes_proxy=manifest["bytes_rewritten"],
    )
    result["commit_seconds"] = perf_counter() - started
    return result


def _account_file(metrics, manifest, table, evidence_bytes):
    from .change_observation import add_cost

    add_cost(
        metrics,
        files_rewritten=1,
        file_bytes_rewritten=manifest["bytes_rewritten"],
        rows_rewritten=len(table),
        changed_rows=manifest["changed_rows"],
        changed_fields=manifest["changed_fields"],
        stale_date_marks=manifest.get("stale_date_marks", 0),
        temporary_space_bytes_proxy=manifest["bytes_rewritten"] + evidence_bytes,
    )


def catalog_rows(root, table, keys, rows, *, source, reason, ignore=(), stale_start=None):
    """Apply known catalog row schemas atomically with streaming per-row evidence.

    None preserves existing values except fields explicitly owned by source conflict
    adjudication (dated facts). Missing rows never imply deletes.
    """
    from .change_observation import row_version
    from .daily_storage import _same_values

    allowed = {"security_calendar", "security_lifecycle", "security_daily_facts", "universe"}
    if table not in allowed:
        raise ValueError("Unsupported catalog mutation dataset")
    operation = uuid4().hex
    changed = fields = candidates = 0
    changed_stale_start = None
    with catalog(root) as conn:
        _initialize(conn)
        conn.execute("""CREATE TABLE IF NOT EXISTS catalog_change_rows (
            operation_id VARCHAR, ordinal BIGINT, evidence VARCHAR,
            PRIMARY KEY(operation_id, ordinal))""")
        conn.execute("BEGIN")
        try:
            revision = _revision(conn, table)
            for incoming in rows:
                candidates += 1
                note_range(
                    "catalog_candidate",
                    rows=1,
                    start=incoming.get("trade_date"),
                    end=incoming.get("trade_date"),
                )
                if incoming.get("operation", "update") not in {"insert", "update"}:
                    raise ValueError("Explicit deletions/retractions are unsupported")
                retract = set(incoming.get("_retract_fields", ()))
                if retract and not (
                    table == "security_daily_facts"
                    and source == "baostock"
                    and reason == "dated_security_fact"
                    and retract <= {"pre_close", "is_st"}
                ):
                    raise ValueError("Unsupported explicit field retraction")
                row = {
                    k: v for k, v in incoming.items() if k not in {"operation", "_retract_fields"}
                }
                cursor = conn.execute(
                    f"SELECT * FROM {table} WHERE " + " AND ".join(f"{key}=?" for key in keys),
                    [row[key] for key in keys],
                )
                names = [item[0] for item in cursor.description]
                previous = cursor.fetchone()
                old = dict(zip(names, previous)) if previous else None
                merged = {**(old or {}), **row}
                if old:
                    merged = {
                        k: old.get(k) if v is None and k not in retract else v
                        for k, v in merged.items()
                    }
                business_old = {k: v for k, v in (old or {}).items() if k not in ignore}
                business_new = {k: v for k, v in merged.items() if k not in ignore}
                if old is not None and _same_values(business_old, business_new):
                    continue
                boundary = stale_start(old, merged) if callable(stale_start) else stale_start
                if boundary is not None:
                    changed_stale_start = (
                        min(changed_stale_start, boundary) if changed_stale_start else boundary
                    )
                touched = sorted(
                    k
                    for k in business_old.keys() | business_new.keys()
                    if not _same_values({k: business_old.get(k)}, {k: business_new.get(k)})
                )
                delta = dict(
                    key={k: row[k] for k in keys},
                    operation="update" if old else "insert",
                    fields=touched,
                    before={k: (old or {}).get(k) for k in touched},
                    after={k: merged.get(k) for k in touched},
                    source=source,
                    reason="validated_primary_conflict" if retract else reason,
                    input_row_version=row_version(business_old) if old else None,
                    output_row_version=row_version(business_new),
                )
                _coverage(conn, (table, list(merged), list(merged.values()), keys))
                changed += 1
                fields += len(touched)
                conn.execute(
                    "INSERT INTO catalog_change_rows VALUES (?, ?, ?)",
                    [operation, changed, _json(delta)],
                )
            if changed:
                marks = _stale(conn, changed_stale_start, reason)
                manifest = dict(
                    operation_id=operation,
                    run_id=(_current_run.get() or {}).get("run_id"),
                    object_key=table,
                    input_revision=revision,
                    output_revision=revision + 1,
                    source=source,
                    reason=reason,
                    changed_rows=changed,
                    changed_fields=fields,
                    evidence=f"catalog_change_rows:{operation}",
                )
                _record(conn, manifest)
            _fault("before_catalog_rows_commit")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        if changed:
            note_range("stale_suffix", rows=marks, start=changed_stale_start)
        try:
            observe(conn, table, "ok" if candidates else "empty")
        except Exception as exc:
            if changed:
                exc.committed_catalog_change = manifest
            raise
    return changed


# These counters describe a maintenance invocation, including nested publication
# stages. They are deliberately separate from public dataset/cache contracts.
_current_run = ContextVar("aspool_change_run", default=None)


def attach_run(report):
    """Associate existing source reports with the canonical maintenance run."""
    if (run := _current_run.get()) is not None:
        report["change_run_id"] = run["run_id"]
        run["source_report_id"] = report.get("run_id")
    return report


def note_range(kind, *, rows=0, path=None, start=None, end=None, bytes_proxy=0):
    run = _current_run.get()
    if run is None:
        return
    entry = run["ranges"].setdefault(
        kind, dict(row_visits=0, file_visits=0, bytes_proxy=0, start=None, end=None, path_sample=[])
    )
    entry["row_visits"] += rows
    entry["bytes_proxy"] += bytes_proxy
    if path is not None:
        entry["file_visits"] += 1
        if len(entry["path_sample"]) < 16:
            entry["path_sample"].append(str(path))
    for key, value, select in [("start", start, min), ("end", end, max)]:
        if value is not None:
            value = str(value)
            entry[key] = select(value, entry[key]) if entry[key] else value


def note_lock(seconds):
    if (run := _current_run.get()) is not None:
        run["lock_wait_seconds"] += seconds


def note_temporary(size, *, release=False):
    if (run := _current_run.get()) is not None:
        run["temporary_live_bytes_proxy"] += -size if release else size
        run["temporary_peak_bytes_proxy"] = max(
            run["temporary_peak_bytes_proxy"], run["temporary_live_bytes_proxy"]
        )


@contextmanager
def maintenance_context(root, command):
    import resource

    if _current_run.get() is not None:
        yield _current_run.get()
        return
    started = perf_counter()
    run = dict(
        run_id=uuid4().hex,
        command=command,
        state="running",
        ranges={},
        lock_wait_seconds=0.0,
        temporary_live_bytes_proxy=0,
        temporary_peak_bytes_proxy=0,
        temporary_metric=(
            "live prepared Parquet + row evidence bytes proxy; excludes WAL/library temp"
        ),
        elapsed_metric="function plus lock wait; final summary persistence excluded",
        metrics_complete=False,
        downstream_cache_evictions=None,
        row_count_basis="row/field change events, including repeated edits of a key across stages",
        range_basis="observed ranges and file visits; path samples capped at 16",
        stale_boundary="conservative published suffix; end unbounded until dependency planning",
        downstream_cache_metric="unavailable: DG-06",
        io_metric="logical file bytes proxy; not device I/O",
        read_metric="pool decoded row visits; source transport/device I/O unavailable",
        rss_metric="process high-water RSS, includes earlier work; not task-exclusive",
    )
    # Begin the durable run record only while holding the normal publication
    # lock; otherwise an unlocked network planner could race another writer.
    token = _current_run.set(run)
    primary = None
    try:
        yield run
        run["state"] = (
            "partial_failure"
            if run.get("failed_operations") or run.get("observed_failure")
            else "completed"
        )
    except BaseException as exc:
        primary = exc
        run.update(state="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        run["metrics_complete"] = True
        run["elapsed_seconds"] = perf_counter() - started
        run["peak_rss_process_high_water_bytes"] = (
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        )
        try:
            # This final snapshot is an observation. business_changes remains
            # authoritative even if summary persistence fails.
            # Do not auto-recover a failed operation while reporting its outcome.
            # Reuse the held writer connection/lock, or acquire a raw directory
            # lock for outer invocations that fetched outside a publication stage.
            from .pool import _holds_write_lock
            from .store import _active_catalog

            active = _active_catalog.get()
            if (active is not None and active[0] == Path(root).resolve()) or _holds_write_lock(
                root
            ):
                _finish_run(root, run)
            elif Path(root).exists():
                import fcntl

                fd = os.open(root, os.O_RDONLY)
                try:
                    tick = perf_counter()
                    fcntl.flock(fd, fcntl.LOCK_EX)
                    run["lock_wait_seconds"] += perf_counter() - tick
                    run["elapsed_seconds"] = perf_counter() - started
                    _finish_run(root, run)
                finally:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                    os.close(fd)
        except Exception as exc:
            if primary is None:
                raise RuntimeError(
                    "Maintenance summary unavailable; committed changes are "
                    "recorded in business_changes"
                ) from exc
        finally:
            _current_run.reset(token)


def _finish_run(root, run):
    with catalog(root) as conn:
        _initialize(conn)
        conn.execute("""CREATE TABLE IF NOT EXISTS maintenance_runs (
            run_id VARCHAR PRIMARY KEY, command VARCHAR, state VARCHAR, metrics VARCHAR)""")
        count = conn.execute(
            "SELECT changed_rows, changed_fields, applied_operations "
            "FROM maintenance_totals WHERE run_id=?",
            [run["run_id"]],
        ).fetchone() or (0, 0, 0)
        coverage = conn.execute(
            "SELECT changed_rows FROM coverage_totals WHERE run_id=?", [run["run_id"]]
        ).fetchone()
        run["coverage_metadata_row_events"] = coverage[0] if coverage else 0
        run.update(
            changed_row_events=count[0], changed_field_events=count[1], applied_operations=count[2]
        )
        if pending(root):
            run["state"] = "recovery_required"
        conn.execute(
            "INSERT OR REPLACE INTO maintenance_runs VALUES (?, ?, ?, ?)",
            [run["run_id"], run["command"], run["state"], _json(run)],
        )


def _note_outcome(value):
    run = _current_run.get()
    if run is None:
        return
    entries = value if isinstance(value, (tuple, list)) else (value,)
    for entry in entries:
        children = entry if isinstance(entry, list) else (entry,)
        for item in children:
            if isinstance(item, dict) and (
                item.get("error")
                or item.get("observation_error")
                or item.get("failed")
                or item.get("source_failures")
                or item.get("status") in {"partial", "failed"}
            ):
                run["observed_failure"] = True


def maintenance(function):
    import inspect

    if inspect.iscoroutinefunction(function):

        @wraps(function)
        async def async_wrapped(root, *args, **kwargs):
            with maintenance_context(root, function.__name__):
                result = await function(root, *args, **kwargs)
                _note_outcome(result)
                return result

        return async_wrapped

    @wraps(function)
    def wrapped(root, *args, **kwargs):
        with maintenance_context(root, function.__name__):
            result = function(root, *args, **kwargs)
            _note_outcome(result)
            return result

    return wrapped


# The raw fact helper is also a laboratory/internal entry point. Nested calls
# share the outer invocation rather than allocating one run per catalog row.
catalog_rows = maintenance(catalog_rows)
