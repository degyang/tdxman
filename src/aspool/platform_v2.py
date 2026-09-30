"""Recoverable shadow migration for the layered data-platform layout."""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path

import duckdb

from .api_contract import DataPoolError
from .fundamentals_store import fundamentals_connection
from .pool import pool_lock

FEATURE_STATE_DDL = """
PRAGMA user_version=1;
CREATE TABLE IF NOT EXISTS feature_state (
    dataset TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    raw_revision INTEGER NOT NULL,
    factor_revision INTEGER,
    algorithm_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('READY','DIRTY','FAILED')),
    affected_from TEXT,
    affected_through TEXT,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY(dataset,scope_key)
) STRICT, WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS migration_state (
    dataset TEXT PRIMARY KEY,
    source_revision INTEGER NOT NULL,
    source_rows INTEGER NOT NULL,
    target_rows INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('COPYING','COMPLETE','FAILED')),
    last_key TEXT,
    updated_at INTEGER NOT NULL
) STRICT, WITHOUT ROWID;
"""

ADJUSTMENTS_DDL = """
PRAGMA user_version=1;
CREATE TABLE stock_adjustment_factors (
    symbol TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    event_factor REAL,
    cumulative_factor REAL NOT NULL CHECK(cumulative_factor>0),
    valid_from TEXT NOT NULL,
    valid_through TEXT NOT NULL,
    factor_basis TEXT NOT NULL,
    source TEXT NOT NULL,
    input_hash TEXT,
    algorithm_version TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY(symbol,effective_date)
) STRICT, WITHOUT ROWID;
CREATE INDEX stock_factors_by_date ON stock_adjustment_factors(effective_date,symbol);
CREATE TABLE stock_factor_anchors (
    symbol TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    source TEXT NOT NULL,
    source_key TEXT NOT NULL,
    source_cumulative_factor REAL NOT NULL CHECK(source_cumulative_factor>0),
    payload_json TEXT,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY(symbol,effective_date,source,source_key)
) STRICT, WITHOUT ROWID;
CREATE TABLE etf_adjustment_factors (
    symbol TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    cumulative_factor REAL NOT NULL CHECK(cumulative_factor>0),
    source TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY(symbol,effective_date)
) STRICT, WITHOUT ROWID;
CREATE TABLE adjustment_state (
    dataset TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    max_date TEXT,
    updated_at INTEGER NOT NULL
) STRICT, WITHOUT ROWID;
"""

SNAPSHOTS_DDL = """
PRAGMA user_version=1;
CREATE TABLE snapshot_catalog (
    snapshot_id TEXT PRIMARY KEY,
    feature_set TEXT NOT NULL,
    feature_version TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    scope TEXT NOT NULL,
    params_hash TEXT NOT NULL,
    input_version TEXT NOT NULL,
    params_json TEXT NOT NULL CHECK(json_valid(params_json)),
    created_at TEXT NOT NULL,
    expires_at TEXT,
    UNIQUE(feature_set,feature_version,as_of_date,asset_type,scope,params_hash,input_version)
) STRICT, WITHOUT ROWID;
CREATE TABLE snapshot_rows (
    snapshot_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    PRIMARY KEY(snapshot_id,entity_id),
    FOREIGN KEY(snapshot_id) REFERENCES snapshot_catalog(snapshot_id) ON DELETE CASCADE
) STRICT, WITHOUT ROWID;
"""


def _connect(path, *, create=False):
    if create and not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb"):
            pass
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    return conn


def _rename_schema(source, target, table, renamed):
    existing = target.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (renamed,)
    ).fetchone()
    if existing:
        return
    sql = source.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if sql is None:
        raise ValueError(f"Missing source table: {table}")
    target.execute(sql[0].replace(f"CREATE TABLE {table}", f"CREATE TABLE {renamed}", 1))


def _create_renamed_indexes(source, target, table, renamed):
    """Build secondary indexes after the bulk copy, never during it."""
    for name, index_sql in source.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
        (table,),
    ):
        target_name = name.replace(table, renamed)
        if target.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (target_name,)
        ).fetchone():
            continue
        target.execute(
            index_sql.replace(name, target_name, 1).replace(f" ON {table}", f" ON {renamed}", 1)
        )


def _ensure_covering_event_index(target):
    """Keep event reads on the small partial index after features are split out."""
    name = "stock_daily_features_events"
    row = target.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (name,)
    ).fetchone()
    if row and "limit_status" in row[0] and "updated_at" in row[0]:
        return
    target.execute(f"DROP INDEX IF EXISTS {name}")
    target.execute(
        f"CREATE INDEX {name} ON stock_daily_features("
        "trade_date,symbol,calc_status,touch_limit_up,touch_limit_down,"
        "close_limit_up,close_limit_down,limit_up_price,limit_down_price,"
        "consecutive_up,prior_consecutive_up,streak_known,is_st,limit_status,updated_at) "
        "WHERE close_limit_up=1 OR touch_limit_up=1 "
        "OR close_limit_down=1 OR touch_limit_down=1"
    )


