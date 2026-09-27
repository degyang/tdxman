#!/usr/bin/env python3
"""Complete-schema immutable monthly alternative; fixed isolated roots only."""

import argparse
import json
import os
import shutil
import signal
import threading
import time
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow.parquet as pq
from bench import (
    LAB,
    SNAPSHOT,
    candidate_root,
    current_rss,
    emit,
    file_sha256,
    measured,
    provenance,
    tree_bytes,
)

from aspool.dg03_candidate import CandidatePool, literal
from aspool.dg03_monthly import (
    MonthlyPool,
    connection,
    initialize,
    install_part,
    month_bounds,
    part_path,
    sync,
)


@contextmanager
def rss_guard():
    done = threading.Event()

    def watch():
        while not done.wait(0.1):
            if current_rss() > 3 * 1024**3:
                emit("monthly_rss_guard", rss=current_rss(), limit=3 * 1024**3)
                os.kill(os.getpid(), signal.SIGINT)
                return

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    try:
        yield
    finally:
        done.set()
        thread.join()


def build_monthly(factor):
    root = LAB / f"monthly-{factor}x"
    initialize(SNAPSHOT, root, candidate_root())
    with connection(root) as c:
        c.execute(f"ATTACH {literal(candidate_root() / 'catalog.duckdb')} AS origin (READ_ONLY)")
        c.execute(
            "COPY (SELECT * FROM origin.dg03_daily WHERE false) TO "
            f"{literal(root / 'empty.parquet')} (FORMAT PARQUET)"
        )
        months = [
            r[0]
            for r in c.execute(
                "SELECT DISTINCT strftime(trade_date,'%Y-%m') FROM origin.dg03_daily ORDER BY 1"
            ).fetchall()
        ]
        total, completed = 0, 0
        if factor > 1:
            prior_factor = 2 if factor == 5 else 1
            prior_root = LAB / f"monthly-{prior_factor}x"
            baseline = json.loads((prior_root / "build-manifest.json").read_text())
            for record in baseline:
                destination = part_path(root, record[1])
                shutil.copy2(part_path(prior_root, record[1]), destination)
                assert file_sha256(destination) == record[5]
                c.execute("INSERT INTO dg03_monthly_parts VALUES (?,?,?,?,?,?)", record)
                total += record[4]
            completed = prior_factor
            emit(
                "monthly_reused_exact_prefix",
                factor=factor,
                prefix_factor=prior_factor,
                files=len(baseline),
                rows=total,
                sha256_exact=True,
            )
        for copy in range(completed, factor):
            for key in months:
                lo, hi = month_bounds(key)
                target_key = f"{lo.year - 40 * copy:04d}-{lo.month:02d}"
                query = (
                    f"SELECT * REPLACE((trade_date - INTERVAL '{40 * copy} years')::DATE "
                    "AS trade_date) "
                    f"FROM origin.dg03_daily WHERE trade_date >= DATE {literal(lo)} "
                    f"AND trade_date < DATE {literal(hi)}"
                )
                path = install_part(c, root, target_key, query)
                # Bidirectional full-column multiset comparison includes SQL NULLs and extensions.
                types = c.execute("DESCRIBE " + query).fetchall()
                actual_types = c.execute(
                    f"DESCRIBE SELECT * FROM read_parquet({literal(path)})"
                ).fetchall()
                assert [(r[0], r[1]) for r in types] == [(r[0], r[1]) for r in actual_types]
                for left, right in [
                    (query, f"SELECT * FROM read_parquet({literal(path)})"),
                    (f"SELECT * FROM read_parquet({literal(path)})", query),
                ]:
                    assert (
                        c.execute(
                            f"SELECT count(*) FROM (({left}) EXCEPT ALL ({right}))"
                        ).fetchone()[0]
                        == 0
                    )
                n = c.execute(
                    "SELECT row_count FROM dg03_monthly_parts WHERE month=?", [target_key]
                ).fetchone()[0]
                total += n
                emit(
                    "monthly_part_parity",
                    factor=factor,
                    month=target_key,
                    rows=n,
                    columns=len(types),
                    exact=True,
                    path=str(path),
                )
            c.execute("CHECKPOINT")
        if factor > 1:
            c.execute("DELETE FROM coverage")
            c.execute(
                "INSERT INTO coverage SELECT __code,__market,(min(trade_date)-INTERVAL '"
                + str(40 * (factor - 1))
                + " years')::DATE,max(trade_date),count(*)*?,"
                "'dg03:synthetic-growth',current_timestamp FROM origin.dg03_daily "
                "GROUP BY __code,__market",
                [factor],
            )
        # Original catalog parity before synthetic coverage changes is already checked
        # for factor 1; factors preserve all other tables and all schema constraints.
        original = [
            r[0]
            for r in c.execute("SHOW TABLES FROM origin").fetchall()
            if not r[0].startswith("dg03_")
        ]
        for table in original:
            if factor > 1 and table == "coverage":
                continue
            for left, right in [(table, f"origin.{table}"), (f"origin.{table}", table)]:
                assert (
                    c.execute(
                        f"SELECT count(*) FROM (SELECT * FROM {left} "
                        f"EXCEPT ALL SELECT * FROM {right})"
                    ).fetchone()[0]
                    == 0
                )
        c.execute("CHECKPOINT")
        (root / "build-manifest.json").write_text(
            json.dumps(
                c.execute("SELECT * FROM dg03_monthly_parts ORDER BY month").fetchall(), default=str
            )
        )
        count = c.execute(
            "SELECT sum(row_count),count(*),min(first_day),max(last_day) FROM dg03_monthly_parts"
        ).fetchone()
        assert total == 17862387 * factor
        emit(
            "monthly_built",
            factor=factor,
            rows=total,
            manifest=count,
            fields=42,
            original_tables=len(original),
            root=str(root),
            disk=tree_bytes(root),
            catalog=(root / "catalog.duckdb").stat().st_size,
        )
    sync(root, directory=True)


