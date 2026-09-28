#!/usr/bin/env python3
"""Verify migrated events/factors, move snapshots to catalog, archive the old lake."""

import argparse
import hashlib
import json
import shutil
import sqlite3
import time
from collections import Counter
from pathlib import Path

import duckdb
import pyarrow.parquet as pq

from aspool.pool import pool_lock


def migrate(root, *, workdir):
    root, workdir = Path(root).resolve(), Path(workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=False)
    result = dict(
        status="verifying",
        factors=0,
        event_files=0,
        events=0,
        event_revisions=[],
        files=[],
        started_at=time.time(),
    )
    try:
        with pool_lock(root, write=True):
            for name in ("stocks.sqlite", "indices.sqlite", "etfs.sqlite"):
                if not (root / name).is_file():
                    raise ValueError(f"Missing migrated database: {name}")
            if (root / "catalog.duckdb.wal").exists():
                raise ValueError("Close catalog writers before retirement")
            shutil.copy2(root / "catalog.duckdb", workdir / "catalog.duckdb.before")
            stock = sqlite3.connect((root / "stocks.sqlite").as_uri() + "?mode=ro", uri=True)
            etf = sqlite3.connect((root / "etfs.sqlite").as_uri() + "?mode=ro", uri=True)
            try:
                stock.execute("BEGIN")
                etf.execute("BEGIN")
                anchors = {
                    (r[0], r[1]): r[2]
                    for r in stock.execute(
                        "SELECT source_key,effective_date,source_cumulative_factor "
                        "FROM corporate_actions WHERE record_kind='factor_anchor' "
                        "AND source='legacy:adjustments/factors.parquet'"
                    )
                }
                fund = {
                    (r[0], r[1]): r[2]
                    for r in etf.execute(
                        "SELECT symbol,trade_date,cumulative_factor FROM adjustment_factors"
                    )
                }
                path = root / "lake/adjustments/factors.parquet"
                raw = path.read_bytes()
                result["files"].append(
                    dict(path=str(path.relative_to(root)), sha256=hashlib.sha256(raw).hexdigest())
                )
                with pq.ParquetFile(path) as parquet:
                    for batch in parquet.iter_batches(batch_size=4096):
                        for r in batch.to_pylist():
                            original = f"{r['symbol']}.{r['market']}"
                            day = str(r["trade_date"])
                            actual = anchors.get(
                                (original + ":cumulative", day), fund.get((original, day))
                            )
                            if actual != r["cumulative_factor"]:
                                raise ValueError(f"Unmigrated factor: {original} {day}")
                            result["factors"] += 1
                for path in sorted((root / "lake/fundamentals/dated_inputs").glob("*.json")):
                    raw = path.read_bytes()
                    envelope = json.loads(raw)
                    symbol = path.stem
                    events = {
                        (r[0], r[1]): json.loads(r[2])
                        for r in stock.execute(
                            "SELECT effective_date,source_key,payload_json FROM corporate_actions "
                            "WHERE symbol=? AND record_kind='event' AND source='tdx:xdxr'",
                            (symbol,),
                        )
                    }
                    slots = Counter()
                    for event in envelope["events"]:
                        day, category = str(event["date"])[:10], event["category"]
                        slots[(day, category)] += 1
                        key = (day, f"category={category}:slot={slots[(day, category)]}")
                        if key not in events:
                            raise ValueError(f"Unmigrated event: {symbol} {key}")
                        actual = events[key]
                        different = [k for k, v in event.items() if actual.get(k) != v]
                        if different:
                            # Updated sources can legitimately revise an old event. Preserve both.
                            result["event_revisions"].append(
                                dict(
                                    symbol=symbol,
                                    date=day,
                                    source_key=key[1],
                                    fields=different,
                                    before=event,
                                    after=actual,
                                )
                            )
                        result["events"] += 1
                    result["event_files"] += 1
                    result["files"].append(
                        dict(
                            path=str(path.relative_to(root)), sha256=hashlib.sha256(raw).hexdigest()
                        )
                    )
                    if result["event_files"] % 500 == 0:
                        print(
                            json.dumps(
                                {k: result[k] for k in ("event_files", "events", "factors")}
                            ),
                            flush=True,
                        )
            finally:
                stock.close()
                etf.close()
            path = root / "lake/fundamentals/snapshots.parquet"
            raw = path.read_bytes()
            result["files"].append(
                dict(path=str(path.relative_to(root)), sha256=hashlib.sha256(raw).hexdigest())
            )
            with duckdb.connect(str(root / "catalog.duckdb")) as catalog:
                catalog.execute("BEGIN")
                try:
                    catalog.execute(
                        "CREATE TABLE fundamental_snapshots AS SELECT * FROM read_parquet(?)",
                        [str(path)],
                    )
                    catalog.execute("ALTER TABLE fundamental_snapshots ADD PRIMARY KEY(symbol)")
                    actual = catalog.execute(
                        "SELECT * FROM fundamental_snapshots ORDER BY symbol"
                    ).fetchall()
                    wanted = catalog.execute(
                        "SELECT * FROM read_parquet(?) ORDER BY symbol", [str(path)]
                    ).fetchall()
                    if actual != wanted:
                        raise ValueError("Snapshot migration value mismatch")
                    result["snapshots"] = len(actual)
                    catalog.execute("COMMIT")
                except BaseException:
                    catalog.execute("ROLLBACK")
                    raise
                catalog.execute("CHECKPOINT")
            result["status"] = "verified_before_archive"
            (workdir / "retirement.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n"
            )
            for folder in ("adjustments", "fundamentals"):
                (root / "lake" / folder).replace(workdir / folder)
            # Remove empty scaffolding only; never delete an unexpected data file.
            for path in sorted(
                (root / "lake").rglob("*"), key=lambda p: len(p.parts), reverse=True
            ):
                if path.is_dir():
                    path.rmdir()
                else:
                    raise ValueError(f"Unexpected remaining lake file: {path}")
            (root / "lake").rmdir()
            result.update(status="retired", finished_at=time.time())
    except BaseException as exc:
        result.update(status="failed", error=str(exc))
        raise
    finally:
        (workdir / "retirement.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    args = parser.parse_args()
    result = migrate(args.root, workdir=args.workdir)
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in ("files", "event_revisions")},
            ensure_ascii=False,
        ),
        flush=True,
    )
