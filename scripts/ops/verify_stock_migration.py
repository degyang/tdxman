"""Verify a closed facts-migration artifact, including after transfer to another host.

Uses only the Python standard library; this does not verify unfinished derived APIs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

TABLES = {"daily_bars", "daily_features", "corporate_actions", "market_daily_summary"}
INDEXES = {"daily_bars_by_date", "daily_features_by_date", "daily_features_events",
           "corporate_actions_selected_factor", "market_summary_by_range"}


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def verify(root, *, reuse_source_integrity=False):
    root = root.resolve()
    report = json.loads((root / "_reports/facts-result.json").read_text())
    path = root / "stocks.sqlite"
    wal = path.with_name("stocks.sqlite-wal")
    if wal.exists() and wal.stat().st_size:
        raise ValueError("Checkpoint and close the writer before verifying a transfer artifact")
    if path.stat().st_size != report["database_bytes"] or digest(path) != report["database_sha256"]:
        raise ValueError("Database checksum/size differs from the verified migration")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA cache_size=-32768")
        conn.execute("BEGIN")
        if conn.execute("PRAGMA user_version").fetchone() != (1,):
            raise ValueError("Unsupported database schema")
        tables = {name for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        indexes = {name for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        if tables != TABLES or not INDEXES <= indexes:
            raise ValueError("Missing/extra tables or missing indexes")
        if reuse_source_integrity:
            if report.get("integrity_check") != "ok":
                raise ValueError("Source integrity evidence is missing or unsuccessful")
            integrity_origin = "source_reused_after_sha256_match"
        else:
            if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("SQLite integrity check failed")
            integrity_origin = "local_full_check"
        counts = {table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                  for table in sorted(TABLES)}
        if counts != dict(report["rows"], market_daily_summary=0):
            raise ValueError("Counts differ from the facts-stage result")
        cursor = conn.execute("SELECT symbol,trade_date,close,amount FROM daily_bars "
                              "WHERE symbol=? ORDER BY trade_date DESC LIMIT 5", ["000001.SZ"])
        sample = [dict(zip([c[0] for c in cursor.description], row)) for row in cursor.fetchall()]
        return {"status": "verified", "stage": "source_facts_only", "counts": counts,
                "database_bytes": path.stat().st_size,
                "database_sha256": report["database_sha256"],
                "integrity_check": "ok", "integrity_check_origin": integrity_origin,
                "sqlite_version": sqlite3.sqlite_version, "sample": sample,
                "derived_ready": False, "production_ready": False}
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-root", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--reuse-source-integrity", action="store_true",
                        help="Reuse successful source integrity evidence only after exact "
                             "SHA-256/size match; still check local schema, counts and reads")
    args = parser.parse_args()
    result = verify(args.target_root, reuse_source_integrity=args.reuse_source_integrity)
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload)
    print(payload)


if __name__ == "__main__":
    main()
