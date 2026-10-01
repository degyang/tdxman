"""Explicit additive schema maintenance and calendar-based six-dimension inputs."""

from __future__ import annotations

import math
import sqlite3
from datetime import date
from pathlib import Path

from .api_contract import DataPoolError
from .sqlite_daily_derived import finite
from .sqlite_market_metadata import SIX_FIELDS

SCHEMA = """
CREATE TABLE IF NOT EXISTS market_sessions (
    trade_date TEXT PRIMARY KEY
) STRICT, WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS board_snapshot_sets (
    snapshot_id TEXT NOT NULL, kind TEXT NOT NULL,
    membership_as_of TEXT NOT NULL, membership_basis TEXT NOT NULL,
    expected_board_count INTEGER NOT NULL, updated_at INTEGER NOT NULL,
    PRIMARY KEY(snapshot_id,kind)
) STRICT, WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS board_snapshots (
    snapshot_id TEXT NOT NULL, kind TEXT NOT NULL, board_id TEXT NOT NULL,
    board_name TEXT NOT NULL, members_json TEXT NOT NULL CHECK(json_valid(members_json)),
    PRIMARY KEY(snapshot_id,kind,board_id)
) STRICT, WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS board_sync_state (
    kind TEXT PRIMARY KEY, status TEXT NOT NULL, snapshot_id TEXT,
    attempted_at TEXT NOT NULL, error TEXT, updated_at INTEGER NOT NULL
) STRICT, WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS board_daily (
    date TEXT NOT NULL, scope TEXT NOT NULL, classification TEXT NOT NULL,
    kind TEXT NOT NULL, board_id TEXT NOT NULL, board_name TEXT NOT NULL,
    member_count INTEGER NOT NULL, trading_member_count INTEGER NOT NULL,
    valid_return_count INTEGER NOT NULL, avg_return REAL,
    membership_as_of TEXT NOT NULL, membership_basis TEXT NOT NULL,
    snapshot_id TEXT NOT NULL, input_digest TEXT NOT NULL, updated_at INTEGER NOT NULL,
    PRIMARY KEY(date,scope,classification,kind,board_id)
) STRICT, WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS board_daily_status (
    date TEXT NOT NULL, scope TEXT NOT NULL, kind TEXT NOT NULL,
    status TEXT NOT NULL, processed_board_count INTEGER NOT NULL,
    expected_board_count INTEGER, mapped_trading_count INTEGER NOT NULL,
    snapshot_id TEXT, updated_at INTEGER NOT NULL,
    PRIMARY KEY(date,scope,kind)
) STRICT, WITHOUT ROWID;
"""


def six_columns(conn):
    return set(SIX_FIELDS) <= {
        row[1] for row in conn.execute("PRAGMA table_info(market_daily_summary)")
    }


def has_sessions(conn):
    return bool(conn.execute("PRAGMA table_info(market_sessions)").fetchall())


def upgrade_six_dimension_schema(root):
    """Upgrade features atomically under the pool lock; no implicit data rebuild.

    Optional tables remain compatible with pre-extension readers. The storage
    layout number stays private and unchanged. The transaction changes only one
    SQLite file, so existing WAL atomicity suffices for schema publication.
    """
    from .platform_v2 import layout_version
    from .pool import pool_lock
    from .sqlite_publication import assert_published

    root = Path(root).expanduser().resolve()
    with pool_lock(root, write=True):
        assert_published(root)
        version = layout_version(root)
        if version == 2:
            raise DataPoolError("SCHEMA_MAINTENANCE_REQUIRED", "Migrate canonical storage first")
        path = root / ("features.sqlite" if version == 3 else "stocks.sqlite")
        table = "market_regime_features" if version == 3 else "market_daily_summary"
        with sqlite3.connect(path.as_uri() + "?mode=rw", uri=True) as conn:
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            for name in SIX_FIELDS:
                if name not in columns:
                    dtype = "INTEGER" if name.endswith("_count") else "REAL"
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {dtype}")
            # executescript would commit the outer transaction before running.
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    conn.execute(statement)


def save_sessions(conn, sessions):
    """Persist caller-confirmed sessions; calendar corrections are explicit maintenance."""
    if not has_sessions(conn):
        return
    days = sorted(set(sessions))
    if any(date.fromisoformat(day).isoformat() != day for day in days):
        raise ValueError("Expected ISO market sessions")
    conn.executemany("INSERT OR IGNORE INTO market_sessions VALUES (?)", ((day,) for day in days))


def volume_inputs(conn, day, max_rows):
    """Read the exact five calendar predecessors and today's raw EOD volumes.

    Unknown slots never use older rows. A sourced suspension can contribute zero
    even without a bar; NO_TRADE derived from a missing row cannot prove zero.
    """
    if not has_sessions(conn) or not conn.execute(
        "SELECT 1 FROM market_sessions WHERE trade_date=?", (day,)
    ).fetchone():
        return {}, 0, False, 0
    previous = [
        row[0]
        for row in conn.execute(
            "SELECT trade_date FROM market_sessions WHERE trade_date<? ORDER BY trade_date DESC "
            "LIMIT 5",
            (day,),
        )
    ]
    if len(previous) != 5:
        return {}, 0, True, 0
    days = [day, *previous]
    marks = ",".join("?" for _ in days)
    # Feature rows include sourced suspensions that lack raw bars.
    cursor = conn.execute(
        "SELECT f.symbol,f.trade_date,b.volume,f.trading_status,f.trading_status_source,"
        "f.calc_status,f.updated_at,b.updated_at FROM daily_features f "
        "LEFT JOIN daily_bars b USING(symbol,trade_date) "
        f"WHERE f.trade_date IN ({marks})",
        days,
    )
    slots, read_rows, stamp = {}, 0, 0
    for symbol, trade_date, volume, status, source, calc, feature_stamp, bar_stamp in cursor:
        read_rows += 1
        if read_rows > max_rows:
            raise DataPoolError("LOCAL_UPDATE_BUDGET_EXCEEDED", "Volume input row budget exceeded")
        stamp = max(stamp, feature_stamp, bar_stamp or 0)
        value = None
        if (
            status in ("SUSPENDED", "停牌")
            and source
            and not source.startswith(("unknown:", "raw_fallback:", "derived:", "limit_derived:"))
        ):
            if volume is None or finite(volume) and volume == 0:
                value = 0.0
        elif (
            calc == "TRADED"
            and status not in ("NO_TRADE", "NOT_LISTED")
            and finite(volume)
            and volume >= 0
        ):
            value = float(volume)
        slots.setdefault(symbol, {})[trade_date] = value
    result = {}
    for symbol, values in slots.items():
        current = values.get(day)
        preceding = [values.get(item) for item in previous]
        if current is None or any(value is None for value in preceding):
            continue
        denominator = math.fsum(preceding) / 5
        if denominator > 0:
            ratio = current / denominator
            if math.isfinite(ratio):
                result[symbol] = ratio
    return result, read_rows, True, stamp


def volume_affected_sessions(sessions, changed_days):
    days = sorted(set(sessions))
    positions = {day: i for i, day in enumerate(days)}
    return {
        following for day in changed_days for following in days[positions[day] : positions[day] + 6]
    }
