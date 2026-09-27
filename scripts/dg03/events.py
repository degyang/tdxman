#!/usr/bin/env python3
"""New candidate's actual five-year bounded public iterator; reuse DG02 baseline."""

import hashlib
import json
import resource
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
from bench import LAB, SNAPSHOT, connect, emit, measured, provenance

from aspool.dg03_candidate import CandidatePool


def run(pool=None, threads=2):
    pool = pool or CandidatePool(LAB / "candidate")
    parameters = dict(
        start="2021-09-24",
        end="2026-09-24",
        close_limit_up=True,
        min_consecutive_up=1,
        batch_days=7,
        max_rows=25000,
        threads=threads,
        memory_limit="512MB",
        temp_directory=str(LAB),
    )
    count = batches = max_rows = nulls = checked = 0
    digest = hashlib.sha256()
    with (
        connect(SNAPSHOT, True) as reference,
        pool.iter_limit_events_with_amount(**parameters) as reader,
    ):
        temp = Path(reader._temp.name)
        for frame in reader:
            # No old Parquet iterator benchmark. Reuse already-verified source facts/catalog;
            # compare public event metadata directly against the immutable publication catalog.
            columns = [name for name in frame.columns if name != "amount"]
            replacements = {
                "rule_version": "b.rule_version",
                "computed_at": "b.computed_at",
                "published_at": "p.published_at",
                "stale": "(s.trade_date IS NOT NULL)",
                "stale_reason": "s.reason",
            }
            select = ",".join(replacements.get(n, "e." + n) + " AS " + n for n in columns)
            expected = reference.execute(
                f"""SELECT {select} FROM daily_limit_events e
                JOIN daily_limit_publication p USING(trade_date,batch_id)
                JOIN daily_limit_batches b USING(batch_id)
                LEFT JOIN daily_limit_staleness s ON s.trade_date=e.trade_date
                WHERE e.trade_date BETWEEN ? AND ? AND e.close_limit_up AND e.consecutive_up>=1
                ORDER BY e.trade_date,e.symbol""",
                [frame.trade_date.min().date(), frame.trade_date.max().date()],
            ).fetchdf()
            pd.testing.assert_frame_equal(frame[columns], expected, check_flags=False)
            if batches % 8 == 0 and len(frame):
                row = frame.iloc[0]
                code, market = row.symbol.split(".")
                table = (
                    pq.ParquetFile(
                        SNAPSHOT / f"lake/bars/daily/market={market}/symbol={code}/bars.parquet"
                    )
                    .read(columns=["trade_date", "amount"])
                    .to_pandas()
                )
                values = table[table.trade_date == row.trade_date.date()].amount
                assert len(values) == 1 and values.iloc[0] == row.amount
                checked += 1
            digest.update(pd.util.hash_pandas_object(frame, index=False).values.tobytes())
            count += len(frame)
            max_rows = max(max_rows, len(frame))
            nulls += int(frame.amount.isna().sum())
            emit(
                "candidate_event_batch",
                batch=batches,
                rows=len(frame),
                start=frame.trade_date.min(),
                end=frame.trade_date.max(),
                catalog_fields_exact=True,
            )
            batches += 1
    assert reader.completed and reader.closed and not temp.exists()
    baseline = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "docs/evidence/data-remediation/20260927-dg02/p0-five-year.json"
        ).read_text()
    )
    assert count == baseline["runs"][0]["rows"] == 82772
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    assert rss <= 2 * 1024**3
    return dict(
        rows=count,
        batches=batches,
        max_batch_rows=max_rows,
        null_amounts=nulls,
        original_parquet_amount_samples=checked,
        all_raw_amount_values_reused_from="phase_parity",
        event_digest=digest.hexdigest(),
        process_highwater_rss=rss,
        closed=reader.closed,
        completed=reader.completed,
        temp_removed=not temp.exists(),
    )


if __name__ == "__main__":
    provenance()
    measured("candidate_public_events_five_year", run, LAB)
