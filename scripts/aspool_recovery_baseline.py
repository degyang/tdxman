"""Create and restore a consistent pool baseline outside the production pool."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from aspool import DataPool
from aspool.pool import pool_lock
from aspool.store import read_only_catalog


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def frames(root):
    pool = DataPool(root)
    coverage = pool.read_limit_coverage()
    end = coverage.trade_date.max().date()
    return {
        "stocks": pool.read_research_daily(symbols=["000001.SZ", "600519.SH"], end=end, lookback=5),
        "index": pool.read_index_daily(symbols="SH.000001", end=end, lookback=5),
        "etf": pool.read_etf_daily(symbols="159915.SZ", end=end, lookback=5),
        "coverage": coverage,
        "summary": pool.read_limit_summary(),
        "events": pool.read_limit_events(trade_date=end),
        "security": pool.read_security_daily(symbols=["000001.SZ"], start=end, end=end),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    root, destination = args.root.resolve(), args.destination.resolve()
    if root == destination or root in destination.parents or destination in root.parents:
        parser.error("Recovery destination must be outside the source tree")
    destination.mkdir(parents=True, exist_ok=False)
    evidence = args.evidence
    evidence.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    snapshot, restored = destination / "snapshot", destination / "restored"
    entries = []
    with pool_lock(root, write=True):
        if (root / "catalog.duckdb.wal").exists():
            raise RuntimeError("Pending WAL: close the active writer before taking a baseline")
        paths = [root / "catalog.duckdb", *sorted((root / "lake").rglob("*"))]
        for path in paths:
            if path.is_symlink():
                raise RuntimeError(f"Snapshot does not follow symlinks: {path}")
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            target = snapshot / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            checksum = digest(path)
            if checksum != digest(target):
                raise RuntimeError(f"Snapshot checksum mismatch: {relative}")
            entries.append(dict(path=str(relative), bytes=path.stat().st_size, sha256=checksum))
    snapshot_seconds = time.perf_counter() - started
    print(f"Snapshot verified: {len(entries)} files in {snapshot_seconds:.2f}s", flush=True)
    tick = time.perf_counter()
    shutil.copytree(snapshot, restored)
    for entry in entries:
        if digest(restored / entry["path"]) != entry["sha256"]:
            raise RuntimeError(f"Restore checksum mismatch: {entry['path']}")
    reference, actual = frames(snapshot), frames(restored)
    for name in reference:
        pd.testing.assert_frame_equal(reference[name], actual[name], check_exact=True)
    restore_seconds = time.perf_counter() - tick
    # Exercise a localized restore in the disposable restored pool.
    relative = Path("lake/bars/daily/market=SZ/symbol=000001/bars.parquet")
    tick = time.perf_counter()
    (restored / relative).unlink()
    shutil.copy2(snapshot / relative, restored / relative)
    pd.testing.assert_frame_equal(reference["stocks"], frames(restored)["stocks"], check_exact=True)
    local_restore_seconds = time.perf_counter() - tick
    with read_only_catalog(snapshot) as conn:
        tables = [r[0] for r in conn.execute("show tables").fetchall()]
        counts = {
            name: conn.execute('select count(*) from "' + name.replace('"', '""') + '"').fetchone()[
                0
            ]
            for name in tables
        }
    coverage = reference["coverage"]
    result = dict(
        observed_at=datetime.now(timezone.utc).isoformat(),
        root=str(root),
        snapshot=str(snapshot),
        restored=str(restored),
        snapshot_seconds=snapshot_seconds,
        restore_and_verify_seconds=restore_seconds,
        local_restore_and_verify_seconds=local_restore_seconds,
        file_count=len(entries),
        bytes=sum(e["bytes"] for e in entries),
        coverage_days=len(coverage),
        stale_days=int(coverage.stale.sum()),
        coverage_start=str(coverage.trade_date.min()),
        coverage_end=str(coverage.trade_date.max()),
        catalog_rows=counts,
        verified_public_frames={k: len(v) for k, v in reference.items()},
        included=["catalog.duckdb", "lake/**"],
        excluded={
            "reports/**": "Retained in original pool; investigation evidence is not deleted."
        },
        recovery_scope=(
            "Exact baseline, single-host local copy; not protection against host/disk loss."
        ),
        result="passed",
        files=entries,
    )
    (destination / "manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    summary = {k: v for k, v in result.items() if k != "files"}
    summary["manifest"] = str(destination / "manifest.json")
    summary["manifest_sha256"] = digest(destination / "manifest.json")
    (evidence / "baseline.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