def read_samples(factor, workload=None):
    root = LAB / f"monthly-{factor}x"
    pool, expected = MonthlyPool(root), CandidatePool(LAB / f"prepared-{factor}x")
    cases = [
        ("day", dict(start="2026-09-24", end="2026-09-24")),
        ("60days", dict(start="2026-07-27", end="2026-09-24")),
        ("single", dict(symbols="000001.SZ")),
    ]
    for name, kwargs in cases:
        if workload and workload != name:
            continue
        reference = expected.read_daily(**kwargs)
        for repeat in range(3):

            def read():
                t = time.perf_counter()
                frame = pool.read_daily(**kwargs)
                elapsed = time.perf_counter() - t
                pd.testing.assert_frame_equal(reference, frame)
                assert reference.attrs == frame.attrs and len(frame.columns) == 35
                return dict(rows=len(frame), fields=35, api_seconds=elapsed, exact=True)

            measured(
                f"monthly_{factor}x_public_{name}{'_bounded' if workload else ''}_{repeat}",
                read,
                root,
            )
    if factor == 1 and not workload:
        reference = expected.read_etf_daily(start="2026-07-27", end="2026-09-24")

        def etf():
            frame = pool.read_etf_daily(start="2026-07-27", end="2026-09-24")
            pd.testing.assert_frame_equal(reference, frame)
            assert reference.attrs == frame.attrs
            return dict(rows=len(frame), columns=list(frame.columns), exact=True)

        measured("monthly_etf", etf, root)
        start, end = date(2021, 9, 25), date(2026, 9, 24)
        total, elapsed = 0, 0
        while start <= end:
            stop = min(start + timedelta(days=59), end)
            reference = expected.read_daily(start=start, end=stop)
            t = time.perf_counter()
            frame = pool.read_daily(start=start, end=stop)
            seconds = time.perf_counter() - t
            pd.testing.assert_frame_equal(reference, frame)
            assert reference.attrs == frame.attrs
            total += len(frame)
            elapsed += seconds
            emit(
                "monthly_fiveyear_window",
                start=start,
                end=stop,
                rows=len(frame),
                fields=35,
                seconds=seconds,
                exact=True,
            )
            start = stop + timedelta(days=1)
        assert total == 6225285
        emit("monthly_fiveyear_public", rows=total, fields=35, seconds=elapsed, exact=True)


def changes_for_day():
    with duckdb.connect(str(candidate_root() / "catalog.duckdb"), read_only=True) as c:
        c.execute("SET threads=1; SET memory_limit='1GB'")
        names = [r[0] for r in c.execute("DESCRIBE dg03_daily").fetchall()]
        rows = c.execute("SELECT * FROM dg03_daily WHERE trade_date='2026-09-24'").fetchall()
    changes = []
    for raw in rows:
        row = dict(zip(names, raw))
        row.pop("trade_date")
        changes.append(
            dict(
                market=row.pop("__market"),
                code=row.pop("__code"),
                trade_date="2026-09-25",
                values=row,
            )
        )
    return changes


