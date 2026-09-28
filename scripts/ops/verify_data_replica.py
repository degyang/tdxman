"""Verify a closed complete replica against its file manifest and derived evidence.

Standard library only. No source data writes, full integrity scan, or row counts.
The caller supplies trusted source acceptance evidence, not a newly inferred one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
import time
from contextlib import closing
from pathlib import Path, PureWindowsPath

BUSINESS_TABLES = {
    "daily_bars", "daily_features", "corporate_actions", "market_daily_summary"
}
TABLES = BUSINESS_TABLES | {"dataset_state"}
REQUIRED_FILES = {"stocks.sqlite", "indices.sqlite", "etfs.sqlite", "catalog.duckdb"}
SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
SIDECARS = {f"{name}.sqlite-{suffix}" for name in ("stocks", "indices", "etfs")
            for suffix in ("wal", "shm")}
SAMPLES = {
    "daily_bars": (
        "SELECT symbol,trade_date,open,high,low,close,amount "
        "FROM daily_bars ORDER BY symbol,trade_date LIMIT 5"
    ),
    "daily_features": (
        "SELECT symbol,trade_date,pre_close,is_st,calc_status,limit_status,ma20 "
        "FROM daily_features ORDER BY symbol,trade_date LIMIT 5"
    ),
    "market_daily_summary": (
        "SELECT frequency,period_key,scope,as_of,session_count,trading_count "
        "FROM market_daily_summary WHERE frequency='D' "
        "ORDER BY frequency,period_key,scope LIMIT 5"
    ),
}


def _signature(info):
    # Access time can change from reading. Identity, content times and mode cannot.
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _absolute_no_symlinks(path):
    path = Path(os.path.abspath(path))
    for component in (*reversed(path.parents), path):
        if component.is_symlink():
            raise ValueError(f"Symlink is not allowed: {component}")
    return path


def _relative_path(value):
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("Manifest path must be a canonical relative POSIX path")
    if (
        Path(value).is_absolute()
        or PureWindowsPath(value).drive
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError(f"Unsafe manifest path: {value!r}")
    return value


def _integer(value, label):
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return value


def _sha256(value, label):
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a SHA-256 hex digest")
    return value.lower()


def _safe_file(root, relative):
    path = root / relative
    for component in (*reversed(path.relative_to(root).parents), Path(relative)):
        candidate = root / component
        if candidate.is_symlink():
            raise ValueError(f"Symlink is not allowed: {candidate}")
    if not path.resolve().is_relative_to(root):
        raise ValueError(f"File outside data root: {path}")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"Expected regular file: {path}")
    return path


def _stream_file(path, *, parse_json=False):
    """Pin an opened regular file and compare descriptor/path stats on both sides."""
    path = _absolute_no_symlinks(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"Expected regular file: {path}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        if _signature(os.fstat(stream.fileno())) != _signature(before):
            raise ValueError(f"File changed before reading: {path}")
        if parse_json:
            result = json.load(stream)
        else:
            digest = hashlib.sha256()
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
            result = digest.hexdigest()
        after = os.fstat(stream.fileno())
    _absolute_no_symlinks(path)
    if _signature(after) != _signature(before) or _signature(path.lstat()) != _signature(before):
        raise ValueError(f"File changed while reading: {path}")
    return result, _signature(before)


def _inventory(root):
    files = {}

    def walk_error(error):
        raise error

    for parent, directories, names in os.walk(root, followlinks=False, onerror=walk_error):
        for name in directories:
            candidate = Path(parent) / name
            if candidate.is_symlink() or not candidate.is_dir():
                raise ValueError(f"Symlink or non-directory in data root: {candidate}")
        for name in names:
            path = Path(parent) / name
            relative = path.relative_to(root).as_posix()
            _safe_file(root, relative)
            files[relative] = _signature(path.lstat())
    if any(files.get(name, (0, 0, 0, 0))[3] for name in SIDECARS if name.endswith("-wal")):
        raise ValueError("Nonempty SQLite WAL: checkpoint and close the source writer first")
    return files


def _manifest(document):
    if (
        not isinstance(document, dict)
        or type(document.get("format_version")) is not int
        or document["format_version"] != 1
        or not isinstance(document.get("files"), list)
    ):
        raise ValueError("Unsupported or malformed replica manifest")
    entries = {}
    for item in document["files"]:
        if not isinstance(item, dict):
            raise ValueError("Manifest file entry must be an object")
        relative = _relative_path(item.get("path"))
        if relative in entries:
            raise ValueError(f"Duplicate manifest path: {relative}")
        if relative in SIDECARS:
            raise ValueError("SQLite sidecars cannot be authoritative manifest files")
        entries[relative] = {
            "bytes": _integer(item.get("bytes"), f"Size of {relative}"),
            "sha256": _sha256(item.get("sha256"), f"Digest of {relative}"),
        }
    total = _integer(document.get("bytes"), "Manifest total bytes")
    if sum(item["bytes"] for item in entries.values()) != total:
        raise ValueError("Manifest total bytes differ from its file sizes")
    if not REQUIRED_FILES <= entries.keys():
        raise ValueError(
            "Manifest must contain stocks.sqlite, indices.sqlite, etfs.sqlite and catalog.duckdb"
        )
    return entries, total


def _evidence(document):
    if (
        not isinstance(document, dict)
        or document.get("integrity_check") != "ok"
        or document.get("daily_calculation_complete") is not True
    ):
        raise ValueError("Missing successful complete derived integrity evidence")
    _sha256(document.get("database_sha256"), "Evidence database digest")
    _integer(document.get("database_bytes"), "Evidence database bytes")
    rows = document.get("rows")
    if not isinstance(rows, dict) or set(rows) != BUSINESS_TABLES:
        raise ValueError("Evidence rows must cover the four stock tables")
    for table, count in rows.items():
        _integer(count, f"Evidence rows.{table}")
        if table != "corporate_actions" and count == 0:
            raise ValueError(f"Complete derived evidence has no rows for {table}")
    return document


def _sqlite_samples(path):
    signature = _signature(path.lstat())
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA cache_size=-16384")
        conn.execute("PRAGMA mmap_size=0")
        if conn.execute("PRAGMA user_version").fetchone() != (1,):
            raise ValueError("Unsupported stocks.sqlite user_version")
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if tables != TABLES:
            raise ValueError("Stocks schema has missing or extra tables")
        # Validate required read columns even when some tables have no rows.
        required = {
            "corporate_actions": {
                "symbol",
                "effective_date",
                "record_kind",
                "source",
                "source_key",
                "cumulative_factor",
                "valid_through",
            }
        }
        for table, sql in SAMPLES.items():
            required[table] = set(sql.split("SELECT ", 1)[1].split(" FROM", 1)[0].split(","))
        for table, columns in required.items():
            actual = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if not columns <= actual:
                raise ValueError(f"Stocks schema missing columns in {table}")
        samples = {}
        for table, sql in SAMPLES.items():
            cursor = conn.execute(sql)
            names = [column[0] for column in cursor.description]
            samples[table] = [dict(zip(names, row)) for row in cursor.fetchall()]
            if not samples[table]:
                raise ValueError(f"Missing local sample rows in {table}")
    _absolute_no_symlinks(path)
    if _signature(path.lstat()) != signature:
        raise ValueError("Stocks database changed during sample reads")
    return samples


def verify(root, manifest_path, integrity_evidence_path):
    """Verify a replica read-only; raise on every failure and never count tables."""
    started = time.monotonic()
    root = _absolute_no_symlinks(root)
    if not root.is_dir():
        raise ValueError("Replica root must be an existing directory")
    root_identity = (root.stat().st_dev, root.stat().st_ino)
    manifest_path = _absolute_no_symlinks(manifest_path)
    integrity_evidence_path = _absolute_no_symlinks(integrity_evidence_path)
    manifest, manifest_signature = _stream_file(manifest_path, parse_json=True)
    evidence, evidence_signature = _stream_file(integrity_evidence_path, parse_json=True)
    entries, total = _manifest(manifest)
    evidence = _evidence(evidence)

    def business_files(inventory):
        return set(inventory) - SIDECARS

    before = _inventory(root)
    missing = entries.keys() - before.keys()
    extra = business_files(before) - entries.keys()
    if missing or extra:
        raise ValueError(
            f"Replica missing/extra business files: missing={sorted(missing)}, "
            f"extra={sorted(extra)}"
        )
    verified_files = []
    for relative, expected in sorted(entries.items()):
        path = _safe_file(root, relative)
        if before[relative][3] != expected["bytes"]:
            raise ValueError(f"File size differs from manifest: {relative}")
        actual_hash, signature = _stream_file(path)
        if signature != before[relative]:
            raise ValueError(f"File changed since inventory: {relative}")
        if actual_hash != expected["sha256"]:
            raise ValueError(f"File SHA-256 differs from manifest: {relative}")
        verified_files.append(dict(path=relative, bytes=expected["bytes"], sha256=actual_hash))
    stock = entries["stocks.sqlite"]
    if (
        stock["bytes"] != evidence["database_bytes"]
        or stock["sha256"] != evidence["database_sha256"].lower()
    ):
        raise ValueError("Stock checksum/size differs from derived integrity evidence")
    samples = _sqlite_samples(_safe_file(root, "stocks.sqlite"))
    after = _inventory(root)
    if (
        business_files(after) != business_files(before)
        or any(
            after.get(name) != signature
            for name, signature in before.items()
            if name in entries or name in SIDECARS
        )
        or (after.keys() & SIDECARS) != (before.keys() & SIDECARS)
    ):
        raise ValueError("Replica files or SQLite sidecars changed during verification")
    for path, signature in (
        (manifest_path, manifest_signature),
        (integrity_evidence_path, evidence_signature),
    ):
        _absolute_no_symlinks(path)
        if _signature(path.lstat()) != signature:
            raise ValueError(f"Verification control file changed: {path}")
    _absolute_no_symlinks(root)
    if (root.stat().st_dev, root.stat().st_ino) != root_identity:
        raise ValueError("Replica root changed during verification")
    return {
        "status": "verified",
        "verification_scope": "complete_file_replica_and_stock_read_samples",
        "root": str(root),
        "files": len(entries),
        "bytes": total,
        "verified_files": verified_files,
        "manifest_path": str(manifest_path),
        "integrity_evidence_path": str(integrity_evidence_path),
        "database_sha256": stock["sha256"],
        "database_bytes": stock["bytes"],
        "integrity_check": "ok",
        "integrity_check_origin": "source_reused_after_sha256_match",
        "daily_calculation_complete": True,
        "rows": evidence["rows"],
        "rows_origin": "source_reused_after_sha256_match",
        "local_row_counts_run": False,
        "local_full_integrity_run": False,
        "sqlite_mode": "mode=ro&immutable=1",
        "sqlite_version": sqlite3.sqlite_version,
        "samples": samples,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def _report_target(path, root, controls):
    path = _absolute_no_symlinks(path)
    if path.suffix != ".json" or path in controls:
        raise ValueError("Report must be a separate .json file")
    if path.is_relative_to(root):
        raise ValueError("Report must be outside the data root")
    if path.exists() and not path.is_file():
        raise ValueError("Report target must be a regular file")
    if path.exists() and path.stat().st_nlink != 1:
        raise ValueError("Report cannot overwrite a hardlinked file")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--integrity-evidence", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    report = None
    try:
        root = _absolute_no_symlinks(args.root)
        controls = {
            _absolute_no_symlinks(args.manifest),
            _absolute_no_symlinks(args.integrity_evidence),
        }
        report = _report_target(args.report, root, controls)
        # Replace stale success before hashing; interrupted runs remain running.
        report.write_text(json.dumps({"status": "running"}) + "\n")
        result = verify(root, args.manifest, args.integrity_evidence)
        payload = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        report.write_text(payload)
        print(payload, end="")
    except (Exception, KeyboardInterrupt) as exc:
        failure = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        if report is not None:
            try:
                report.write_text(json.dumps(failure, ensure_ascii=False, indent=2) + "\n")
            except OSError as write_error:
                failure["report_write_error"] = str(write_error)
        print(json.dumps(failure, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