def _copy_feature_table(source, target, table, renamed, *, source_revision, batch_symbols=256):
    columns = [row[1] for row in source.execute(f"PRAGMA table_info({table})")]
    projection = ",".join('"' + name + '"' for name in columns)
    state = target.execute(
        "SELECT source_revision,source_rows,target_rows,status,last_key "
        "FROM migration_state WHERE dataset=?",
        (renamed,),
    ).fetchone()
    source_rows = state[1] if state and state[0] == source_revision else None
    if source_rows is None:
        source_rows = source.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    if state and state[:4] == (source_revision, source_rows, source_rows, "COMPLETE"):
        return {"rows": source_rows, "revision": source_revision, "reused": True}
    if state and state[0] != source_revision:
        target.execute(f"DELETE FROM {renamed}")
        state = None
    last = state[4] if state and state[3] == "COPYING" else ""
    copied = state[2] if state and state[3] == "COPYING" else 0
    stamp = time.time_ns() // 1000
    target.execute(
        "INSERT OR REPLACE INTO migration_state VALUES (?,?,?,?,?,?,?)",
        (
            renamed,
            source_revision,
            source_rows,
            state[2] if state and state[3] == "COPYING" else 0,
            "COPYING",
            last or None,
            stamp,
        ),
    )
    target.commit()
    source_path = Path(source.execute("PRAGMA database_list").fetchone()[2]).resolve()
    target.execute("ATTACH DATABASE ? AS migration_source", (str(source_path),))
    try:
        while True:
            symbols = [
                row[0]
                for row in source.execute(
                    f"SELECT DISTINCT symbol FROM {table} WHERE symbol>? ORDER BY symbol LIMIT ?",
                    (last, batch_symbols),
                )
            ]
            if not symbols:
                break
            marks = ",".join("?" for _ in symbols)
            target.execute("BEGIN IMMEDIATE")
            target.execute(
                f"INSERT OR REPLACE INTO {renamed}({projection}) "
                f"SELECT {projection} FROM migration_source.{table} "
                f"WHERE symbol IN ({marks})",
                symbols,
            )
            copied += target.execute("SELECT changes()").fetchone()[0]
            last = symbols[-1]
            target.execute(
                "UPDATE migration_state SET target_rows=?,last_key=?,updated_at=? WHERE dataset=?",
                (copied, last, time.time_ns() // 1000, renamed),
            )
            target.commit()
    finally:
        if target.in_transaction:
            target.rollback()
        target.execute("DETACH DATABASE migration_source")
    copied = target.execute(f"SELECT count(*) FROM {renamed}").fetchone()[0]
    if copied != source_rows:
        raise ValueError(f"{renamed} row mismatch: {copied} != {source_rows}")
    target.execute(
        "UPDATE migration_state SET target_rows=?,status='COMPLETE',last_key=NULL,updated_at=? "
        "WHERE dataset=?",
        (copied, time.time_ns() // 1000, renamed),
    )
    target.commit()
    return {"rows": copied, "revision": source_revision, "reused": False}


def _copy_market_summary(source, target):
    table, renamed = "market_daily_summary", "market_regime_features"
    columns = [row[1] for row in source.execute(f"PRAGMA table_info({table})")]
    projection = ",".join('"' + name + '"' for name in columns)
    rows = source.execute(f"SELECT {projection} FROM {table}").fetchall()
    target.execute("BEGIN IMMEDIATE")
    target.execute(f"DELETE FROM {renamed}")
    target.executemany(
        f"INSERT INTO {renamed}({projection}) VALUES (" + ",".join("?" for _ in columns) + ")",
        rows,
    )
    revision = max((row[columns.index("updated_at")] for row in rows), default=0)
    target.execute(
        "INSERT OR REPLACE INTO migration_state VALUES (?,?,?,?,?,?,?)",
        (renamed, revision, len(rows), len(rows), "COMPLETE", None, time.time_ns() // 1000),
    )
    target.commit()
    return {"rows": len(rows), "revision": revision, "reused": False}


def _prepare_features(root, *, factor_revision):
    source = sqlite3.connect((root / "stocks.sqlite").as_uri() + "?mode=ro", uri=True, timeout=30)
    target = _connect(root / "features.sqlite", create=True)
    try:
        _rename_schema(source, target, "daily_features", "stock_daily_features")
        _rename_schema(source, target, "market_daily_summary", "market_regime_features")
        target.executescript(FEATURE_STATE_DDL)
        if target.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("Unsupported features schema")
        raw_state = source.execute(
            "SELECT revision,max_date FROM dataset_state WHERE dataset='stock_raw'"
        ).fetchone()
        if raw_state is None:
            raise ValueError("stock_raw revision is unavailable")
        raw_revision = raw_state[0]
        daily = _copy_feature_table(
            source,
            target,
            "daily_features",
            "stock_daily_features",
            source_revision=raw_revision,
        )
        _create_renamed_indexes(source, target, "daily_features", "stock_daily_features")
        _ensure_covering_event_index(target)
        market = _copy_market_summary(source, target)
        _create_renamed_indexes(source, target, "market_daily_summary", "market_regime_features")
        stamp = time.time_ns() // 1000
        target.execute(
            "INSERT OR REPLACE INTO feature_state VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "stock_daily_features",
                "all",
                raw_revision,
                factor_revision,
                "legacy-v1",
                "READY",
                None,
                None,
                stamp,
            ),
        )
        target.execute(
            "INSERT OR REPLACE INTO feature_state VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "market_regime_features",
                "all",
                raw_revision,
                factor_revision,
                "legacy-v1",
                "READY",
                None,
                None,
                stamp,
            ),
        )
        target.commit()
        return {"stock_daily_features": daily, "market_regime_features": market}
    finally:
        target.close()
        source.close()


def _reconcile_features(root):
    """Precise rebuild of feature mirror from authoritative stocks.sqlite.

    Deletes stale target rows before copying so that source deletions are
    reflected.  Runs inside a single transaction so an interruption leaves
    the previous state intact.
    """
    source = sqlite3.connect((root / "stocks.sqlite").as_uri() + "?mode=ro", uri=True, timeout=30)
    target = _connect(root / "features.sqlite")
    adj_conn = _connect(root / "adjustments.sqlite")
    try:
        if target.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("features.sqlite is not prepared")
        raw_state = source.execute(
            "SELECT revision FROM dataset_state WHERE dataset='stock_raw'"
        ).fetchone()
        raw_revision = raw_state[0] if raw_state else 0
        factor_row = adj_conn.execute(
            "SELECT revision FROM adjustment_state WHERE dataset='stock_adjustment_factors'"
        ).fetchone()
        factor_revision = factor_row[0] if factor_row else raw_revision
        target.execute("BEGIN IMMEDIATE")
        for dataset in ("stock_daily_features", "market_regime_features"):
            target.execute(
                "UPDATE feature_state SET status='DIRTY',updated_at=? WHERE dataset=?",
                (time.time_ns() // 1000, dataset),
            )
        target.execute("DELETE FROM stock_daily_features")
        target.execute("DELETE FROM market_regime_features")
        # ATTACH source: INSERT SELECT runs inside SQLite, no Python-side row materialisation.
        target.execute("ATTACH DATABASE ? AS _recon_src", (str(root / "stocks.sqlite"),))
        try:
            columns = [row[1] for row in source.execute("PRAGMA table_info(daily_features)")]
            projection = ",".join('"' + name + '"' for name in columns)
            target.execute(
                f"INSERT INTO stock_daily_features({projection}) "
                f"SELECT {projection} FROM _recon_src.daily_features"
            )
            feature_rows = target.execute("SELECT changes()").fetchone()[0]
            mkt_columns = [
                row[1] for row in source.execute("PRAGMA table_info(market_daily_summary)")
            ]
            mkt_projection = ",".join('"' + name + '"' for name in mkt_columns)
            target.execute(
                f"INSERT INTO market_regime_features({mkt_projection}) "
                f"SELECT {mkt_projection} FROM _recon_src.market_daily_summary"
            )
            summary_rows = target.execute("SELECT changes()").fetchone()[0]
        finally:
            try:
                target.execute("DETACH DATABASE _recon_src")
            except sqlite3.OperationalError:
                pass  # locked by active transaction; auto-detached on commit
        _create_renamed_indexes(source, target, "daily_features", "stock_daily_features")
        _ensure_covering_event_index(target)
        _create_renamed_indexes(source, target, "market_daily_summary", "market_regime_features")
        stamp = time.time_ns() // 1000
        for dataset in ("stock_daily_features", "market_regime_features"):
            target.execute(
                "INSERT OR REPLACE INTO feature_state VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    dataset,
                    "all",
                    raw_revision,
                    factor_revision,
                    "legacy-v1",
                    "READY",
                    None,
                    None,
                    stamp,
                ),
            )
        target.commit()
        return {
            "stock_daily_features": {"rows": feature_rows, "revision": raw_revision},
            "market_regime_features": {"rows": summary_rows, "revision": raw_revision},
        }
    finally:
        adj_conn.close()
        target.close()
        source.close()


def _prepare_adjustments(root):
    path = root / "adjustments.sqlite"
    conn = _connect(path, create=True)
    try:
        if conn.execute("PRAGMA user_version").fetchone()[0] == 0:
            conn.executescript(ADJUSTMENTS_DDL)
        if conn.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("Unsupported adjustments schema")
        source = sqlite3.connect(
            (root / "stocks.sqlite").as_uri() + "?mode=ro", uri=True, timeout=30
        )
        try:
            factors = source.execute(
                "SELECT symbol,effective_date,event_factor,cumulative_factor,valid_from,"
                "valid_through,factor_basis,source,NULL,'legacy-v1',updated_at "
                "FROM corporate_actions WHERE record_kind='factor'"
            ).fetchall()
            anchors = source.execute(
                "SELECT symbol,effective_date,source,source_key,source_cumulative_factor,"
                "payload_json,updated_at FROM corporate_actions WHERE record_kind='factor_anchor'"
            ).fetchall()
        finally:
            source.close()
        etf_path = root / "etfs.sqlite"
        etf = []
        if etf_path.exists():
            source = sqlite3.connect(etf_path.as_uri() + "?mode=ro", uri=True)
            try:
                etf = [
                    (*row, 0)
                    for row in source.execute(
                        "SELECT symbol,trade_date,cumulative_factor,source FROM adjustment_factors"
                    )
                ]
            finally:
                source.close()
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM stock_adjustment_factors")
        conn.execute("DELETE FROM stock_factor_anchors")
        conn.execute("DELETE FROM etf_adjustment_factors")
        conn.executemany(
            "INSERT OR REPLACE INTO stock_adjustment_factors VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            factors,
        )
        conn.executemany(
            "INSERT OR REPLACE INTO stock_factor_anchors VALUES (?,?,?,?,?,?,?)", anchors
        )
        conn.executemany("INSERT OR REPLACE INTO etf_adjustment_factors VALUES (?,?,?,?,?)", etf)
        stamp = time.time_ns() // 1000
        for dataset, table in (
            ("stock_adjustment_factors", "stock_adjustment_factors"),
            ("etf_adjustment_factors", "etf_adjustment_factors"),
        ):
            revision, maximum = conn.execute(
                f"SELECT coalesce(max(updated_at),0),max(effective_date) FROM {table}"
            ).fetchone()
            conn.execute(
                "INSERT OR REPLACE INTO adjustment_state VALUES (?,?,?,?)",
                (dataset, revision, maximum, stamp),
            )
        conn.commit()
        return {
            "stock_adjustment_factors": len(factors),
            "stock_factor_anchors": len(anchors),
            "etf_adjustment_factors": len(etf),
        }
    finally:
        conn.close()


def _prepare_empty_stores(root):
    if not (root / "fundamentals.sqlite").exists():
        with fundamentals_connection(root, create=True, read_only=False):
            pass
    path = root / "snapshots.sqlite"
    conn = _connect(path, create=True)
    try:
        if conn.execute("PRAGMA user_version").fetchone()[0] == 0:
            conn.executescript(SNAPSHOTS_DDL)
        if conn.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("Unsupported snapshots schema")
    finally:
        conn.close()


def _ensure_layout_metadata(root):
    with duckdb.connect(str(root / "catalog.duckdb")) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS pool_metadata("
            "key VARCHAR PRIMARY KEY,value VARCHAR NOT NULL,updated_at TIMESTAMP NOT NULL)"
        )
        conn.execute(
            "INSERT INTO pool_metadata VALUES ('layout_version','1',current_timestamp) "
            "ON CONFLICT(key) DO NOTHING"
        )


def prepare_platform_v2(root):
    """Copy target stores under the writer lock without switching public reads."""
    root = Path(root).expanduser().resolve()
    if layout_version(root) == 3:
        return {"layout_version": 3, "activated": True, "verification": verify_platform_v2(root)}
    for required in ("catalog.duckdb", "stocks.sqlite", "indices.sqlite", "etfs.sqlite"):
        if not (root / required).is_file():
            raise FileNotFoundError(root / required)
    with pool_lock(root, write=True):
        with sqlite3.connect(root / "stocks.sqlite") as stocks:
            stocks.execute(
                "CREATE TABLE IF NOT EXISTS dataset_state("
                "dataset TEXT PRIMARY KEY,revision INTEGER NOT NULL,max_date TEXT,"
                "updated_at INTEGER NOT NULL) STRICT, WITHOUT ROWID"
            )
            if (
                stocks.execute("SELECT 1 FROM dataset_state WHERE dataset='stock_raw'").fetchone()
                is None
            ):
                revision = max(
                    stocks.execute("SELECT coalesce(max(updated_at),0) FROM daily_bars").fetchone()[
                        0
                    ],
                    stocks.execute(
                        "SELECT coalesce(max(updated_at),0) FROM corporate_actions"
                    ).fetchone()[0],
                )
                maximum = stocks.execute("SELECT max(trade_date) FROM daily_bars").fetchone()[0]
                stocks.execute(
                    "INSERT INTO dataset_state VALUES ('stock_raw',?,?,?)",
                    (revision, maximum, time.time_ns() // 1000),
                )
        adjustments = _prepare_adjustments(root)
        with sqlite3.connect(root / "adjustments.sqlite") as factor_store:
            factor_revision = factor_store.execute(
                "SELECT revision FROM adjustment_state WHERE dataset='stock_adjustment_factors'"
            ).fetchone()[0]
        features = _prepare_features(root, factor_revision=factor_revision)
        _prepare_empty_stores(root)
        _ensure_layout_metadata(root)
    version = layout_version(root)
    return {
        "layout_version": version,
        "activated": version >= 2,
        "features": features,
        "adjustments": adjustments,
        "verification": verify_platform_v2(root),
    }


def require_current_mirror(root):
    """Check the previous commit before starting another bounded source write.

    Call while holding the pool write lock. A failed mirror must be recovered
    explicitly; a later, unrelated slice cannot certify its missing rows.
    """
    root = Path(root).resolve()
    if layout_version(root) == 3:
        from .sqlite_publication import assert_published
        from .storage_verify import assert_coherent

        assert_published(root)
        with sqlite3.connect((root / "stocks.sqlite").as_uri() + "?mode=ro", uri=True) as conn:
            for alias, file in (
                ("features", "features.sqlite"),
                ("adjustments", "adjustments.sqlite"),
            ):
                conn.execute(
                    f'ATTACH DATABASE ? AS "{alias}"', ((root / file).as_uri() + "?mode=ro",)
                )
            assert_coherent(conn)
        return
    if not (root / "features.sqlite").is_file():
        return
    with (
        closing(sqlite3.connect((root / "stocks.sqlite").as_uri() + "?mode=ro", uri=True)) as raw,
        closing(
            sqlite3.connect((root / "features.sqlite").as_uri() + "?mode=ro", uri=True)
        ) as features,
        closing(
            sqlite3.connect((root / "adjustments.sqlite").as_uri() + "?mode=ro", uri=True)
        ) as factors,
    ):
        revision = raw.execute(
            "SELECT revision FROM dataset_state WHERE dataset='stock_raw'"
        ).fetchone()
        factor = factors.execute(
            "SELECT revision FROM adjustment_state WHERE dataset='stock_adjustment_factors'"
        ).fetchone()
        states = features.execute(
            "SELECT dataset,raw_revision,factor_revision,status FROM feature_state "
            "WHERE scope_key='all'"
        ).fetchall()
    if (
        revision is None
        or factor is None
        or {row[0] for row in states} != {"stock_daily_features", "market_regime_features"}
        or any(row[1:] != (revision[0], factor[0], "READY") for row in states)
    ):
        raise DataPoolError(
            "DERIVED_NOT_READY",
            "Previous mirror is incomplete; run aspool platform reconcile --root " + str(root),
        )


def mirror_platform_v2(root, *, symbols, dates, raw_revision, previous_raw_revision=None):
    """Serialize source reads and target commits, including direct maintenance calls."""
    with pool_lock(root, write=True):
        if layout_version(root) == 3:
            require_current_mirror(root)
            return {"mirrored": False, "reason": "canonical storage has no mirror"}
        return _mirror_platform_v2(
            root,
            symbols=symbols,
            dates=dates,
            raw_revision=raw_revision,
            previous_raw_revision=previous_raw_revision,
        )


def _mirror_platform_v2(root, *, symbols, dates, raw_revision, previous_raw_revision):
    """Copy only rows affected by one committed stock update into shadow stores."""
    root = Path(root).expanduser().resolve()
    feature_path = root / "features.sqlite"
    if not feature_path.is_file():
        return {"mirrored": False, "reason": "features.sqlite is not prepared"}
    symbols, dates = sorted(set(symbols)), sorted(set(dates))
    if not symbols and not dates:
        return {"mirrored": False, "reason": "no affected rows"}
    # A matching global revision does not prove that every requested symbol was
    # mirrored before an interrupted run.  Explicit symbols must always repair
    # their factor rows, even when no feature date changed.
    source = sqlite3.connect((root / "stocks.sqlite").as_uri() + "?mode=ro", uri=True, timeout=30)
    target = _connect(feature_path)
    adjustments = _connect(root / "adjustments.sqlite")
    try:
        advance_unchanged = (
            not dates
            and previous_raw_revision is not None
            and raw_revision != previous_raw_revision
        )
        if dates or advance_unchanged:
            source_revision = source.execute(
                "SELECT revision FROM dataset_state WHERE dataset='stock_raw'"
            ).fetchone()
            states = target.execute(
                "SELECT dataset,raw_revision,status FROM feature_state WHERE scope_key='all'"
            ).fetchall()
            # Legacy direct callers may mirror one new revision or retry a no-op.
            # Writers supply the exact predecessor recorded in their transaction.
            predecessors = (
                {previous_raw_revision}
                if previous_raw_revision is not None
                else {raw_revision, raw_revision - 1}
            )
            if (
                source_revision != (raw_revision,)
                or {row[0] for row in states} != {"stock_daily_features", "market_regime_features"}
                or any(row[1] not in predecessors or row[2] != "READY" for row in states)
            ):
                raise DataPoolError(
                    "DERIVED_NOT_READY",
                    "Mirror predecessor is incomplete; run aspool platform reconcile --root "
                    + str(root),
                )
        target.execute("BEGIN IMMEDIATE")
        feature_rows = summary_rows = 0
        if symbols and dates:
            columns = [row[1] for row in source.execute("PRAGMA table_info(daily_features)")]
            projection = ",".join('"' + name + '"' for name in columns)
            symbol_marks = ",".join("?" for _ in symbols)
            date_marks = ",".join("?" for _ in dates)
            rows = source.execute(
                f"SELECT {projection} FROM daily_features WHERE symbol IN ({symbol_marks}) "
                f"AND trade_date IN ({date_marks})",
                [*symbols, *dates],
            ).fetchall()
            target.executemany(
                f"INSERT OR REPLACE INTO stock_daily_features({projection}) VALUES ("
                + ",".join("?" for _ in columns)
                + ")",
                rows,
            )
            feature_rows = len(rows)
        if dates:
            columns = [row[1] for row in source.execute("PRAGMA table_info(market_daily_summary)")]
            projection = ",".join('"' + name + '"' for name in columns)
            marks = ",".join("?" for _ in dates)
            rows = source.execute(
                f"SELECT {projection} FROM market_daily_summary "
                f"WHERE frequency='D' AND period_key IN ({marks})",
                dates,
            ).fetchall()
            target.executemany(
                f"INSERT OR REPLACE INTO market_regime_features({projection}) VALUES ("
                + ",".join("?" for _ in columns)
                + ")",
                rows,
            )
            summary_rows = len(rows)
        stamp = time.time_ns() // 1000
        if dates:
            for dataset in ("stock_daily_features", "market_regime_features"):
                target.execute(
                    "UPDATE feature_state SET raw_revision=?,status='DIRTY',"
                    "affected_from=?,affected_through=?,updated_at=? "
                    "WHERE dataset=? AND scope_key='all'",
                    (raw_revision, min(dates), max(dates), stamp, dataset),
                )
            target.commit()
        factor_rows = anchor_rows = 0
        if symbols:
            marks = ",".join("?" for _ in symbols)
            factors = source.execute(
                "SELECT symbol,effective_date,event_factor,cumulative_factor,valid_from,"
                "valid_through,factor_basis,source,NULL,'legacy-v1',updated_at "
                f"FROM corporate_actions WHERE record_kind='factor' AND symbol IN ({marks})",
                symbols,
            ).fetchall()
            anchors = source.execute(
                "SELECT symbol,effective_date,source,source_key,source_cumulative_factor,"
                f"payload_json,updated_at FROM corporate_actions WHERE record_kind='factor_anchor' "
                f"AND symbol IN ({marks})",
                symbols,
            ).fetchall()
            adjustments.execute("BEGIN IMMEDIATE")
            adjustments.execute(
                f"DELETE FROM stock_adjustment_factors WHERE symbol IN ({marks})", symbols
            )
            adjustments.execute(
                f"DELETE FROM stock_factor_anchors WHERE symbol IN ({marks})", symbols
            )
            adjustments.executemany(
                "INSERT INTO stock_adjustment_factors VALUES (?,?,?,?,?,?,?,?,?,?,?)", factors
            )
            adjustments.executemany(
                "INSERT INTO stock_factor_anchors VALUES (?,?,?,?,?,?,?)", anchors
            )
            factor_revision, maximum = adjustments.execute(
                "SELECT coalesce(max(updated_at),0),max(effective_date) "
                "FROM stock_adjustment_factors"
            ).fetchone()
            adjustments.execute(
                "INSERT OR REPLACE INTO adjustment_state VALUES (?,?,?,?)",
                ("stock_adjustment_factors", factor_revision, maximum, time.time_ns() // 1000),
            )
            adjustments.commit()
            factor_rows, anchor_rows = len(factors), len(anchors)
        else:
            factor_revision = adjustments.execute(
                "SELECT revision FROM adjustment_state WHERE dataset='stock_adjustment_factors'"
            ).fetchone()[0]
        # Conditional READY: only advance status when feature content was actually written.
        # Pure factor mirrors (dates empty) must not mark stale features as READY.
        stamp = time.time_ns() // 1000
        for dataset, rows in (
            ("stock_daily_features", feature_rows),
            ("market_regime_features", summary_rows),
        ):
            if rows > 0 or (dataset == "stock_daily_features" and not symbols and summary_rows):
                target.execute(
                    "UPDATE feature_state SET raw_revision=?,factor_revision=?,"
                    "status='READY',updated_at=? WHERE dataset=? AND scope_key='all'",
                    (raw_revision, factor_revision, stamp, dataset),
                )
            else:
                target.execute(
                    "UPDATE feature_state SET factor_revision=?,updated_at=?,"
                    "raw_revision=CASE WHEN ? THEN ? ELSE raw_revision END "
                    "WHERE dataset=? AND scope_key='all'",
                    (factor_revision, stamp, advance_unchanged, raw_revision, dataset),
                )
        target.commit()
    except BaseException:
        # Roll back any open transaction; the last committed state (DIRTY or
        # READY) survives.  Reconcile will detect and repair the inconsistency.
        target.rollback()
        raise
    finally:
        adjustments.close()
        target.close()
        source.close()
    return {
        "mirrored": True,
        "feature_rows": feature_rows,
        "summary_rows": summary_rows,
        "factor_rows": factor_rows,
        "anchor_rows": anchor_rows,
    }


def activate_platform_v2(root):
    """Atomically switch public reads only after row and revision verification."""
    root = Path(root).expanduser().resolve()
    with pool_lock(root, write=True):
        if layout_version(root) == 3:
            return verify_platform_v2(root)
        check = verify_platform_v2(root, deep=True)
        if not check["ready"]:
            raise ValueError("Platform v2 verification has not passed")
        with (
            sqlite3.connect(root / "stocks.sqlite") as stocks,
            sqlite3.connect(root / "features.sqlite") as features,
            sqlite3.connect(root / "adjustments.sqlite") as adjustments,
        ):
            raw = stocks.execute(
                "SELECT revision FROM dataset_state WHERE dataset='stock_raw'"
            ).fetchone()
            states = features.execute(
                "SELECT dataset,raw_revision,factor_revision,status "
                "FROM feature_state WHERE scope_key='all'"
            ).fetchall()
            factor = adjustments.execute(
                "SELECT revision FROM adjustment_state WHERE dataset='stock_adjustment_factors'"
            ).fetchone()
        if raw is None or {row[0] for row in states} != {
            "stock_daily_features",
            "market_regime_features",
        }:
            raise ValueError("Platform revisions are incomplete")
        if factor is None or any(
            row[1] != raw[0] or row[2] != factor[0] or row[3] != "READY" for row in states
        ):
            raise ValueError("Platform derived revisions do not match stock_raw")
        _set_layout_version(root, 2)
    return verify_platform_v2(root)


def reconcile_platform_v2(root):
    """Rebuild feature and adjustment mirrors from authoritative local stores."""
    root = Path(root).expanduser().resolve()
    if layout_version(root) == 3:
        raise DataPoolError(
            "MIRROR_RETIRED", "Canonical storage has no mirror; use platform recover"
        )
    for required in ("stocks.sqlite", "etfs.sqlite", "adjustments.sqlite", "features.sqlite"):
        if not (root / required).is_file():
            raise FileNotFoundError(root / required)
    with pool_lock(root, write=True):
        adjustments = _prepare_adjustments(root)
        features = _reconcile_features(root)
        verification = verify_platform_v2(root, deep=True)
    return {"adjustments": adjustments, "features": features, "verification": verification}


def rollback_platform_v2(root):
    """Route public reads back to layout 1 while retaining all shadow files."""
    root = Path(root).expanduser().resolve()
    with pool_lock(root, write=True):
        if layout_version(root) == 3:
            raise DataPoolError(
                "LAYOUT_RETIRED", "Restore a verified recovery point; old tables are retired"
            )
        _set_layout_version(root, 1)
    return platform_status(root)


def _set_layout_version(root, version):
    with duckdb.connect(str(root / "catalog.duckdb")) as catalog:
        catalog.execute("BEGIN")
        catalog.execute(
            "UPDATE pool_metadata SET value=?,updated_at=current_timestamp "
            "WHERE key='layout_version'",
            [str(version)],
        )
        catalog.execute("COMMIT")


def layout_version(root):
    root = Path(root).expanduser().resolve()
    path = root / "catalog.duckdb"
    raw_schema = 1
    raw_path = root / "stocks.sqlite"
    if raw_path.is_file():
        try:
            with sqlite3.connect(raw_path.as_uri() + "?mode=ro", uri=True) as raw:
                raw_schema = raw.execute("PRAGMA user_version").fetchone()[0]
        except sqlite3.Error as exc:
            raise DataPoolError("LAYOUT_INVALID", "Cannot identify the stock store schema") from exc
    if not path.is_file():
        if raw_schema >= 3:
            raise DataPoolError("LAYOUT_INVALID", "Canonical layout catalog is missing")
        return 1
    try:
        with duckdb.connect(str(path), read_only=True) as catalog:
            metadata = catalog.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name='pool_metadata'"
            ).fetchone()
            if metadata is None:
                if raw_schema >= 3:
                    raise DataPoolError("LAYOUT_INVALID", "Canonical layout metadata is missing")
                return 1
            row = catalog.execute(
                "SELECT value FROM pool_metadata WHERE key='layout_version'"
            ).fetchone()
    except duckdb.Error as exc:
        raise DataPoolError("LAYOUT_UNAVAILABLE", "Cannot read the pool layout") from exc
    if row is None:
        if raw_schema >= 3:
            raise DataPoolError("LAYOUT_INVALID", "Canonical layout version is missing")
        return 1
    try:
        version = int(row[0])
    except (TypeError, ValueError) as exc:
        raise DataPoolError("LAYOUT_INVALID", "Invalid pool layout version") from exc
    if version not in {1, 2, 3}:
        raise DataPoolError("LAYOUT_INVALID", "Unsupported pool layout version")
    if raw_schema >= 3 and version != 3:
        raise DataPoolError("LAYOUT_INVALID", "Retired layout cannot select canonical storage")
    return version


def verify_platform_v2(root, *, deep=False):
    """Verify a stable pool view while excluding concurrent canonical writers."""
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        return _verify_platform_v2(root, deep=deep)
    with pool_lock(root):
        return _verify_platform_v2(root, deep=deep)


def _verify_platform_v2(root, *, deep=False):
    root = Path(root).expanduser().resolve()
    if layout_version(root) == 3:
        from .storage_verify import verify_canonical

        return verify_canonical(root, deep=deep)
    required = [
        "catalog.duckdb",
        "stocks.sqlite",
        "indices.sqlite",
        "etfs.sqlite",
        "fundamentals.sqlite",
        "adjustments.sqlite",
        "features.sqlite",
        "snapshots.sqlite",
    ]
    missing = [name for name in required if not (root / name).is_file()]
    result = {
        "ready": not missing,
        "missing": missing,
        "counts": {},
        "verification_level": "deep" if deep else "shallow",
    }
    if missing:
        return result
    with (
        sqlite3.connect(root / "stocks.sqlite") as old,
        sqlite3.connect(root / "features.sqlite") as new,
    ):
        if new.execute("PRAGMA user_version").fetchone()[0] != 1:
            result["ready"] = False
        # Row-count comparison (always, fast) + optional full EXCEPT (deep, slow).
        source_path = (root / "stocks.sqlite").resolve()
        for source, target in (
            ("daily_features", "stock_daily_features"),
            ("market_daily_summary", "market_regime_features"),
        ):
            source_count = old.execute(f"SELECT count(*) FROM {source}").fetchone()[0]
            target_count = new.execute(f"SELECT count(*) FROM {target}").fetchone()[0]
            entry = {"source": source_count, "target": target_count}
            if deep:
                new.execute("ATTACH DATABASE ? AS _verify_src", (str(source_path),))
                try:
                    extra = new.execute(
                        f"SELECT count(*) FROM "
                        f"(SELECT * FROM {target} EXCEPT SELECT * FROM _verify_src.{source})"
                    ).fetchone()[0]
                    missing_rows = new.execute(
                        f"SELECT count(*) FROM "
                        f"(SELECT * FROM _verify_src.{source} EXCEPT SELECT * FROM {target})"
                    ).fetchone()[0]
                finally:
                    new.execute("DETACH DATABASE _verify_src")
                entry["extra"] = extra
                entry["missing"] = missing_rows
                content_equal = extra == 0 and missing_rows == 0
            else:
                content_equal = None
            entry["count_equal"] = source_count == target_count
            entry["content_equal"] = content_equal
            result["counts"][target] = entry
            result["ready"] = (
                result["ready"] and entry["count_equal"] and content_equal is not False
            )
        event_index = new.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' "
            "AND name='stock_daily_features_events'"
        ).fetchone()
        result["event_index_covering"] = bool(
            event_index and "limit_status" in event_index[0] and "updated_at" in event_index[0]
        )
        result["ready"] = result["ready"] and result["event_index_covering"]
        raw_row = old.execute(
            "SELECT revision FROM dataset_state WHERE dataset='stock_raw'"
        ).fetchone()
        raw_revision = raw_row[0] if raw_row else None
        states = new.execute(
            "SELECT dataset,status,raw_revision,factor_revision FROM feature_state "
            "WHERE scope_key='all'"
        ).fetchall()
        expected_datasets = {"stock_daily_features", "market_regime_features"}
        actual_datasets = {row[0] for row in states}
        all_ready = all(row[1] == "READY" for row in states)
        raw_match = all(row[2] == raw_revision for row in states)
        factor_row = None
        with sqlite3.connect(root / "adjustments.sqlite") as adj:
            factor_row = adj.execute(
                "SELECT revision FROM adjustment_state WHERE dataset='stock_adjustment_factors'"
            ).fetchone()
        factor_revision = factor_row[0] if factor_row else None
        factor_match = all(row[3] == factor_revision for row in states)
        result["feature_state"] = {
            "datasets": sorted(actual_datasets),
            "all_ready": all_ready,
            "raw_revision_match": raw_match,
            "factor_revision_match": factor_match,
        }
        result["ready"] = (
            result["ready"]
            and actual_datasets == expected_datasets
            and all_ready
            and raw_match
            and factor_match
        )
    with (
        sqlite3.connect(root / "stocks.sqlite") as stocks,
        sqlite3.connect(root / "etfs.sqlite") as etfs,
        sqlite3.connect(root / "adjustments.sqlite") as adjustments,
    ):
        if adjustments.execute("PRAGMA user_version").fetchone()[0] != 1:
            result["ready"] = False
        factor_pairs = (
            (
                "stock_adjustment_factors",
                stocks.execute(
                    "SELECT symbol,effective_date,event_factor,cumulative_factor,valid_from,"
                    "valid_through,factor_basis,source,updated_at FROM corporate_actions "
                    "WHERE record_kind='factor'"
                ).fetchall(),
                adjustments.execute(
                    "SELECT symbol,effective_date,event_factor,cumulative_factor,valid_from,"
                    "valid_through,factor_basis,source,updated_at "
                    "FROM stock_adjustment_factors"
                ).fetchall(),
            ),
            (
                "stock_factor_anchors",
                stocks.execute(
                    "SELECT symbol,effective_date,source,source_key,source_cumulative_factor,"
                    "payload_json,updated_at FROM corporate_actions "
                    "WHERE record_kind='factor_anchor'"
                ).fetchall(),
                adjustments.execute(
                    "SELECT symbol,effective_date,source,source_key,source_cumulative_factor,"
                    "payload_json,updated_at FROM stock_factor_anchors"
                ).fetchall(),
            ),
            (
                "etf_adjustment_factors",
                etfs.execute(
                    "SELECT symbol,trade_date,cumulative_factor,source FROM adjustment_factors"
                ).fetchall(),
                adjustments.execute(
                    "SELECT symbol,effective_date,cumulative_factor,source "
                    "FROM etf_adjustment_factors"
                ).fetchall(),
            ),
        )
        for table, expected_rows, actual_rows in factor_pairs:
            content_equal = set(expected_rows) == set(actual_rows)
            result["counts"][table] = {
                "source": len(expected_rows),
                "target": len(actual_rows),
                "content_equal": content_equal,
            }
            result["ready"] = (
                result["ready"] and len(expected_rows) == len(actual_rows) and content_equal
            )
    with duckdb.connect(str(root / "catalog.duckdb"), read_only=True) as catalog:
        row = catalog.execute(
            "SELECT value FROM pool_metadata WHERE key='layout_version'"
        ).fetchone()
        result["layout_version"] = int(row[0]) if row else 1
    result["activated"] = result["layout_version"] >= 2
    return result


def platform_status(root):
    root = Path(root).expanduser().resolve()
    files = {}
    for name in (
        "catalog.duckdb",
        "stocks.sqlite",
        "indices.sqlite",
        "etfs.sqlite",
        "fundamentals.sqlite",
        "adjustments.sqlite",
        "features.sqlite",
        "snapshots.sqlite",
    ):
        path = root / name
        files[name] = {
            "exists": path.is_file(),
            "bytes": path.stat().st_size if path.exists() else 0,
        }
    return {"root": str(root), "files": files, **verify_platform_v2(root)}