def writes(factor):
    root, changes = LAB / f"monthly-{factor}x", changes_for_day()
    pool = MonthlyPool(root)

    def manifest():
        with connection(root, True) as c:
            return {
                r[0]: r[1:]
                for r in c.execute(
                    "SELECT month,path,sha256,row_count FROM dg03_monthly_parts"
                ).fetchall()
            }

    initial = manifest()
    fingerprints = {}
    for key, record in initial.items():
        info = part_path(root, record[0]).stat()
        fingerprints[key] = (info.st_ino, info.st_size, info.st_mtime_ns)

    def apply(name, changes):
        before = manifest()
        result = measured(
            f"monthly_{factor}x_{name}",
            lambda: pool.apply(changes, source="dg03:synthetic", reason=name),
            root,
        )
        after = manifest()
        changed = sorted(
            key for key in before.keys() | after.keys() if before.get(key) != after.get(key)
        )
        assert len(changed) == result["published_months"]
        emit(
            "monthly_rewrite_bound",
            factor=factor,
            operation=name,
            changed_months=changed,
            old_rows=sum(before[k][2] for k in changed if k in before),
            new_rows=sum(after[k][2] for k in changed if k in after),
            new_file_bytes=sum(
                part_path(root, after[k][0]).stat().st_size for k in changed if k in after
            ),
            catalog_copied_bytes=result["catalog_copied_bytes"],
            unchanged_manifest_months=len(before.keys() - set(changed)),
        )
        return result

    assert apply("fullmarket_insert", changes)["changed"] == len(changes)
    with connection(root, True) as c:
        c.execute(f"ATTACH {literal(candidate_root() / 'catalog.duckdb')} AS origin (READ_ONLY)")
        path = part_path(root, manifest()["2026-09"][0])
        expected = (
            "SELECT * REPLACE(DATE '2026-09-25' AS trade_date) FROM origin.dg03_daily "
            "WHERE trade_date=DATE '2026-09-24'"
        )
        actual = f"SELECT * FROM read_parquet({literal(path)}) WHERE trade_date=DATE '2026-09-25'"
        for left, right in [(actual, expected), (expected, actual)]:
            assert (
                c.execute(f"SELECT count(*) FROM (({left}) EXCEPT ALL ({right}))").fetchone()[0]
                == 0
            )
        emit(
            "monthly_fullmarket_insert_parity",
            factor=factor,
            rows=len(changes),
            fields=42,
            exact=True,
            atomic_revision=1,
        )
    before = file_sha256(root / "catalog.duckdb")
    assert apply("fullmarket_noop", changes)["changed"] == 0
    assert file_sha256(root / "catalog.duckdb") == before
    for cycle in range(12):
        change = [
            dict(
                market="SZ",
                code="000001",
                trade_date="2026-09-24",
                values={"amount": 12345678.0 + cycle},
            )
        ]
        assert apply(f"correction_{cycle}", change)["changed"] == 1
        before = file_sha256(root / "catalog.duckdb")
        assert apply(f"noop_{cycle}", change)["changed"] == 0
        assert file_sha256(root / "catalog.duckdb") == before
    seed = next(r for r in changes if r["market"] == "SZ" and r["code"] == "000001")
    assert apply("delete", [dict(seed, operation="delete")])["changed"] == 1
    backfill = [
        dict(seed, trade_date=str(date(1800, 12, 25) + timedelta(days=i))) for i in range(20)
    ]
    assert apply("backfill_two_months", backfill)["changed"] == 20
    assert apply("backfill_noop", backfill)["changed"] == 0
    final = manifest()
    cold = [key for key in initial if key != "2026-09"]
    for key in cold:
        assert initial[key] == final[key]
        info = part_path(root, final[key][0]).stat()
        assert fingerprints[key] == (info.st_ino, info.st_size, info.st_mtime_ns)
    emit(
        "monthly_cold_files_unchanged",
        factor=factor,
        months=len(cold),
        verified="manifest/path/SHA plus inode,size,mtime unchanged across all operations",
    )
    with connection(root) as c:
        measured(f"monthly_{factor}x_checkpoint", lambda: c.execute("CHECKPOINT").fetchall(), root)
        manifest = c.execute(
            "SELECT month,row_count,path FROM dg03_monthly_parts ORDER BY month"
        ).fetchall()
        assert sum(r[1] for r in manifest) == factor * 17862387 + 7285
        assert c.execute("SELECT revision FROM dg03_revision").fetchone()[0] == 15
        emit(
            "monthly_write_state",
            factor=factor,
            revision=c.execute("SELECT * FROM dg03_revision").fetchall(),
            changes=c.execute("SELECT count(*) FROM dg03_changes").fetchone()[0],
            catalog=(root / "catalog.duckdb").stat().st_size,
            root_disk=tree_bytes(root),
            manifest=manifest,
        )


