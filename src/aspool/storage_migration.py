"""Explicit, recoverable retirement of duplicate production storage."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import time
from pathlib import Path

from .pool import pool_lock
from .sqlite_canonical import ACTION_COLUMNS
from .sqlite_publication import (
    assert_published,
    durable_remove,
    durable_write,
    migration_path,
)

CORE_FILES = (
    "catalog.duckdb",
    "stocks.sqlite",
    "features.sqlite",
    "adjustments.sqlite",
    "etfs.sqlite",
    "indices.sqlite",
    "fundamentals.sqlite",
    "snapshots.sqlite",
)
EVENT_DDL = """CREATE TABLE corporate_actions (
 symbol TEXT NOT NULL, effective_date TEXT NOT NULL,
 record_kind TEXT NOT NULL DEFAULT 'event' CHECK(record_kind='event'),
 source TEXT NOT NULL, source_key TEXT NOT NULL, category INTEGER NOT NULL,
 payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), updated_at INTEGER NOT NULL,
 PRIMARY KEY(symbol,effective_date,record_kind,source,source_key)
) STRICT, WITHOUT ROWID"""


def _reference_ddl(table, kind):
    key = "symbol,effective_date" if kind == "factor" else "symbol,effective_date,source,source_key"
    valid = (
        "cumulative_factor>0 AND valid_from=effective_date AND valid_through>=effective_date "
        "AND factor_basis IS NOT NULL AND source_cumulative_factor IS NULL"
        if kind == "factor"
        else "source_cumulative_factor>0 AND cumulative_factor IS NULL AND event_factor IS NULL"
    )
    return f"""CREATE TABLE adjustments.{table} (
 symbol TEXT NOT NULL, effective_date TEXT NOT NULL,
 record_kind TEXT NOT NULL DEFAULT '{kind}' CHECK(record_kind='{kind}'),
 source TEXT NOT NULL, source_key TEXT NOT NULL DEFAULT 'selected', category INTEGER,
 payload_json TEXT CHECK(payload_json IS NULL OR json_valid(payload_json)),
 source_cumulative_factor REAL, event_factor REAL, cumulative_factor REAL,
 valid_from TEXT, valid_through TEXT, factor_basis TEXT, updated_at INTEGER NOT NULL,
 input_hash TEXT, algorithm_version TEXT NOT NULL DEFAULT 'legacy-v1',
 PRIMARY KEY({key}), UNIQUE(symbol,effective_date,record_kind,source,source_key),
 CHECK({valid})
) STRICT, WITHOUT ROWID"""


def file_digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def verify_recovery(root, recovery, *, compare_live=True):
    root, recovery = Path(root).resolve(), Path(recovery).resolve()
    if recovery.is_relative_to(root) or root.is_relative_to(recovery):
        raise ValueError("Recovery point must be outside the production root")
    manifest = json.loads((recovery.parent / "manifest.json").read_text())
    if manifest.get("status") != "verified":
        raise ValueError("Recovery point has not been verified")
    files = {row["name"]: row for row in manifest["files"]}
    if not set(CORE_FILES) <= files.keys():
        raise ValueError("Recovery point omits a production database")
    for name in CORE_FILES:
        row = files[name]
        if file_digest(recovery / name) != row["sha256"]:
            raise ValueError("Recovery point checksum mismatch: " + name)
        if compare_live and file_digest(root / name) != row["sha256"]:
            raise ValueError("Production changed since the recovery point: " + name)
    return manifest


def _scan(conn, sql, metrics, label):
    digest = hashlib.sha256()
    cursor = conn.execute(sql)
    count = 0
    while rows := cursor.fetchmany(4096):
        digest.update(json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode())
        count += len(rows)
        yield from rows
    metrics[label] = {"rows": count, "sha256": digest.hexdigest()}


def _transfer_features(conn, root, table, target, keys, report):
    columns = [r[1] for r in conn.execute(f'PRAGMA main.table_info("{table}")')]
    projection = ",".join(f'"{name}"' for name in columns)
    ordering = ",".join(f'"{name}"' for name in keys)
    marks = ",".join("?" for _ in columns)
    upsert = f'INSERT OR REPLACE INTO features."{target}" ({projection}) VALUES ({marks})'
    where = " AND ".join(f'"{name}"=?' for name in keys)
    positions = [columns.index(name) for name in keys]
    metrics, changed, deleted = {}, 0, 0
    # A separate read connection pins the pre-migration target while writes go
    # to its WAL through the migration connection.
    with sqlite3.connect((root / "features.sqlite").as_uri() + "?mode=ro", uri=True) as reader:
        reader.execute("BEGIN")
        expected = _scan(
            conn, f'SELECT {projection} FROM main."{table}" ORDER BY {ordering}', metrics, "source"
        )
        actual = _scan(
            reader,
            f'SELECT {projection} FROM "{target}" ORDER BY {ordering}',
            metrics,
            "target_before",
        )
        left, right = next(expected, None), next(actual, None)
        while left is not None or right is not None:
            left_key = tuple(left[i] for i in positions) if left is not None else None
            right_key = tuple(right[i] for i in positions) if right is not None else None
            if right is None or (left is not None and left_key < right_key):
                conn.execute(upsert, left)
                changed += 1
                left = next(expected, None)
            elif left is None or right_key < left_key:
                conn.execute(f'DELETE FROM features."{target}" WHERE {where}', right_key)
                deleted += 1
                right = next(actual, None)
            else:
                if left != right:
                    conn.execute(upsert, left)
                    changed += 1
                left, right = next(expected, None), next(actual, None)
    conn.commit()
    # Full ordered content verification is explicit migration work, never a
    # hidden daily-update scan.
    for _ in _scan(
        conn,
        f'SELECT {projection} FROM features."{target}" ORDER BY {ordering}',
        metrics,
        "target_after",
    ):
        pass
    if metrics["source"] != metrics["target_after"]:
        raise ValueError("Canonical feature transfer failed: " + target)
    report[target] = dict(metrics, copied_rows=changed, deleted_rows=deleted)


def consolidate_storage(root, *, recovery):
    """Move ownership once, verify all rows, then remove old writable objects."""
    from .platform_v2 import _set_layout_version, layout_version
    from .storage_verify import verify_canonical

    root, recovery = Path(root).resolve(), Path(recovery).resolve()
    started = time.monotonic()
    with pool_lock(root, write=True):
        assert_published(root)
        if layout_version(root) != 2:
            raise ValueError("Consolidation requires an explicitly activated layout 2")
        backup = verify_recovery(root, recovery)
        intent = {
            "format": 1,
            "root": str(root),
            "recovery": str(recovery),
            "backup_files": backup["files"],
            "target_layout": 3,
        }
        durable_write(migration_path(root), intent)
        report = {"root": str(root), "recovery": str(recovery), "from_layout": 2, "layout": 3}
        with sqlite3.connect((root / "stocks.sqlite").as_uri() + "?mode=rw", uri=True) as conn:
            for alias, file in (
                ("features", "features.sqlite"),
                ("adjustments", "adjustments.sqlite"),
                ("etf", "etfs.sqlite"),
            ):
                conn.execute(
                    f'ATTACH DATABASE ? AS "{alias}"', ((root / file).as_uri() + "?mode=rw",)
                )
            _transfer_features(
                conn,
                root,
                "daily_features",
                "stock_daily_features",
                ("symbol", "trade_date"),
                report,
            )
            _transfer_features(
                conn,
                root,
                "market_daily_summary",
                "market_regime_features",
                ("frequency", "period_key", "scope"),
                report,
            )
            # Additive board/calendar facts have the same sole features owner.
            from .sqlite_canonical import OPTIONAL_TABLES
            from .sqlite_six_dimension import SCHEMA
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    conn.execute(statement.replace("IF NOT EXISTS ", "IF NOT EXISTS features.", 1))
            conn.commit()
            optional = []
            for table in sorted(OPTIONAL_TABLES):
                shape = conn.execute(f'PRAGMA main.table_info("{table}")').fetchall()
                if not shape:
                    continue
                keys = tuple(row[1] for row in sorted(shape, key=lambda row: row[5]) if row[5])
                _transfer_features(conn, root, table, table, keys, report)
                optional.append(table)
            projection = ",".join(f'"{name}"' for name in ACTION_COLUMNS)
            references = {}
            conn.execute("BEGIN IMMEDIATE")
            for kind, table in (
                ("factor", "stock_adjustment_factors"),
                ("factor_anchor", "stock_factor_anchors"),
            ):
                expected = conn.execute(
                    f"SELECT {projection} FROM main.corporate_actions WHERE record_kind=? "
                    "ORDER BY symbol,effective_date,source,source_key",
                    (kind,),
                ).fetchall()
                conn.execute(f'DROP TABLE adjustments."{table}"')
                conn.execute(_reference_ddl(table, kind))
                conn.execute(
                    f'INSERT INTO adjustments."{table}" ({projection}) '
                    f"SELECT {projection} FROM main.corporate_actions WHERE record_kind=?",
                    (kind,),
                )
                actual = conn.execute(
                    f'SELECT {projection} FROM adjustments."{table}" '
                    "ORDER BY symbol,effective_date,source,source_key"
                ).fetchall()
                if expected != actual:
                    raise ValueError("Reference proof or values lost in migration")
                references[table] = len(actual)
            # ETF references have a sole owner too; preserve their existing
            # source semantics without claiming fresh online coverage.
            expected = conn.execute(
                "SELECT symbol,trade_date,cumulative_factor,source FROM etf.adjustment_factors "
                "ORDER BY symbol,trade_date"
            ).fetchall()
            conn.execute("DELETE FROM adjustments.etf_adjustment_factors")
            conn.execute(
                "INSERT INTO adjustments.etf_adjustment_factors "
                "SELECT symbol,trade_date,cumulative_factor,source,0 FROM etf.adjustment_factors"
            )
            actual = conn.execute(
                "SELECT symbol,effective_date,cumulative_factor,source "
                "FROM adjustments.etf_adjustment_factors ORDER BY symbol,effective_date"
            ).fetchall()
            if expected != actual:
                raise ValueError("ETF references lost in migration")
            references["etf_adjustment_factors"] = len(actual)
            conn.commit()
            # All old authoritative content is verified above. Retire the old
            # objects rather than leave a second production interpretation.
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DROP TABLE main.daily_features")
            conn.execute("DROP TABLE main.market_daily_summary")
            for table in optional:
                conn.execute(f'DROP TABLE main."{table}"')
            conn.execute("ALTER TABLE main.corporate_actions RENAME TO _retired_actions")
            conn.execute(EVENT_DDL)
            event_columns = (
                "symbol,effective_date,record_kind,source,source_key,"
                "category,payload_json,updated_at"
            )
            before = conn.execute(
                f"SELECT {event_columns} FROM _retired_actions WHERE record_kind='event' "
                "ORDER BY symbol,effective_date,source,source_key"
            ).fetchall()
            conn.execute(
                f"INSERT INTO main.corporate_actions ({event_columns}) SELECT {event_columns} "
                "FROM _retired_actions WHERE record_kind='event'"
            )
            after = conn.execute(
                f"SELECT {event_columns} FROM main.corporate_actions "
                "ORDER BY symbol,effective_date,source,source_key"
            ).fetchall()
            if before != after:
                raise ValueError("Source events lost in migration")
            conn.execute("DROP TABLE main._retired_actions")
            conn.execute("DROP TABLE etf.adjustment_factors")
            conn.execute("DROP TABLE IF EXISTS features.migration_state")
            for alias in ("main", "features", "adjustments"):
                conn.execute(f'PRAGMA "{alias}".user_version=3')
            conn.commit()
            report["references"] = references
            report["source_event_rows"] = len(after)
        _set_layout_version(root, 3)
        report["verification"] = verify_canonical(root, allow_migration=True)
        if not report["verification"]["ready"]:
            raise ValueError("Canonical storage verification failed")
        report["seconds"] = time.monotonic() - started
        durable_write(migration_path(root).with_name("completed-migration.json"), report)
        durable_remove(migration_path(root))
    return report


def restore_migration(root, *, recovery):
    """Restore the complete verified pool after a failed migration, never route around it."""
    from .change_protocol import _sync_directory

    root, recovery = Path(root).resolve(), Path(recovery).resolve()
    with pool_lock(root, write=True):
        path = migration_path(root)
        intent = json.loads(path.read_text())
        if intent.get("root") != str(root) or intent.get("recovery") != str(recovery):
            raise ValueError("Recovery point does not match the pending migration")
        verify_recovery(root, recovery, compare_live=False)
        for name in CORE_FILES:
            temporary = root / (name + ".restore")
            shutil.copy2(recovery / name, temporary)
            with temporary.open("rb") as stream:
                import os

                os.fsync(stream.fileno())
            temporary.replace(root / name)
            for suffix in ("-wal", "-shm"):
                (root / (name + suffix)).unlink(missing_ok=True)
        # Persist replacement names and retired WALs before removing the gate.
        _sync_directory(root)
        verify_recovery(root, recovery)
        durable_remove(path)
    return {"restored": True, "root": str(root), "recovery": str(recovery), "layout": 2}
