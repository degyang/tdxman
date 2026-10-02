"""Bounded, durable row intents for a publication across canonical WAL stores."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from uuid import uuid4

from .api_contract import DataPoolError

FILES = {
    "main": "stocks.sqlite",
    "features": "features.sqlite",
    "adjustments": "adjustments.sqlite",
}
TABLES = {
    "main": {"daily_bars", "corporate_actions", "dataset_state", "stock_qfq_bars"},
    "features": {
        "stock_daily_features",
        "market_regime_features",
        "feature_state",
        "market_sessions",
        "board_snapshots",
        "board_snapshot_sets",
        "board_sync_state",
        "board_daily",
        "board_daily_status",
    },
    "adjustments": {"stock_adjustment_factors", "stock_factor_anchors", "adjustment_state"},
}
MAX_ROWS = 250_000
MAX_BYTES = 256 * 1024 * 1024


def state_directory(root):
    root = Path(root).resolve()
    return root.parent / ".aspool-state" / root.name


def pending_path(root):
    return state_directory(root) / "pending-publication.json"


def migration_path(root):
    return state_directory(root) / "pending-migration.json"


def assert_published(root):
    from .base_delta import assert_replica_complete

    assert_replica_complete(root)
    from .qfq_audit_repair import ACTIVE

    history_active = ACTIVE.get() == str(Path(root).resolve())
    if pending_path(root).exists() or (migration_path(root).exists() and not history_active):
        raise DataPoolError(
            "RECOVERY_REQUIRED", "A database publication is incomplete; recover it explicitly"
        )


def _encoded(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")
    ).encode()


def _digest(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


def _fault(phase):
    """Named boundaries for exception and actual process-exit recovery tests."""


def durable_write(path, value):
    from .change_protocol import _mkdir_durable, _sync_directory

    path = Path(path)
    _mkdir_durable(path.parent)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(_encoded(value))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def durable_remove(path):
    from .change_protocol import _sync_directory

    path.unlink()
    _sync_directory(path.parent)


def table_shape(conn, alias, table):
    if alias not in TABLES or table not in TABLES[alias]:
        raise ValueError("Unknown publication table")
    info = conn.execute(f'PRAGMA "{alias}".table_info("{table}")').fetchall()
    columns = [row[1] for row in info]
    keys = [row[1] for row in sorted(info, key=lambda row: row[5]) if row[5]]
    if not columns or not keys:
        raise ValueError("Publication requires an existing keyed table")
    return columns, keys


def read_row(conn, alias, table, columns, keys, key):
    projection = ",".join(f'"{name}"' for name in columns)
    where = " AND ".join(f'"{name}"=?' for name in keys)
    return conn.execute(
        f'SELECT {projection} FROM "{alias}"."{table}" WHERE {where}', key
    ).fetchone()


def prepare_intent(root, rows, shapes):
    assert_published(root)
    if len(rows) > MAX_ROWS:
        raise DataPoolError("LOCAL_UPDATE_BUDGET_EXCEEDED", "Publication row budget exceeded")
    value = {
        "format": 1,
        "layout": 3,
        "root": str(Path(root).resolve()),
        "publication_id": uuid4().hex,
        "prepared_at_us": time.time_ns() // 1000,
        "shapes": shapes,
        "rows": [
            dict(
                row,
                key=list(row["key"]),
                value=list(row["value"]) if row["value"] is not None else None,
            )
            for row in rows
        ],
    }
    value["sha256"] = _digest(value)
    if len(_encoded(value)) > MAX_BYTES:
        raise DataPoolError("LOCAL_UPDATE_BUDGET_EXCEEDED", "Publication byte budget exceeded")
    path = pending_path(root)
    durable_write(path, value)
    _fault("after_intent")
    return value


def validate_intent(root):
    path = pending_path(root)
    if path.stat().st_size > MAX_BYTES:
        raise DataPoolError("RECOVERY_INVALID", "Oversized publication intent")
    try:
        value = json.loads(path.read_bytes())
        checksum = value.pop("sha256")
        if (
            value["format"] != 1
            or value["layout"] != 3
            or value["root"] != str(Path(root).resolve())
            or _digest(value) != checksum
            or len(value["rows"]) > MAX_ROWS
        ):
            raise ValueError("Invalid identity or checksum")
        for row in value["rows"]:
            alias, table = row["alias"], row["table"]
            if alias not in TABLES or table not in TABLES[alias]:
                raise ValueError("Unknown publication table")
            shape = value["shapes"][alias + "." + table]
            if len(row["key"]) != len(shape["keys"]):
                raise ValueError("Invalid primary key")
            if row["value"] is not None and len(row["value"]) != len(shape["columns"]):
                raise ValueError("Invalid row shape")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DataPoolError("RECOVERY_INVALID", "Invalid publication intent") from exc
    value["sha256"] = checksum
    return value


def verify_intent(conn, value):
    for name, shape in value["shapes"].items():
        alias, table = name.split(".")
        columns, keys = table_shape(conn, alias, table)
        if columns != shape["columns"] or keys != shape["keys"]:
            raise DataPoolError("RECOVERY_INVALID", "Publication schema changed")
    for row in value["rows"]:
        shape = value["shapes"][row["alias"] + "." + row["table"]]
        actual = read_row(
            conn, row["alias"], row["table"], shape["columns"], shape["keys"], row["key"]
        )
        if (list(actual) if actual is not None else None) != row["value"]:
            raise DataPoolError("PUBLICATION_INVALID", "Committed publication differs from intent")


def complete_intent(root, conn, value):
    verify_intent(conn, value)
    _fault("after_verification")
    durable_remove(pending_path(root))


def recover_publication(root):
    """Idempotently roll forward a prepared publication under the writer lock."""
    from .pool import pool_lock
    from .qfq_audit_repair import ACTIVE

    root = Path(root).resolve()
    with pool_lock(root, write=True):
        history_active = ACTIVE.get() == str(root) and (
            not migration_path(root).exists()
            or json.loads(migration_path(root).read_text()).get("operation") == "qfq-history-audit"
        )
        if migration_path(root).exists() and not history_active:
            raise DataPoolError(
                "RECOVERY_REQUIRED", "Restore the verified migration recovery point"
            )
        if not pending_path(root).exists():
            return {"recovered": False, "reason": "no pending publication"}
        value = validate_intent(root)
        with sqlite3.connect((root / FILES["main"]).as_uri() + "?mode=rw", uri=True) as conn:
            for alias, file in FILES.items():
                if alias != "main":
                    conn.execute(
                        f'ATTACH DATABASE ? AS "{alias}"',
                        ((root / file).as_uri() + "?mode=rw",),
                    )
            # Validate every shape before the first recovery write.
            for name, shape in value["shapes"].items():
                alias, table = name.split(".")
                columns, keys = table_shape(conn, alias, table)
                if columns != shape["columns"] or keys != shape["keys"]:
                    raise DataPoolError("RECOVERY_INVALID", "Publication schema changed")
            for alias in FILES:
                conn.execute("BEGIN IMMEDIATE")
                for row in value["rows"]:
                    if row["alias"] != alias:
                        continue
                    shape = value["shapes"][alias + "." + row["table"]]
                    where = " AND ".join(f'"{name}"=?' for name in shape["keys"])
                    if row["value"] is None:
                        conn.execute(
                            f'DELETE FROM "{alias}"."{row["table"]}" WHERE {where}', row["key"]
                        )
                    else:
                        columns = ",".join(f'"{name}"' for name in shape["columns"])
                        marks = ",".join("?" for _ in shape["columns"])
                        conn.execute(
                            f'INSERT OR REPLACE INTO "{alias}"."{row["table"]}" '
                            f"({columns}) VALUES ({marks})",
                            row["value"],
                        )
                conn.commit()
                _fault("after_recovery_" + alias)
            complete_intent(root, conn, value)
    return {
        "recovered": True,
        "publication_id": value["publication_id"],
        "rows": len(value["rows"]),
    }
