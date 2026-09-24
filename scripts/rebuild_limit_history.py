"""Rebuild published limit facts from existing bars and export an audited handoff.

Example: python scripts/rebuild_limit_history.py --start 2021-09-24 --end 2026-09-24
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import resource
import shutil
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pyarrow.parquet as pq

from aspool import DataPool
from aspool.limit_events import RULE_VERSION
from aspool.pool import pool_lock
from aspool.store import read_only_catalog


def bar_manifest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((root / "lake" / "bars" / "daily").rglob("*.parquet")):
        stat = path.stat()
        digest.update(f"{path.relative_to(root)}:{stat.st_size}:{stat.st_mtime_ns}\n".encode())
    return digest.hexdigest()


def write_json(path: Path, value: dict) -> None:
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")
    temp.replace(path)


def main() -> None:
    started = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.home() / ".aspool")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--warmup-start", type=date.fromisoformat)
    parser.add_argument("--cache-years", type=int, default=6)
    parser.add_argument("--export-only", action="store_true",
                        help="Export and validate the current publication without recomputing")
    args = parser.parse_args()
    warmup = args.warmup_start or args.start - timedelta(days=120)
    if not warmup <= args.start <= args.end:
        parser.error("Require warmup-start <= start <= end")
    root = args.root.expanduser().resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = root / "reports" / "limit-history" / stamp
    output.mkdir(parents=True, exist_ok=False)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(output / "run.log")],
    )
    report = dict(
        status="running", root=str(root), output=str(output), rule_version=RULE_VERSION,
        requested_start=args.start, requested_end=args.end, warmup_start=warmup,
        started_at=datetime.now(timezone.utc),
        mode="export-only" if args.export_only else "rebuild",
    )
    write_json(output / "report.json", report)
    try:
        with pool_lock(root, write=True):
            # Writers close their catalog under this lock, flushing its WAL.
            if (root / "catalog.duckdb.wal").exists():
                raise RuntimeError("Catalog has a pending WAL; refusing an incomplete backup")
            shutil.copy2(root / "catalog.duckdb", output / "catalog.before.duckdb")
            manifest_before = bar_manifest(root)
        logging.info("Backup saved at %s; deriving %s through %s", output, warmup, args.end)
        pool = DataPool(root)
        batches = (pool.read_limit_coverage(start=warmup, end=args.end)
                   if args.export_only else
                   pool.compute_limit_events([warmup, args.end], cache_years=args.cache_years))
        coverage = pool.read_limit_coverage(start=args.start, end=args.end)
        summary = pool.read_limit_summary(start=args.start, end=args.end)
        events = pool.read_limit_events(start=args.start, end=args.end)
        exceptions = pool.read_limit_exceptions(start=args.start, end=args.end)
        calendar = pool.read_trading_calendar(start=args.start, end=args.end)
        expected_dates = set(calendar.loc[calendar.is_open, "trade_date"].dt.date)
        actual_dates = set(coverage.trade_date.dt.date)
        missing_dates = sorted(expected_dates - actual_dates)
        extra_dates = sorted(actual_dates - expected_dates)
        for name, frame in (
            ("coverage", coverage), ("summary", summary),
            ("events", events), ("exceptions", exceptions),
        ):
            frame.to_parquet(output / f"{name}.parquet", index=False)
            if name in {"coverage", "summary"}:
                frame.to_csv(output / f"{name}.csv", index=False)
        up = events[events.close_limit_up.eq(True)]
        streaks = up.groupby("trade_date").agg(
            limit_up_count=("symbol", "size"),
            consecutive_known=("consecutive_up", "count"),
            known_max_consecutive=("consecutive_up", "max"),
        ).reset_index()
        streaks["consecutive_unknown"] = streaks.limit_up_count - streaks.consecutive_known
        if "consecutive_gap_sessions" in up:
            gap_counts = up.assign(
                gap_bridged=up.consecutive_gap_sessions.gt(0)
            ).groupby("trade_date").gap_bridged.sum()
            streaks["gap_bridged_count"] = streaks.trade_date.map(gap_counts).fillna(0).astype(int)
        streaks.to_csv(output / "consecutive_coverage.csv", index=False)
        # Verify all publication components agree at date/batch granularity.
        with pool_lock(root), read_only_catalog(root) as conn:
            has_boundaries = conn.execute(
                "select count(*) from information_schema.tables "
                "where table_name='daily_limit_streak_boundaries'"
            ).fetchone()[0]
            boundary_count = 0
            if has_boundaries:
                boundaries = conn.execute(
                    """select z.* from daily_limit_streak_boundaries z
                       join daily_limit_publication p using (trade_date, batch_id)
                       where z.trade_date between ? and ? order by z.trade_date, z.symbol""",
                    [args.start, args.end],
                ).fetchdf()
                boundaries.to_csv(output / "streak_boundaries.csv", index=False)
                boundary_count = len(boundaries)
            count_mismatches = conn.execute("""
                select count(*) from daily_limit_batches b
                join daily_limit_publication p using (trade_date, batch_id)
                where b.trade_date between ? and ? and (
                    b.processed_count != b.known_count + b.unknown_count
                        + b.no_limit_count + b.invalid_count
                    or b.processed_count != (select count(*) from daily_limit_scope s
                        where s.trade_date=b.trade_date and s.batch_id=b.batch_id))
                """, [args.start, args.end]).fetchone()[0]
            reference_counts = dict(conn.execute("""
                select r.basis, count(*) from daily_limit_references r
                join daily_limit_publication p using (trade_date, batch_id)
                where r.trade_date between ? and ? group by r.basis
                """, [args.start, args.end]).fetchall())
            reader = conn.execute("""
                select r.* from daily_limit_references r
                join daily_limit_publication p using (trade_date, batch_id)
                where r.trade_date between ? and ?
                """, [args.start, args.end]).fetch_record_batch(100000)
            with pq.ParquetWriter(output / "reference_audit.parquet", reader.schema) as sink:
                for batch in reader:
                    sink.write_batch(batch)
        daily = coverage.merge(summary, on=["trade_date", "batch_id"], suffixes=("", "_summary"))
        manifest_after = bar_manifest(root)
        stale = int(coverage.stale.sum())
        latest = str(coverage.trade_date.max().date())
        report.update(
            status="complete", completed_at=datetime.now(timezone.utc),
            elapsed_seconds=round(time.monotonic() - started, 2),
            peak_rss_mib=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
            published_batches_including_warmup=len(batches), published_days=len(coverage),
            first_date=str(coverage.trade_date.min().date()), last_date=latest,
            event_rows=len(events), exception_rows=len(exceptions), stale_days=stale,
            limit_up_rows=len(up), consecutive_known=int(up.consecutive_up.notna().sum()),
            consecutive_unknown=int(up.consecutive_up.isna().sum()),
            excluded_legacy_ipo_first_days=boundary_count,
            gap_bridged_up_events=int(up.consecutive_gap_sessions.gt(0).sum()),
            gap_bridged_symbols=int(up.loc[up.consecutive_gap_sessions.gt(0), "symbol"].nunique()),
            count_mismatches=count_mismatches,
            reference_basis_counts=reference_counts,
            missing_calendar_sessions=missing_dates, unexpected_sessions=extra_dates,
            raw_bars_unchanged=manifest_before == manifest_after,
            latest_coverage=json.loads(
                coverage.tail(1).to_json(orient="records", date_format="iso")),
            latest_consecutive=json.loads(
                streaks.tail(1).to_json(orient="records", date_format="iso")),
            exceptions_by_reason=exceptions.groupby(["kind", "reason"]).size().rename("rows")
                .reset_index().sort_values("rows", ascending=False).to_dict("records"),
            limitations=[
                "Scope is the stock histories stored in this pool, not a historical universe.",
                "Missing listing, suspension, ST or reference-price evidence remains unknown.",
                "Stored historical ST values retain their existing provenance limitations.",
                "Missing bars after a known up streak pause counting under the user-defined v7 "
                "policy; consecutive_gap_sessions records this assumption.",
                "Unknown pre-gap streaks, invalid bars and unresolved rules remain unknown.",
                "v8 excludes confirmed legacy main-board IPO first days from streak counting; "
                "the first-day price-limit classification stays unknown.",
            ],
        )
        if (stale or count_mismatches or missing_dates or extra_dates or len(daily) != len(coverage)
                or manifest_before != manifest_after):
            report["status"] = "validation_failed"
            raise RuntimeError("Derived publication validation failed; inspect report.json")
        write_json(output / "report.json", report)
        logging.info("COMPLETE: %s", json.dumps(report, ensure_ascii=False, default=str))
    except BaseException as exc:
        report.update(status="failed", error=repr(exc), stopped_at=datetime.now(timezone.utc))
        write_json(output / "report.json", report)
        raise


if __name__ == "__main__":
    main()
