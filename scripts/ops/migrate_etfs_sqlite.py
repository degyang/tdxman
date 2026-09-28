#!/usr/bin/env python3
"""One-time verified ETF migration, with bounded batches and atomic installation."""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

import pyarrow.parquet as pq

from aspool.pool import pool_lock
from aspool.sqlite_etf_store import DDL, FIELDS, normalize


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def migrate(root, *, workdir):
    root, workdir = Path(root).resolve(), Path(workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=False)
    manifest = dict(status="building", rows=0, files=[], started_at=time.time())
    report = workdir / "migration.json"
    target = root / "etfs.sqlite"
    try:
        with (
            pool_lock(root, write=True),
            tempfile.TemporaryDirectory(prefix="aspool-etf-") as scratch,
        ):
            if target.exists():
                raise FileExistsError("etfs.sqlite exists; refusing replacement")
            paths = sorted((root / "lake/bars/daily").glob("market=*/symbol=*/bars.parquet"))
            if not paths:
                raise ValueError("No legacy ETF files")
            if (root / "catalog.duckdb.wal").exists():
                raise ValueError("Close catalog writer before migration")
            shutil.copy2(root / "catalog.duckdb", workdir / "catalog.duckdb.before")
            # Build random-write-heavy indices on local scratch, then copy a closed image.
            stage = Path(scratch) / "etfs.sqlite"
            conn = sqlite3.connect(stage)
            stamp = time.time_ns() // 1000
            cols = ",".join(f'"{k}"' for k in FIELDS)
            try:
                conn.executescript(DDL)
                conn.execute("DROP INDEX daily_bars_by_date")
                conn.execute("PRAGMA cache_size=-32768")
                for number, path in enumerate(paths, 1):
                    sha = digest(path)
                    item = dict(
                        market=path.parent.parent.name.split("=", 1)[1],
                        code=path.parent.name.split("=", 1)[1],
                    )
                    symbol = f"{item['code']}.{item['market']}"
                    count, last = 0, None
                    conn.execute("BEGIN")
                    with pq.ParquetFile(path) as parquet:
                        for batch in parquet.iter_batches(batch_size=4096):
                            payload = []
                            for raw in batch.to_pylist():
                                if raw.get("asset_type") != "etf":
                                    raise ValueError(f"Non-ETF legacy file: {path}")
                                row = normalize(raw, item)
                                if last and row["trade_date"] <= last:
                                    raise ValueError("Unsorted/duplicate ETF history")
                                last = row["trade_date"]
                                payload.append(tuple(row.get(k) for k in FIELDS))
                            conn.executemany(
                                f"INSERT INTO daily_bars({cols},source,updated_at) VALUES ("
                                + ",".join("?" for _ in range(len(FIELDS) + 2))
                                + ")",
                                [(*p, "migration:parquet", stamp) for p in payload],
                            )
                            days = [p[FIELDS.index("trade_date")] for p in payload]
                            actual = conn.execute(
                                f"SELECT {cols} FROM daily_bars WHERE symbol=? "
                                "AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
                                (symbol, days[0], days[-1]),
                            ).fetchall()
                            if actual != payload:
                                raise ValueError("ETF migration value mismatch")
                            count += len(payload)
                    conn.commit()
                    if digest(path) != sha or not count:
                        raise ValueError("Legacy source changed or empty")
                    manifest["files"].append(
                        dict(
                            path=str(path.relative_to(root)),
                            symbol=symbol,
                            sha256=sha,
                            rows=count,
                            end=last,
                        )
                    )
                    manifest["rows"] += count
                    if number % 100 == 0:
                        print(json.dumps(dict(files=number, rows=manifest["rows"])), flush=True)
                factor_path = root / "lake/adjustments/factors.parquet"
                factors = []
                if factor_path.exists():
                    with pq.ParquetFile(factor_path) as source:
                        for r in source.read().to_pylist():
                            if (r["market"] == "SH" and r["symbol"].startswith("5")) or (
                                r["market"] == "SZ" and r["symbol"].startswith(("15", "16", "18"))
                            ):
                                factors.append(
                                    (
                                        f"{r['symbol']}.{r['market']}",
                                        str(r["trade_date"]),
                                        r["cumulative_factor"],
                                        "legacy:free-stockdb",
                                    )
                                )
                    conn.executemany("INSERT INTO adjustment_factors VALUES (?,?,?,?)", factors)
                    conn.commit()
                if conn.execute(
                    "SELECT * FROM adjustment_factors ORDER BY symbol,trade_date"
                ).fetchall() != sorted(factors):
                    raise ValueError("ETF factor migration mismatch")
                manifest["factor_rows"] = len(factors)
                conn.execute("CREATE INDEX daily_bars_by_date ON daily_bars(trade_date,symbol)")
                conn.commit()
                if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise ValueError("ETF integrity check failed")
                if (
                    conn.execute("SELECT count(*) FROM daily_bars").fetchone()[0]
                    != manifest["rows"]
                ):
                    raise ValueError("ETF row count mismatch")
            finally:
                conn.close()
            installed_stage = workdir / "etfs.sqlite"
            shutil.copyfile(stage, installed_stage)
            sha = digest(stage)
            if digest(installed_stage) != sha:
                raise ValueError("Staged database copy hash mismatch")
            with installed_stage.open("rb") as stream:
                os.fsync(stream.fileno())
            manifest.update(
                status="verified", database_sha256=sha, bytes=installed_stage.stat().st_size
            )
            report.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            installed_stage.replace(target)
            fd = os.open(root, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            (root / "lake/bars/daily").replace(workdir / "legacy-etfs")
            manifest.update(status="installed", finished_at=time.time())
    except BaseException as exc:
        manifest.update(status="failed", error=str(exc))
        raise
    finally:
        report.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    args = parser.parse_args()
    result = migrate(args.root, workdir=args.workdir)
    print(
        json.dumps({k: v for k, v in result.items() if k != "files"}, ensure_ascii=False),
        flush=True,
    )
