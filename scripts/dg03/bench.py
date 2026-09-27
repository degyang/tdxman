#!/usr/bin/env python3
"""DG-03 reproducible workloads. All mutation roots must be under --lab.

Run phases separately to serialize heavy work with other Orca workers. JSONL
samples are append-only and include source hashes, times, RSS and kernel I/O.
No implicit production DataPool root, network fetch, or production selection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import resource
import shutil
import subprocess
import sys
import threading
import time
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from aspool.dg03_candidate import CandidatePool, build, ident, literal
from aspool.dg03_etf import read_etf_daily as candidate_etf
from aspool.etf_api import read_etf_daily
from aspool.pool import DataPool, pool_lock

SNAPSHOT = Path("/home/ubuntu/aspool-recovery/20260927-data-remediation/snapshot")
LAB = Path("/home/ubuntu/aspool-labs/dg03-20260927")


def io():
    return {
        k: int(v)
        for k, v in (line.split(":") for line in Path("/proc/self/io").read_text().splitlines())
    }


def tree_bytes(root):
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


def emit(name, **values):
    entry = {"name": name, "time": time.time(), **values}
    with (LAB / "samples.jsonl").open("a") as f:
        f.write(json.dumps(entry, default=str) + "\n")
    print(json.dumps(entry, default=str), flush=True)
    return entry


def measured(name, fn, root=None):
    before = io()
    disk_before = tree_bytes(root) if root else 0
    peak_disk = [disk_before]
    done = threading.Event()

    def watch():
        while not done.wait(0.1):
            if root:
                try:
                    peak_disk[0] = max(peak_disk[0], tree_bytes(root))
                except FileNotFoundError:
                    pass

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    start = time.perf_counter()
    try:
        result = fn()
    finally:
        elapsed = time.perf_counter() - start
        done.set()
        thread.join()
    after = io()
    emit(
        name,
        seconds=elapsed,
        maxrss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
        io_delta={k: after[k] - before[k] for k in before},
        disk_before=disk_before,
        disk_after=tree_bytes(root) if root else 0,
        peak_sampled_disk=max(peak_disk),
        result=result,
    )
    return result


def connect(root, read_only=False):
    c = duckdb.connect(str(root / "catalog.duckdb"), read_only=read_only)
    c.execute("SET threads=2; SET memory_limit='1GB'")
    return c


def clone(source, target):
    for name in ["catalog.duckdb", "source-schema.json"]:
        shutil.copy2(source / name, target / name)


def provenance():
    import importlib.metadata

    import aspool

    repo = Path(__file__).resolve().parents[2]
    paths = [repo / "src/aspool/dg03_candidate.py", repo / "src/aspool/dg03_etf.py", Path(__file__)]
    emit(
        "provenance",
        git_head=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        imports=aspool.__file__,
        python=sys.executable,
        source_hashes={
            str(p.relative_to(repo)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths
        },
        snapshot=str(SNAPSHOT),
        lab=str(LAB),
        manifest_sha256=hashlib.sha256(
            (SNAPSHOT.parent / "manifest.json").read_bytes()
        ).hexdigest(),
        versions={
            n: importlib.metadata.version(n) for n in ["duckdb", "pandas", "pyarrow", "numpy"]
        },
        memory_limit="1GB",
        threads=2,
        cache="OS cache not evicted; raw repeats retained",
    )


def build_phase():
    inventory = measured("build_full", lambda: len(build(SNAPSHOT, LAB / "candidate")), LAB)
    emit("inventory_count", count=inventory)


def parity():
    root = LAB / "candidate"
    inventory = json.loads((root / "source-schema.json").read_text())
    manifest = json.loads((SNAPSHOT.parent / "manifest.json").read_text())
    expected = {r["path"]: r["sha256"] for r in manifest["files"]}
    # Exact values in each original schema, streaming the candidate in source key order.
    with connect(root, True) as c:
        reader = c.execute(
            "SELECT * FROM dg03_daily ORDER BY __market,__code,trade_date"
        ).fetch_record_batch(65536)
        pending = pa.Table.from_batches([], schema=reader.schema)
        checked = rows = 0
        for item in inventory:
            path = SNAPSHOT / item["path"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == expected[item["path"]]
            original = pq.ParquetFile(path).read()
            needed = len(original)
            while len(pending) < needed:
                pending = pa.concat_tables([pending, pa.Table.from_batches([next(reader)])])
            actual, pending = pending.slice(0, needed), pending.slice(needed)
            market = path.parts[-3].split("=")[1]
            code = path.parts[-2].split("=")[1]
            assert pc.all(pc.equal(actual["__market"], market)).as_py()
            assert pc.all(pc.equal(actual["__code"], code)).as_py()
            # Restore every source type, including null-only columns and timestamps.
            restored = []
            for field in original.schema:
                column = actual[field.name]
                if pa.types.is_null(field.type):
                    assert column.null_count == len(column), (item["path"], field.name)
                    restored.append(pa.nulls(len(column)))
                else:
                    restored.append(column.cast(field.type))
            actual = pa.Table.from_arrays(restored, schema=original.schema)
            original = original.take(
                pc.sort_indices(original, sort_keys=[("trade_date", "ascending")])
            )
            assert actual.equals(original, check_metadata=False), item["path"]
            checked += 1
            rows += needed
        assert len(pending) == 0
        assert next(reader, None) is None
        c.execute(f"ATTACH {literal(SNAPSHOT / 'catalog.duckdb')} AS baseline (READ_ONLY)")
        tables = c.execute(
            "SELECT table_name FROM duckdb_tables() WHERE database_name='baseline'"
        ).fetchall()
        catalog = {}
        for (table,) in tables:
            n = ident(table)
            mismatch = c.execute(
                f"SELECT count(*) FROM ((SELECT * FROM baseline.{n} EXCEPT ALL "
                f"SELECT * FROM main.{n}) UNION ALL (SELECT * FROM main.{n} "
                f"EXCEPT ALL SELECT * FROM baseline.{n}))"
            ).fetchone()[0]
            assert mismatch == 0, table
            catalog[table] = c.execute(f"SELECT count(*) FROM main.{n}").fetchone()[0]
        original_constraints = c.execute(
            "SELECT table_name,constraint_type,constraint_text "
            "FROM duckdb_constraints() WHERE database_name='baseline' ORDER BY ALL"
        ).fetchall()
        retained_constraints = c.execute(
            "SELECT table_name,constraint_type,constraint_text "
            "FROM duckdb_constraints() WHERE database_name <> 'baseline' "
            "AND NOT starts_with(table_name,'dg03_') ORDER BY ALL"
        ).fetchall()
        assert original_constraints == retained_constraints
        assert (
            hashlib.sha256((SNAPSHOT / "catalog.duckdb").read_bytes()).hexdigest()
            == expected["catalog.duckdb"]
        )
        schema = c.execute("DESCRIBE dg03_daily").fetchall()
        constraints = c.execute(
            "SELECT table_name,constraint_type,constraint_text FROM duckdb_constraints() "
            "WHERE database_name <> 'baseline'"
        ).fetchall()
        indexes = c.execute("SELECT index_name,sql FROM duckdb_indexes()").fetchall()
    return {
        "files_exact": checked,
        "rows_exact": rows,
        "catalog_tables_exact": catalog,
        "schema": schema,
        "constraints": constraints,
        "indexes": indexes,
        "metadata": "Original Arrow schemas retained; values cast losslessly and compared exactly",
    }


def compare_call(name, old_call, new_call, repeats=3):
    for repeat in range(repeats):
        tick = time.perf_counter()
        old = old_call()
        old_seconds = time.perf_counter() - tick
        tick = time.perf_counter()
        new = new_call()
        new_seconds = time.perf_counter() - tick
        pd.testing.assert_frame_equal(old, new)
        assert old.attrs == new.attrs
        emit(
            name,
            repeat=repeat,
            old_seconds=old_seconds,
            candidate_seconds=new_seconds,
            rows=len(old),
            columns=list(old.columns),
            exact=True,
            metadata_exact=True,
        )


def reads():
    old, new = DataPool(SNAPSHOT), CandidatePool(LAB / "candidate")
    end = date(2026, 9, 24)
    compare_call(
        "public_day",
        lambda: old.read_daily(start=end, end=end),
        lambda: new.read_daily(start=end, end=end),
    )
    start = end - timedelta(days=59)
    compare_call(
        "public_60_natural_days",
        lambda: old.read_research_daily(start=start, end=end),
        lambda: new.read_research_daily(start=start, end=end),
    )
    compare_call(
        "public_single_history",
        lambda: old.read_daily(symbols="000001.SZ"),
        lambda: new.read_daily(symbols="000001.SZ"),
    )
    compare_call(
        "etf_60_natural_days",
        lambda: read_etf_daily(SNAPSHOT, start=start, end=end),
        lambda: candidate_etf(LAB / "candidate", start=start, end=end),
    )
    # Five-year full-market all-column API is deliberately bounded by 60 natural days.
    for repeat in range(3):
        start = date(2021, 9, 25)
        total = 0
        old_total = new_total = 0
        while start <= end:
            stop = min(start + timedelta(days=59), end)
            tick = time.perf_counter()
            a = old.read_daily(start=start, end=stop)
            t1 = time.perf_counter() - tick
            tick = time.perf_counter()
            b = new.read_daily(start=start, end=stop)
            t2 = time.perf_counter() - tick
            pd.testing.assert_frame_equal(a, b)
            assert a.attrs == b.attrs
            emit(
                "public_five_year_window",
                repeat=repeat,
                start=start,
                end=stop,
                rows=len(a),
                old_seconds=t1,
                candidate_seconds=t2,
                exact=True,
            )
            total += len(a)
            old_total += t1
            new_total += t2
            start = stop + timedelta(days=1)
        emit(
            "public_five_year",
            repeat=repeat,
            rows=total,
            old_seconds=old_total,
            candidate_seconds=new_total,
            exact=True,
        )
    with connect(LAB / "candidate", True) as c:
        events = c.execute(
            "SELECT e.symbol,e.trade_date FROM daily_limit_events e "
            "JOIN daily_limit_publication p USING(trade_date,batch_id) "
            "ORDER BY e.trade_date,e.symbol"
        ).fetchdf()
        # Sparse keys from all five years, no narrow contiguous proxy.
        events = events.iloc[::50].copy()
        c.register("events", events)
        actual = c.execute(
            "SELECT e.symbol,e.trade_date,d.amount FROM events e LEFT JOIN dg03_daily d "
            "ON e.symbol=d.__code||'.'||d.__market AND e.trade_date=d.trade_date "
            "ORDER BY e.symbol,e.trade_date"
        ).fetchdf()
        files = [
            str(p) for p in (SNAPSHOT / "lake/bars/daily").glob("market=*/symbol=*/bars.parquet")
        ]
        rel = c.read_parquet(files, union_by_name=True, hive_partitioning=True)
        c.execute("CREATE TEMP VIEW old_bars AS " + rel.sql_query())
        tick = time.perf_counter()
        expected = c.execute(
            "SELECT e.symbol,e.trade_date,d.amount FROM events e LEFT JOIN old_bars d "
            "ON e.symbol=d.symbol||'.'||d.market AND e.trade_date=d.trade_date "
            "ORDER BY e.symbol,e.trade_date"
        ).fetchdf()
        old_seconds = time.perf_counter() - tick
        pd.testing.assert_frame_equal(expected, actual)
        emit("sparse_event_amount", keys=len(events), exact=True, old_seconds=old_seconds)


def profile(c, sql, name, params=None):
    path = LAB / (name + ".json")
    c.execute("PRAGMA enable_profiling='json'")
    c.execute(f"PRAGMA profiling_output={literal(path)}")
    result = c.execute(sql, params or []).fetchall()
    c.execute("PRAGMA disable_profiling")
    data = json.loads(path.read_text())
    scans = []

    def walk(node):
        if "SCAN" in node.get("operator_name", ""):
            scans.append(
                {
                    k: node.get(k)
                    for k in [
                        "operator_name",
                        "operator_rows_scanned",
                        "operator_cardinality",
                        "extra_info",
                    ]
                }
            )
        for child in node.get("children", []):
            walk(child)

    walk(data)
    emit(
        name,
        rows=len(result),
        scans=scans,
        cpu_time=data.get("cpu_time"),
        peak_buffer=data.get("system_peak_buffer_memory"),
        peak_temp=data.get("system_peak_temp_dir_size"),
        profile=str(path),
    )
    return result


def physical_bounds(c, name):
    segments = c.execute("PRAGMA storage_info('dg03_daily')").fetchdf()
    dates = segments[(segments.column_name == "trade_date") & (segments.segment_type == "DATE")]
    bounds = {}
    for row in dates.itertuples():
        match = re.search(r"Min: ([0-9-]+), Max: ([0-9-]+)", row.stats)
        if not match:
            raise AssertionError("unrecognized date statistics")
        low, high = match.groups()
        if row.row_group_id not in bounds:
            bounds[row.row_group_id] = [low, high, 0]
        value = bounds[row.row_group_id]
        value[0], value[1] = min(value[0], low), max(value[1], high)
        value[2] += row.count
    # Conservative possible rows from date zonemaps, not claimed physical reads.
    day = "2026-09-24"
    eligible = [v for v in bounds.values() if v[0] <= day <= v[1]]
    emit(
        name,
        row_groups=len(bounds),
        total_rows=sum(v[2] for v in bounds.values()),
        day_eligible_groups=len(eligible),
        day_row_upper_bound=sum(v[2] for v in eligible),
        updated_segments=int(segments.has_updates.sum()),
    )
    (LAB / (name + ".json")).write_text(json.dumps(bounds, default=str))


def growth(factors=(1, 2, 5), more_securities=True):
    # 1x/2x/5x is the entire 17.86M-row complete-schema population, shifted by 40 years.
    for factor in factors:
        root = LAB / f"growth-{factor}x"
        root.mkdir()
        clone(LAB / "candidate", root)
        with connect(root) as c:
            c.execute(f"ATTACH {literal(LAB / 'candidate/catalog.duckdb')} AS base (READ_ONLY)")
            for i in range(1, factor):
                measured(
                    f"growth_{factor}x_add_{i}",
                    lambda i=i: c.execute(
                        "INSERT INTO dg03_daily SELECT * REPLACE "
                        f"((trade_date - INTERVAL '{40 * i} years')::DATE "
                        f"AS trade_date) FROM base.dg03_daily"
                    ).fetchone(),
                    root,
                )
            c.execute("CHECKPOINT")
            emit(
                "growth_size",
                factor=factor,
                rows=c.execute("SELECT count(*) FROM dg03_daily").fetchone()[0],
                securities=c.execute(
                    "SELECT count(DISTINCT (__market,__code)) FROM dg03_daily"
                ).fetchone()[0],
                disk=tree_bytes(root),
            )
            physical_bounds(c, f"growth_{factor}x_physical_before")
            for repeat in range(3):
                profile(
                    c,
                    "SELECT * FROM dg03_daily WHERE trade_date=DATE '2026-09-24'",
                    f"growth_{factor}x_day_{repeat}",
                )
                profile(
                    c,
                    "SELECT * FROM dg03_daily WHERE __code='000001' AND __market='SZ'",
                    f"growth_{factor}x_single_{repeat}",
                )
            seed = (
                c.execute(
                    "SELECT * FROM dg03_daily WHERE __code='000001' AND __market='SZ' "
                    "ORDER BY trade_date DESC LIMIT 1"
                )
                .fetchdf()
                .iloc[0]
                .to_dict()
            )
        pool = CandidatePool(root)
        for cycle in range(12):
            changes = [
                dict(
                    market="SZ",
                    code="000001",
                    trade_date="2026-09-24",
                    values={"amount": float(seed["amount"]) + cycle + 1},
                )
            ]
            measured(
                f"growth_{factor}x_update_{cycle}",
                lambda: pool.apply(changes, source="synthetic", reason="repeated correction"),
                root,
            )
            measured(
                f"growth_{factor}x_noop_{cycle}",
                lambda: pool.apply(changes, source="synthetic", reason="no-op replay"),
                root,
            )
        with connect(root) as c:
            physical_bounds(c, f"growth_{factor}x_physical_after")
            measured(
                f"growth_{factor}x_checkpoint", lambda: c.execute("CHECKPOINT").fetchall(), root
            )
            profile(
                c,
                "SELECT * FROM dg03_daily WHERE trade_date=DATE '2026-09-24'",
                f"growth_{factor}x_after_updates",
            )
    if not more_securities:
        return
    # Independent more-securities scenario: duplicate identities, keep full history and all fields.
    root = LAB / "growth-securities"
    root.mkdir()
    clone(LAB / "candidate", root)
    with connect(root) as c:
        measured(
            "growth_double_securities",
            lambda: c.execute(
                "INSERT INTO dg03_daily SELECT * REPLACE ('X'||__code AS __code) FROM dg03_daily"
            ).fetchone(),
            root,
        )
        c.execute("CHECKPOINT")
        profile(
            c,
            "SELECT * FROM dg03_daily WHERE trade_date=DATE '2026-09-24'",
            "double_securities_day",
        )
        emit(
            "more_securities",
            rows=c.execute("SELECT count(*) FROM dg03_daily").fetchone()[0],
            securities=c.execute(
                "SELECT count(DISTINCT (__market,__code)) FROM dg03_daily"
            ).fetchone()[0],
            disk=tree_bytes(root),
        )


def mutations():
    root = LAB / "mutations"
    root.mkdir()
    clone(LAB / "candidate", root)
    pool = CandidatePool(root)
    with connect(root, True) as c:
        names = [r[0] for r in c.execute("DESCRIBE dg03_daily").fetchall()]
        seed = dict(
            zip(
                names,
                c.execute(
                    "SELECT * FROM dg03_daily WHERE __code='000001' AND __market='SZ' "
                    "ORDER BY trade_date DESC LIMIT 1"
                ).fetchone(),
            )
        )
    values = {k: v for k, v in seed.items() if k not in {"__market", "__code", "trade_date"}}

    def apply(name, changes):
        return measured(name, lambda: pool.apply(changes, source="dg03:fixture", reason=name), root)

    def row(day, operation="merge", v=None):
        return dict(
            market="SZ",
            code="000001",
            trade_date=day,
            operation=operation,
            values=values if v is None else v,
        )

    assert apply("insert", [row("2026-09-25")])["changed"] == 1
    assert apply("no_op", [row("2026-09-25")])["changed"] == 0
    assert apply("correct", [row("2026-09-25", v={"amount": 12345.0})])["changed"] == 1
    assert apply("delete", [row("2026-09-25", "delete")])["changed"] == 1
    days = [date(1970, 12, 25) + timedelta(days=i) for i in range(20)]
    assert apply("backfill_cross_year", [row(day) for day in days])["changed"] == 20
    assert apply("backfill_no_op", [row(day) for day in days])["changed"] == 0
    with connect(root, True) as c:
        emit(
            "mutation_state",
            revision=c.execute("SELECT * FROM dg03_revision").fetchall(),
            changes=c.execute(
                "SELECT operation,count(*) FROM dg03_changes GROUP BY operation"
            ).fetchall(),
            stale=c.execute("SELECT count(*) FROM daily_limit_staleness").fetchone()[0],
            coverage=c.execute("SELECT * FROM coverage WHERE symbol='000001'").fetchall(),
        )


def process_child(mode, root):
    # The parent waits for READY, then SIGKILLs genuine separate processes.
    if mode in {"crash_uncommitted", "crash_committed"}:
        c = connect(root)
        c.execute("SET wal_autocheckpoint='1GB'")
        c.execute("CREATE TABLE IF NOT EXISTS crash_probe(id INTEGER PRIMARY KEY)")
        key = c.execute(
            "SELECT __market,__code,trade_date,amount FROM dg03_daily "
            "ORDER BY __market,__code,trade_date LIMIT 1"
        ).fetchone()
        c.execute("CHECKPOINT; BEGIN; INSERT INTO crash_probe VALUES (1)")
        c.execute(
            "UPDATE dg03_daily SET amount=amount+7 WHERE __market=? AND __code=? AND trade_date=?",
            list(key[:3]),
        )
        c.execute("UPDATE dg03_revision SET revision=revision+1")
        if mode == "crash_committed":
            c.execute("COMMIT")
        print("READY", flush=True)
        time.sleep(300)
    elif mode == "hold_write":
        with pool_lock(root, write=True), connect(root) as c:
            print("READY", flush=True)
            time.sleep(1.5)
    elif mode == "hold_read":
        with pool_lock(root), connect(root, True) as c:
            print("READY", flush=True)
            time.sleep(1.5)
    elif mode == "uncoordinated":
        try:
            with connect(root, True):
                pass
        except duckdb.IOException as e:
            print(json.dumps({"rejected": True, "error": str(e)}), flush=True)
        else:
            raise AssertionError("expected native cross-process lock rejection")
    elif mode == "coordinated_write":
        tick = time.perf_counter()
        with pool_lock(root, write=True), connect(root) as c:
            c.execute("CREATE TABLE IF NOT EXISTS concurrency_probe(n INTEGER)")
            c.execute("INSERT INTO concurrency_probe VALUES (1)")
            count = c.execute("SELECT count(*) FROM concurrency_probe").fetchone()[0]
        print(json.dumps({"commits": count, "seconds": time.perf_counter() - tick}), flush=True)
    elif mode == "coordinated_read":
        tick = time.perf_counter()
        with pool_lock(root), connect(root, True) as c:
            count = c.execute("SELECT count(*) FROM dg03_daily").fetchone()[0]
        print(json.dumps({"rows": count, "seconds": time.perf_counter() - tick}), flush=True)


def child(mode, root):
    return subprocess.Popen(
        [sys.executable, __file__, "child", "--mode", mode, "--root", str(root)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def recovery():
    for mode in ["crash_uncommitted", "crash_committed"]:
        root = LAB / mode
        root.mkdir()
        clone(LAB / "candidate", root)
        with connect(root, True) as c:
            before = c.execute(
                "SELECT amount FROM dg03_daily ORDER BY __market,__code,trade_date LIMIT 1"
            ).fetchone()[0]
        p = child(mode, root)
        assert p.stdout.readline().strip() == "READY"
        wal_path = root / "catalog.duckdb.wal"
        wal = wal_path.stat().st_size if wal_path.exists() else 0
        p.kill()
        p.wait()
        tick = time.perf_counter()
        with connect(root) as c:
            count = c.execute("SELECT count(*) FROM crash_probe").fetchone()[0]
            amount = c.execute(
                "SELECT amount FROM dg03_daily ORDER BY __market,__code,trade_date LIMIT 1"
            ).fetchone()[0]
            revision = c.execute("SELECT revision FROM dg03_revision").fetchone()[0]
        assert amount == before + 7 * int(mode == "crash_committed")
        assert revision == int(mode == "crash_committed")
        assert count == int(mode == "crash_committed")
        emit(
            mode,
            returncode=p.returncode,
            wal_before_kill=wal,
            recovered_rows=count,
            recovered_amount=amount,
            recovered_revision=revision,
            recovery_seconds=time.perf_counter() - tick,
        )
    root = LAB / "concurrency"
    root.mkdir()
    clone(LAB / "candidate", root)
    p = child("hold_write", root)
    assert p.stdout.readline().strip() == "READY"
    other = child("uncoordinated", root)
    out, err = other.communicate()
    assert other.returncode == 0, err
    p.wait()
    emit("native_process_conflict", result=json.loads(out))
    p = child("hold_write", root)
    assert p.stdout.readline().strip() == "READY"
    other = child("coordinated_read", root)
    out, err = other.communicate()
    assert other.returncode == 0, err
    p.wait()
    emit("coordinated_writer_reader", result=json.loads(out))
    p = child("hold_read", root)
    assert p.stdout.readline().strip() == "READY"
    other = child("coordinated_read", root)
    out, err = other.communicate()
    assert other.returncode == 0, err
    p.wait()
    emit("concurrent_readers", result=json.loads(out))
    for holder in ["hold_write", "hold_read"]:
        p = child(holder, root)
        assert p.stdout.readline().strip() == "READY"
        other = child("coordinated_write", root)
        out, err = other.communicate()
        assert other.returncode == 0, err
        p.wait()
        emit(holder + "_then_writer", result=json.loads(out))
    target = LAB / "restored"
    target.mkdir()
    tick = time.perf_counter()
    with pool_lock(root):
        clone(root, target)
    assert (
        hashlib.sha256((root / "catalog.duckdb").read_bytes()).digest()
        == hashlib.sha256((target / "catalog.duckdb").read_bytes()).digest()
    )
    pd.testing.assert_frame_equal(
        CandidatePool(root).read_daily(start="2026-09-24"),
        CandidatePool(target).read_daily(start="2026-09-24"),
    )
    emit(
        "snapshot_restore", seconds=time.perf_counter() - tick, bytes=tree_bytes(target), exact=True
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase", choices=["build", "parity", "reads", "mutations", "growth", "recovery", "child"]
    )
    parser.add_argument("--mode")
    parser.add_argument("--factor", type=int, choices=[1, 2, 5])
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    if args.phase == "child":
        process_child(args.mode, args.root)
        return
    LAB.mkdir(parents=True, exist_ok=True)
    provenance()
    measured(
        "phase_" + args.phase,
        {
            "build": build_phase,
            "parity": parity,
            "reads": reads,
            "mutations": mutations,
            "growth": lambda: growth(
                [args.factor] if args.factor else [1, 2, 5],
                more_securities=args.factor in (None, 5),
            ),
            "recovery": recovery,
        }[args.phase],
        LAB,
    )


if __name__ == "__main__":
    main()
