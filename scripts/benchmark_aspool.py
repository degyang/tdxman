"""Compare equal sync/async workloads on temporary copies; never modify pool data."""

import argparse
import json
import shutil
import subprocess
import tempfile
import time
import types
from pathlib import Path
from unittest.mock import patch

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from aspool import DataPool
from aspool.fundamentals import update_from_quotes
from aspool.index_lists import load_indices
from aspool.index_pool import index_path, sync_indices
from aspool.pool import pool_lock
from aspool.store import bars_path, catalog, initialize
from tdxman.client import AsyncTdxClient, TdxClient
from tdxman.mac.client import AsyncMacClient, MacClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.home() / ".aspool")
    parser.add_argument("--stocks", type=int, default=200)
    parser.add_argument("--indices", type=int, default=40)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--legacy-ref", help="Optional git revision for a baseline quote implementation"
    )
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    tick = time.perf_counter()
    mac_host = MacClient.from_best_host()._host
    index_host = TdxClient.from_best_host()._host
    preparation = time.perf_counter() - tick
    with duckdb.connect(str(root / "catalog.duckdb"), read_only=True) as conn:
        rows = conn.execute(
            "SELECT * FROM coverage WHERE end_date = (SELECT max(end_date) FROM coverage) "
            "ORDER BY symbol LIMIT ?",
            [args.stocks],
        ).fetchall()
    items = load_indices()[: args.indices]
    day = max(r[3] for r in rows)
    day = day.date() if hasattr(day, "date") else day
    legacy = None
    if args.legacy_ref:
        source = subprocess.run(
            ["git", "show", f"{args.legacy_ref}:src/aspool/fundamentals.py"],
            check=True,
            text=True,
            capture_output=True,
        ).stdout
        legacy = types.ModuleType("aspool._benchmark_legacy")
        legacy.__package__ = "aspool"
        exec(compile(source, "legacy_fundamentals.py", "exec"), legacy.__dict__)
    results = {
        "stocks": len(rows),
        "indices": len(items),
        "target_date": str(day),
        "mac_host": mac_host,
        "index_host": index_host,
        "server_selection_seconds": preparation,
        "scope": "Independent temporary copies, quote update then index incremental sync",
        "cases": [],
    }
    reference = {}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="aspool-benchmark-") as base:
        base = Path(base)
        seed = base / "seed"
        initialize(seed)
        with pool_lock(root):
            with catalog(seed) as conn:
                conn.executemany("INSERT INTO coverage VALUES (?,?,?,?,?,?,?)", rows)
            for symbol, market, *_ in rows:
                src = bars_path(root, "daily", market, symbol)
                dst = bars_path(seed, "daily", market, symbol)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
            snapshot = root / "lake/fundamentals/snapshots.parquet"
            shutil.copyfile(snapshot, seed / "lake/fundamentals/snapshots.parquet")
            for item in items:
                dst = index_path(seed, item)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(index_path(root, item), dst)
        results["seed_copied"] = True
        run_cases(
            base, seed, rows, items, day, args, legacy, mac_host, index_host, results, reference
        )


def run_cases(base, seed, rows, items, day, args, legacy, mac_host, index_host, results, reference):
    with (
        patch.object(
            MacClient,
            "from_best_host",
            side_effect=lambda **_: MacClient(mac_host, heartbeat_interval=0),
        ),
        patch.object(
            AsyncMacClient,
            "from_best_host",
            side_effect=lambda **_: AsyncMacClient(mac_host, heartbeat_interval=0),
        ),
        patch.object(
            TdxClient,
            "from_best_host",
            side_effect=lambda **_: TdxClient(index_host, heartbeat_interval=0),
        ),
        patch.object(
            AsyncTdxClient,
            "from_best_host",
            side_effect=lambda **_: AsyncTdxClient(index_host, heartbeat_interval=0),
        ),
    ):
        modes = [
            ("sync-1", False, 1),
            ("async-1", True, 1),
            ("sync-4", False, 4),
            ("async-4", True, 4),
        ]
        for scenario in ["unchanged", "changed"]:
            runs = [("legacy", False, 1)] if legacy else []
            runs += modes * args.repeats
            for iteration, (label, asynchronous, workers) in enumerate(runs):
                dest = base / "case"
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(seed, dest)
                if scenario == "changed":
                    paths = [bars_path(dest, "daily", r[1], r[0]) for r in rows] + [
                        index_path(dest, i) for i in items
                    ]
                    for path in paths:
                        table = pq.ParquetFile(path).read()
                        column = table.column_names.index("name")
                        names = table["name"].to_pylist()
                        names[-1] = "benchmark pending refresh"
                        pq.write_table(
                            table.set_column(column, "name", pa.array(names, pa.string())), path
                        )
                started = time.perf_counter()
                if label == "legacy":
                    result = legacy.update_from_quotes.__wrapped__(dest)
                    quote_report = {}
                else:
                    result = update_from_quotes(dest, async_mode=asynchronous, workers=workers)
                    quote_report = json.loads(
                        (dest / "reports/maintenance/latest.json").read_text()
                    )
                quote_seconds = time.perf_counter() - started
                quote = DataPool(dest).read_daily(start=str(day), end=str(day))
                if "stock" not in reference:
                    reference["stock"] = quote
                pd.testing.assert_frame_equal(
                    reference["stock"], quote, check_dtype=False, check_exact=True
                )
                record = {
                    "scenario": scenario,
                    "mode": label,
                    "iteration": iteration,
                    "quote_seconds": quote_seconds,
                    "quote_result": result,
                    "quote_write_seconds": quote_report.get("write_seconds"),
                    "quote_total_seconds": quote_report.get("total_seconds"),
                }
                if label != "legacy":
                    started = time.perf_counter()
                    report, _ = sync_indices(
                        dest, items=items, asynchronous=asynchronous, workers=workers
                    )
                    record["index_seconds"] = time.perf_counter() - started
                    assert not report["failed"], report["failed"]
                    index = DataPool(dest).read_index_daily(start=str(day), end=str(day))
                    if "index" not in reference:
                        reference["index"] = index
                    pd.testing.assert_frame_equal(
                        reference["index"], index, check_dtype=False, check_exact=True
                    )
                    record["index_write_seconds"] = report["write_seconds"]
                    record["index_changed"] = sum(r["changed"] for r in report["success"])
                    record["total_seconds"] = quote_seconds + record["index_seconds"]
                record["equal_results"] = True
                results["cases"].append(record)
                args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
                print(json.dumps(record, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
