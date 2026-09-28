#!/usr/bin/env python3
"""Explicit, verified index migration. Daily sync never invokes this full scan."""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path

import pyarrow.parquet as pq

from aspool.index_pool import normalize
from aspool.pool import pool_lock
from aspool.sqlite_index_store import DDL, FIELDS


def sha256(stream):
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def finish_archive(root, *, workdir):
    """Resume only an already verified installation whose archival failed."""
    root, workdir = Path(root).resolve(), Path(workdir).resolve()
    report = workdir / "migration.json"
    manifest = json.loads(report.read_text())
    with pool_lock(root, write=True):
        if manifest["root"] != str(root) or not manifest.get("database_sha256"):
            raise ValueError("Recovery manifest does not identify a verified database")
        for suffix in ("-wal", "-journal"):
            sidecar = root / ("indices.sqlite" + suffix)
            if sidecar.exists() and sidecar.stat().st_size:
                raise ValueError("Index database has active changes; cannot resume migration")
        with (root / "indices.sqlite").open("rb") as stream:
            if sha256(stream) != manifest["database_sha256"]:
                raise ValueError("Installed index database differs from verified migration")
        (root / "lake/indices").replace(workdir / "legacy-indices")
        manifest.update(status="installed", finished_at=time.time())
        manifest["archive_retry_error"] = manifest.pop("error", None)
        report.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def migrate(root, *, workdir):
    root, workdir = Path(root).resolve(), Path(workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=False)
    destination = root / "indices.sqlite"
    stage = workdir / "indices.sqlite"
    manifest = dict(
        status="building",
        root=str(root),
        files=[],
        rows=0,
        started_at=time.time(),
        migrated_at=time.time_ns() // 1000,
    )
    report = workdir / "migration.json"
    try:
        with pool_lock(root, write=True):
            if destination.exists():
                raise FileExistsError("indices.sqlite already exists; refusing replacement")
            paths = sorted((root / "lake/indices/daily").glob("market=*/symbol=*/bars.parquet"))
            if not paths:
                raise ValueError("No legacy index files")
            if (root / "catalog.duckdb.wal").exists():
                raise ValueError("Catalog WAL present; close catalog writers before migration")
            shutil.copy2(root / "catalog.duckdb", workdir / "catalog.duckdb.before")
            conn = sqlite3.connect(stage)
            try:
                conn.executescript(DDL)
                conn.execute("PRAGMA cache_size=-32768")
                placeholders = ",".join("?" for _ in FIELDS)
                for number, path in enumerate(paths, 1):
                    with path.open("rb") as stream:
                        digest = sha256(stream)
                    parquet = pq.ParquetFile(path)
                    identity = dict(
                        market=path.parent.parent.name.split("=", 1)[1],
                        code=path.parent.name.split("=", 1)[1],
                    )
                    symbol = identity["code"] + "." + identity["market"]
                    count, first, last = 0, None, None
                    conn.execute("BEGIN")
                    legacy_fields = [field for field in FIELDS if field != "breadth_status"]
                    for batch in parquet.iter_batches(batch_size=4096, columns=legacy_fields):
                        rows = batch.to_pylist()
                        payload = []
                        for row in rows:
                            if any(row[k] != v for k, v in identity.items()):
                                raise ValueError("Index identity differs from path")
                            normalized = normalize([row], {**identity, "name": row["name"]})[0]
                            if normalized != row:
                                raise ValueError("Migration would alter index values")
                            day = row["trade_date"].isoformat()
                            if last and day <= last:
                                raise ValueError("Index dates duplicated or unsorted")
                            first, last = first or day, day
                            row["trade_date"] = day
                            row["breadth_status"] = (
                                "AVAILABLE" if row["up_count"] + row["down_count"] > 0
                                else "UNAVAILABLE"
                            )
                            payload.append(
                                (
                                    symbol,
                                    *(row[f] for f in FIELDS),
                                    "migration:parquet",
                                    manifest["migrated_at"],
                                )
                            )
                        conn.executemany(
                            f"INSERT INTO daily_bars(symbol,{','.join(FIELDS)},source,updated_at) "
                            f"VALUES (?,{placeholders},?,?)",
                            payload,
                        )
                        # Compare every value while the bounded source batch is available.
                        actual = conn.execute(
                            f"SELECT {','.join(FIELDS)} FROM daily_bars WHERE symbol=? "
                            "AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
                            (symbol, payload[0][4], payload[-1][4]),
                        ).fetchall()
                        if actual != [p[1:-2] for p in payload]:
                            raise ValueError("Index migration row comparison failed")
                        count += len(payload)
                    # Windows cannot rename an ancestor while Arrow holds its file open.
                    parquet.close()
                    if not count:
                        raise ValueError("Empty legacy index")
                    conn.commit()
                    with path.open("rb") as stream:
                        if sha256(stream) != digest:
                            raise ValueError("Source changed during migration")
                    manifest["files"].append(
                        dict(
                            path=str(path.relative_to(root)),
                            symbol=symbol,
                            sha256=digest,
                            rows=count,
                            start=first,
                            end=last,
                        )
                    )
                    manifest["rows"] += count
                    if number % 50 == 0:
                        print(json.dumps(dict(files=number, rows=manifest["rows"])), flush=True)
                if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise ValueError("Index database integrity check failed")
                assert (
                    conn.execute("SELECT count(*) FROM daily_bars").fetchone()[0]
                    == manifest["rows"]
                )
                conn.execute("PRAGMA optimize")
            finally:
                conn.close()
            with stage.open("rb") as stream:
                os.fsync(stream.fileno())
                manifest["database_sha256"] = sha256(stream)
            manifest.update(status="verified_before_install", bytes=stage.stat().st_size)
            report.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            stage.replace(destination)
            fd = os.open(root, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            # Public readers now select SQLite. Preserve all old files outside runtime data.
            (root / "lake/indices").replace(workdir / "legacy-indices")
            manifest.update(
                status="installed",
                finished_at=time.time(),
                recovery="Stop writers; archive indices.sqlite (and checkpoint WAL), "
                "move legacy-indices back to data/lake/indices. "
                "The preserved legacy backend resumes when SQLite is absent.",
            )
    except BaseException as exc:
        manifest.update(status="failed", error=str(exc))
        raise
    finally:
        report.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--finish-archive", action="store_true")
    parser.add_argument(
        "--workdir",
        required=True,
        type=Path,
        help="New recovery directory on the same filesystem as data",
    )
    args = parser.parse_args()
    operation = finish_archive if args.finish_archive else migrate
    result = operation(args.root, workdir=args.workdir)
    print(json.dumps({k: v for k, v in result.items() if k != "files"}, ensure_ascii=False))
