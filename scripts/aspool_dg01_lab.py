"""Exercise DG-01 on twelve real bars from an already verified recovery snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

import pandas as pd
import pyarrow.parquet as pq

from aspool import DataPool
from aspool.daily_storage import merge_daily
from aspool.free_stockdb import _write_daily
from aspool.limit_events import RULE_VERSION, initialize_limits
from aspool.pool import pool_lock
from aspool.security_facts import initialize_facts
from aspool.store import catalog, daily_path, initialize


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(part)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--lab-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    source_root = Path(manifest["snapshot"]).resolve()
    root = args.lab_root.resolve()
    if (
        root == source_root
        or source_root in root.parents
        or root in source_root.parents
        or root == (Path.home() / ".aspool").resolve()
    ):
        parser.error("Laboratory must be independent from source/production")
    relative = "lake/bars/daily/market=SZ/symbol=000001/bars.parquet"
    source = source_root / relative
    expected = next(row for row in manifest["files"] if row["path"] == relative)
    checksum = digest(source)
    assert checksum == expected["sha256"]
    table = pq.ParquetFile(source).read()
    rows = table.slice(max(0, len(table) - 12)).to_pylist()
    root.mkdir(parents=True, exist_ok=False)
    initialize(root)
    initialize_facts(root)
    initialize_limits(root)
    with pool_lock(root, write=True):
        _write_daily(root, "SZ", "000001", rows)
    fields = ["symbol", "date", "open", "high", "low", "close", "volume", "amount"]
    lo, hi = rows[0]["trade_date"], rows[-1]["trade_date"]
    reference = DataPool(source_root).read_research_daily(
        symbols="000001.SZ", start=lo, end=hi, fields=fields
    )
    actual = DataPool(root).read_research_daily(symbols="000001.SZ", fields=fields)
    pd.testing.assert_frame_equal(reference, actual, check_exact=True)
    incoming = dict(rows[-1], amount=rows[-1]["amount"] + 1.0)
    started = perf_counter()
    with pool_lock(root, write=True):
        changed = merge_daily(root, "SZ", "000001", [incoming], "laboratory:verified_snapshot")
    first_seconds = perf_counter() - started
    target = daily_path(root, "SZ", "000001")
    before = target.read_bytes(), target.stat().st_mtime_ns
    with catalog(root) as conn:
        revisions = conn.execute("SELECT * FROM business_revisions ORDER BY 1").fetchall()
    started = perf_counter()
    with pool_lock(root, write=True):
        replay = merge_daily(root, "SZ", "000001", [incoming], "laboratory:verified_snapshot")
    replay_seconds = perf_counter() - started
    assert replay == 0 and (target.read_bytes(), target.stat().st_mtime_ns) == before
    with catalog(root) as conn:
        assert conn.execute("SELECT * FROM business_revisions ORDER BY 1").fetchall() == revisions
        runs = [
            json.loads(r[0])
            for r in conn.execute("SELECT metrics FROM maintenance_runs").fetchall()
        ]
        evidence = conn.execute(
            "SELECT evidence FROM business_changes ORDER BY applied_at"
        ).fetchall()
    assert digest(source) == checksum
    result = dict(
        result="passed",
        interpreter=sys.executable,
        code_base=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        code_files_sha256={str(p): digest(p) for p in sorted(Path("src/aspool").glob("*.py"))},
        laboratory_script_sha256=digest(Path(__file__)),
        source_diff_sha256=hashlib.sha256(
            subprocess.check_output(["git", "diff", "--", "src/aspool"])
        ).hexdigest(),
        dependencies={name: version(name) for name in ("duckdb", "pyarrow", "pandas", "pytest")},
        rule_version=RULE_VERSION,
        manifest=str(args.manifest),
        manifest_sha256=digest(args.manifest),
        source_root=str(source_root),
        source_file=relative,
        source_sha256=checksum,
        source_rows=len(table),
        sample_rows=len(rows),
        start=str(lo),
        end=str(hi),
        root=str(root),
        inputs="Last 12 unmodified raw bars; final amount +1 CNY correction",
        raw_public_reference_exact=True,
        changed_row_events=changed,
        replay_changed_rows=replay,
        first_seconds=first_seconds,
        replay_seconds=replay_seconds,
        maintenance_runs=runs,
        change_evidence=[r[0] for r in evidence],
        limitation=(
            "Single security sample, no full historical overlays/PIT or production recovery SLA"
        ),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in {"maintenance_runs", "change_evidence"}},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
