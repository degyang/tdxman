"""Explicit SQLite stock connections; no implicit fallback or production routing."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path


@contextmanager
def stock_connection(root: Path, *, create: bool = False, read_only: bool = True):
    root = Path(root).expanduser().resolve()
    path = root / "stocks.sqlite"
    if create:
        if read_only:
            raise ValueError("Cannot create a read-only database")
        root.mkdir(parents=True, exist_ok=True)
        # Reserve exclusively, so creation cannot overwrite an existing database.
        with path.open("xb"):
            pass
    conn = sqlite3.connect(path.as_uri() + ("?mode=ro" if read_only else "?mode=rw"),
                           uri=True, timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA cache_size=-32768")
        if read_only:
            conn.execute("PRAGMA query_only=ON")
        else:
            mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if mode.lower() != "wal":
                raise RuntimeError(f"WAL unavailable: {mode}")
            conn.execute("PRAGMA synchronous=FULL")
        if create:
            conn.executescript(files("aspool").joinpath("stocks_schema.sql").read_text())
        if not read_only:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS dataset_state("
                "dataset TEXT PRIMARY KEY,revision INTEGER NOT NULL,max_date TEXT,"
                "updated_at INTEGER NOT NULL) STRICT, WITHOUT ROWID"
            )
        if conn.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("Unsupported stock schema version")
        yield conn
    finally:
        conn.close()