def bounds(factor, only=None):
    root = LAB / f"monthly-{factor}x"
    with connection(root, True) as c:
        for name, lo, hi in [
            ("day", "2026-09-24", "2026-09-24"),
            ("60days", "2026-07-27", "2026-09-24"),
            ("fiveyears", "2021-09-25", "2026-09-24"),
        ]:
            if only and name != only:
                continue
            records = c.execute(
                "SELECT month,path,row_count FROM dg03_monthly_parts "
                "WHERE last_day>=? AND first_day<=? ORDER BY month",
                [lo, hi],
            ).fetchall()
            files = [str(part_path(root, r[1])) for r in records]
            rel = c.read_parquet(files, hive_partitioning=False)
            query = (
                f"SELECT * FROM ({rel.sql_query()}) "
                f"WHERE trade_date BETWEEN DATE {literal(lo)} AND DATE {literal(hi)}"
            )
            profile = LAB / f"monthly_{factor}x_{name}_profile.json"
            c.execute("PRAGMA enable_profiling='json'")
            c.execute(f"PRAGMA profiling_output={literal(profile)}")
            rows = sum(b.num_rows for b in c.execute(query).fetch_record_batch(65536))
            c.execute("PRAGMA disable_profiling")
            emit(
                "monthly_scan_bounds",
                factor=factor,
                workload=name,
                files=len(files),
                eligible_rows=sum(r[2] for r in records),
                file_bytes=sum(Path(f).stat().st_size for f in files),
                returned=rows,
                fields=42,
                profile=str(profile),
            )
        if only and only != "single":
            return
        records = c.execute(
            "SELECT path,row_count FROM dg03_monthly_parts ORDER BY month"
        ).fetchall()
        files = [str(part_path(root, r[0])) for r in records]
        eligible = 0
        for path in files:
            metadata = pq.ParquetFile(path).metadata
            names = metadata.schema.names
            for group in range(metadata.num_row_groups):
                row_group = metadata.row_group(group)
                code = row_group.column(names.index("__code")).statistics
                market = row_group.column(names.index("__market")).statistics
                if (code is None or code.min <= "000001" <= code.max) and (
                    market is None or market.min <= "SZ" <= market.max
                ):
                    eligible += row_group.num_rows
        rel = c.read_parquet(files, hive_partitioning=False)
        path = LAB / f"monthly_{factor}x_single_full_schema_profile.json"
        c.execute("PRAGMA enable_profiling='json'")
        c.execute(f"PRAGMA profiling_output={literal(path)}")
        reader = c.execute(
            "SELECT * FROM (" + rel.sql_query() + ") WHERE __market='SZ' AND __code='000001'"
        ).fetch_record_batch(65536)
        assert len(reader.schema) == 42
        count = sum(batch.num_rows for batch in reader)
        c.execute("PRAGMA disable_profiling")
        assert count == 6318 * factor
        emit(
            "monthly_single_scan_bound",
            factor=factor,
            fields=42,
            rows=count,
            bound_files=len(files),
            row_group_upper_bound=eligible,
            profile=str(path),
            note="one full-column physical scan, public validation uses additional scans",
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["build", "reads", "writes", "bounds", "events"])
    parser.add_argument("--factor", type=int, choices=[1, 2, 5], required=True)
    parser.add_argument("--workload", choices=["day", "60days", "single"])
    args = parser.parse_args()
    provenance()
    emit(
        "monthly_sources",
        script_sha256=file_sha256(Path(__file__)),
        module_sha256=file_sha256(Path("src/aspool/dg03_monthly.py")),
    )
    with rss_guard():
        measured(
            f"phase_monthly_{args.phase}_{args.factor}x",
            lambda: {
                "build": lambda: build_monthly(args.factor),
                "reads": lambda: read_samples(args.factor, args.workload),
                "writes": lambda: writes(args.factor),
                "bounds": lambda: bounds(args.factor, args.workload),
                "events": lambda: __import__("events").run(
                    MonthlyPool(LAB / f"monthly-{args.factor}x"), threads=1
                ),
            }[args.phase](),
            LAB,
        )
