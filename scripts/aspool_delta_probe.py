"""Measure eight single-row corrections and no-op replays in an isolated pool.

The source is a fixed recovery snapshot. No network, production writes, or
limit-event recomputation occurs. File byte counts are proxies, not device I/O.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import shutil
import subprocess
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import pyarrow.parquet as pq

from aspool import DataPool
from aspool.change_observation import empty_cost
from aspool.daily_storage import merge_daily
from aspool.pool import pool_lock
from aspool.store import daily_path, initialize

SYMBOLS = (
    "000001.SZ",
    "000002.SZ",
    "000333.SZ",
    "000651.SZ",
    "600000.SH",
    "600036.SH",
    "600519.SH",
    "601318.SH",
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    snapshot, root, output = (
        p.expanduser().resolve() for p in (args.snapshot, args.destination, args.output)
    )
    if root == snapshot or root in snapshot.parents or snapshot in root.parents:
        parser.error("Destination must be outside snapshot and must not exist")
    if output == snapshot or snapshot in output.parents:
        parser.error("Evidence must be outside snapshot")
    root.mkdir(parents=True, exist_ok=False)
    initialize(root)
    copies = []
    with pool_lock(snapshot):
        for symbol in SYMBOLS:
            code, market = symbol.split(".")
            source = daily_path(snapshot, market, code)
            target = daily_path(root, market, code)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copies.append((symbol, source, target, sha(source)))
    results = []
    with pool_lock(root, write=True):
        for symbol, source, path, fingerprint in copies:
            code, market = symbol.split(".")
            before = pq.ParquetFile(path).read()
            row = before.slice(len(before) - 1).to_pylist()[0]
            row["amount"] = float(row["amount"]) + 1.0
            changes, correction = [], empty_cost()
            changed = merge_daily(
                root,
                market,
                code,
                [row],
                "isolated:delta-probe",
                changes=changes,
                metrics=correction,
            )
            after = pq.ParquetFile(path).read()
            assert before.num_rows == after.num_rows
            for field in before.column_names:
                if field != "amount":
                    assert before[field].equals(after[field]), (symbol, field)
            assert changed == 1 and changes[0]["fields"] == ["amount"]
            stable_file = sha(path), path.stat().st_mtime_ns
            replay, replay_changes = empty_cost(), []
            assert (
                merge_daily(
                    root,
                    market,
                    code,
                    [row],
                    "isolated:delta-probe",
                    changes=replay_changes,
                    metrics=replay,
                )
                == 0
            )
            assert replay_changes == []
            assert (sha(path), path.stat().st_mtime_ns) == stable_file
            assert sha(source) == fingerprint
            results.append(
                dict(
                    symbol=symbol,
                    correction=correction,
                    replay=replay,
                    source_sha256=fingerprint,
                    changes=changes,
                )
            )
    public = DataPool(root).read_research_daily(
        symbols=list(SYMBOLS),
        start=str(row["trade_date"]),
        end=str(row["trade_date"]),
        fields=["symbol", "date", "amount"],
    )
    assert len(public) == len(SYMBOLS)
    source_files = ["daily_storage.py", "change_observation.py", "tdx_online.py"]
    result = dict(
        observed_at=datetime.now(timezone.utc).isoformat(),
        snapshot=str(snapshot),
        root=str(root),
        base_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        source_hashes={name: sha(Path("src/aspool") / name) for name in source_files},
        dependencies={name: version(name) for name in ("duckdb", "pyarrow", "pandas")},
        peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
        public_rows=len(public),
        results=results,
        result="passed",
        limitations=[
            "Eight selected securities; not a five-year RSS or market-wide benchmark",
            "No limit publications in the lab; invalidation covered by unit tests",
            "No cold-cache, lock-contention or device-I/O measurement",
        ],
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "result": result["result"],
                "output": str(output),
                "peak_rss_bytes": result["peak_rss_bytes"],
            }
        )
    )


if __name__ == "__main__":
    main()
