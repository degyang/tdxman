"""Verify an existing recovery manifest against its snapshot and the current pool.

This deliberately hashes the full baseline once at the DG-00 checkpoint. It is
not a daily update or cache-invalidation mechanism and never copies pool data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from aspool import DataPool
from aspool.pool import pool_lock
from aspool.store import read_only_catalog


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def inventory(root):
    paths = [root / "catalog.duckdb", *sorted((root / "lake").rglob("*"))]
    if any(path.is_symlink() for path in paths):
        raise ValueError("Baseline verification does not follow symlinks")
    return {str(p.relative_to(root)): p for p in paths if p.is_file()}


def compare(root, expected):
    paths = inventory(root)
    changed = [
        name
        for name in sorted(paths.keys() & expected.keys())
        if paths[name].stat().st_size != expected[name]["bytes"]
        or digest(paths[name]) != expected[name]["sha256"]
    ]
    return dict(
        files=len(paths),
        bytes=sum(p.stat().st_size for p in paths.values()),
        added=sorted(paths.keys() - expected.keys()),
        missing=sorted(expected.keys() - paths.keys()),
        changed=changed,
    )


def verify(root, manifest_path):
    manifest = json.loads(manifest_path.read_text())
    snapshot = Path(manifest["snapshot"]).resolve()
    expected = {entry["path"]: entry for entry in manifest["files"]}
    if len(expected) != len(manifest["files"]):
        raise ValueError("Duplicate manifest paths")
    for name in expected:
        path = Path(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Manifest paths must stay within the pool")
    started = time.perf_counter()
    with pool_lock(snapshot):
        snapshot_check = compare(snapshot, expected)
    with pool_lock(root):
        if (root / "catalog.duckdb.wal").exists():
            raise RuntimeError("Pending WAL: finish the active writer before verification")
        current_check = compare(root, expected)
        with read_only_catalog(root) as conn:
            tables = [row[0] for row in conn.execute("show tables").fetchall()]
            counts = {
                name: conn.execute(
                    'select count(*) from "' + name.replace('"', '""') + '"'
                ).fetchone()[0]
                for name in tables
            }
    pool = DataPool(root)
    coverage = pool.read_limit_coverage()
    index = pool.read_index_daily(symbols="SH.000001", fields=["symbol", "date", "close"])
    return dict(
        observed_at=datetime.now(timezone.utc).isoformat(),
        root=str(root),
        manifest=str(manifest_path),
        manifest_sha256=digest(manifest_path),
        snapshot=str(snapshot),
        snapshot_check=snapshot_check,
        current_check=current_check,
        source_matches_baseline=all(
            not current_check[key] for key in ("added", "missing", "changed")
        ),
        snapshot_verified=all(not snapshot_check[key] for key in ("added", "missing", "changed")),
        catalog_rows=counts,
        coverage_days=len(coverage),
        stale_days=int(coverage.stale.sum()),
        coverage_start=str(coverage.trade_date.min()),
        coverage_end=str(coverage.trade_date.max()),
        index=dict(rows=len(index), start=str(index.date.min()), end=str(index.date.max())),
        elapsed_seconds=time.perf_counter() - started,
        dependencies={name: version(name) for name in ("duckdb", "pyarrow", "pandas")},
        scope="catalog.duckdb + lake/**; reports excluded, no pool mutation or copy",
        limitation="Public coverage/index probes follow the locked hash check; no PIT guarantee.",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root, manifest_path, output = (
        path.expanduser().resolve() for path in (args.root, args.manifest, args.output)
    )
    snapshot = Path(json.loads(manifest_path.read_text())["snapshot"]).resolve()
    if any(output == p or p in output.parents for p in (root, snapshot)):
        parser.error("Evidence must be outside the source and snapshot pools")
    result = verify(root, manifest_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    if not result["snapshot_verified"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
