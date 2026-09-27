#!/usr/bin/env python3
"""Stream every raw column to profile complete-schema date bounds at each scale."""

import json
import re
from pathlib import Path

from bench import LAB, connect, emit, file_sha256, literal, measured, provenance

WINDOWS = {
    "day": ("2026-09-24", "2026-09-24"),
    "60days": ("2026-07-27", "2026-09-24"),
    "five_years": ("2021-09-25", "2026-09-24"),
}


def run():
    baseline_counts = {}
    for factor in (1, 2, 5):
        root = LAB / f"prepared-{factor}x"
        with connect(root, True) as c:
            segments = c.execute("PRAGMA storage_info('dg03_daily')").fetchdf()
            groups = {}
            for row in segments[
                (segments.column_name == "trade_date") & (segments.segment_type == "DATE")
            ].itertuples():
                low, high = re.search(r"Min: ([0-9-]+), Max: ([0-9-]+)", row.stats).groups()
                prior = groups.setdefault(row.row_group_id, [low, high, 0])
                prior[0], prior[1], prior[2] = (
                    min(prior[0], low),
                    max(prior[1], high),
                    prior[2] + row.count,
                )
            for label, (start, end) in WINDOWS.items():
                path = LAB / f"growth_{factor}x_full_schema_{label}.json"
                c.execute("PRAGMA enable_profiling='json'")
                c.execute(f"PRAGMA profiling_output={literal(path)}")

                def stream():
                    reader = c.execute(
                        "SELECT * FROM dg03_daily WHERE trade_date BETWEEN ? AND ?",
                        [start, end],
                    ).fetch_record_batch(65536)
                    assert len(reader.schema) == 42
                    count = sum(len(batch) for batch in reader)
                    return {"rows": count, "fields": len(reader.schema), "batch_rows": 65536}

                result = measured(f"growth_{factor}x_full_schema_{label}", stream, root)
                c.execute("PRAGMA disable_profiling")
                if factor == 1:
                    baseline_counts[label] = result["rows"]
                assert result["rows"] == baseline_counts[label]
                data = json.loads(path.read_text())
                scans = []

                def walk(node):
                    if "SCAN" in node.get("operator_name", ""):
                        scans.append(
                            {
                                k: node.get(k)
                                for k in (
                                    "operator_name",
                                    "operator_rows_scanned",
                                    "operator_cardinality",
                                    "extra_info",
                                )
                            }
                        )
                    for child in node.get("children", []):
                        walk(child)

                walk(data)
                eligible = [g for g in groups.values() if g[0] <= end and g[1] >= start]
                emit(
                    "full_schema_scan_bound",
                    factor=factor,
                    workload=label,
                    returned_rows=result["rows"],
                    columns=42,
                    eligible_groups=len(eligible),
                    row_upper_bound=sum(g[2] for g in eligible),
                    total_rows=sum(g[2] for g in groups.values()),
                    scans=scans,
                    profile=str(path),
                )


if __name__ == "__main__":
    provenance()
    emit("growth_bounds_source", path=__file__, sha256=file_sha256(Path(__file__)))
    measured("phase_growth_bounds", run, LAB)
