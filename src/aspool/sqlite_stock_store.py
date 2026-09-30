"""Explicit stock connections and canonical publication snapshots."""

from __future__ import annotations

import sqlite3
from contextlib import ExitStack, contextmanager
from importlib.resources import files
from pathlib import Path


@contextmanager
def stock_connection(root: Path, *, create: bool = False, read_only: bool = True):
    from .platform_v2 import layout_version
    from .pool import pool_lock
    from .sqlite_publication import assert_published

    root = Path(root).expanduser().resolve()
    path = root / "stocks.sqlite"
    assert_published(root)
    if create:
        if read_only:
            raise ValueError("Cannot create a read-only database")
        root.mkdir(parents=True, exist_ok=True)
        # Reserve exclusively, so creation cannot overwrite an existing database.
        with path.open("xb"):
            pass
    version = layout_version(root) if not create else 1
    factory = sqlite3.Connection
    if version == 3:
        from .sqlite_canonical import CanonicalConnection

        factory = CanonicalConnection
    with ExitStack() as contexts:
        if read_only and version == 3:
            contexts.enter_context(pool_lock(root))
            assert_published(root)
        conn = sqlite3.connect(
            path.as_uri() + ("?mode=ro" if read_only else "?mode=rw"),
            uri=True,
            timeout=30,
            factory=factory,
        )
        try:

            def execute(sql):
                return sqlite3.Connection.execute(conn, sql)

            execute("PRAGMA busy_timeout=30000")
            execute("PRAGMA cache_size=-32768")
            if version == 3:
                conn.configure(root, read_only=read_only)
            if read_only:
                execute("PRAGMA query_only=ON")
                if version == 3:
                    from .storage_verify import assert_coherent

                    execute("BEGIN")
                    assert_coherent(conn.native)
                    # Keep pinned WAL snapshots; allow later writers to proceed.
                    contexts.close()
            else:
                mode = execute("PRAGMA journal_mode=WAL").fetchone()[0]
                if mode.lower() != "wal":
                    raise RuntimeError(f"WAL unavailable: {mode}")
                execute("PRAGMA synchronous=FULL")
            if create:
                conn.executescript(files("aspool").joinpath("stocks_schema.sql").read_text())
            if not read_only and version != 3:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS dataset_state("
                    "dataset TEXT PRIMARY KEY,revision INTEGER NOT NULL,max_date TEXT,"
                    "updated_at INTEGER NOT NULL) STRICT, WITHOUT ROWID"
                )
            if execute("PRAGMA user_version").fetchone()[0] != (3 if version == 3 else 1):
                raise ValueError("Unsupported stock schema version")
            yield conn
        finally:
            conn.close()
