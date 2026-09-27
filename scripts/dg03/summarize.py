#!/usr/bin/env python3
"""Summarize append-only raw evidence without touching any experiment database."""

import json
import re
import statistics
from collections import defaultdict

from bench import LAB, file_sha256


def main():
    rows = [json.loads(line) for line in (LAB / "samples.jsonl").read_text().splitlines()]
    groups = defaultdict(list)
    for row in rows:
        match = re.fullmatch(
            r"((?:growth_\dx(?:_bounded)?|facts_\dx(?:_bounded)?|more_securities)_public_.+)_\d+", row["name"]
        )
        if match:
            groups[match.group(1)].append(row)
    summary = {
        "input_sha256": file_sha256(LAB / "samples.jsonl"),
        "public_samples": {
            name: {
                "n": len(values),
                "seconds": [v["seconds"] for v in values],
                "median_seconds": statistics.median(v["seconds"] for v in values),
                "api_seconds": [v.get("result", {}).get("api_seconds") for v in values],
                "peak_sampled_rss": max(v.get("peak_sampled_rss", 0) for v in values),
                "maxrss_process_highwater": max(v["maxrss_bytes"] for v in values),
                "rows": [v.get("result", {}).get("rows") for v in values],
            }
            for name, values in sorted(groups.items())
        },
        "failures": [r for r in rows if r.get("status") == "failed"],
        "physical_bounds": [r for r in rows if "physical_" in r["name"]],
        "process_and_recovery": [
            r
            for r in rows
            if r["name"]
            in {
                "crash_uncommitted",
                "crash_committed",
                "native_process_conflict",
                "coordinated_writer_reader",
                "concurrent_readers",
                "hold_write_then_writer",
                "hold_read_then_writer",
                "snapshot_restore",
            }
        ],
        "growth_sizes": [
            r
            for r in rows
            if r["name"] in {"growth_size", "more_securities", "facts_growth_population"}
        ],
    }
    (LAB / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                "path": str(LAB / "summary.json"),
                "sample_groups": len(groups),
                "failures_retained": len(summary["failures"]),
            }
        )
    )


if __name__ == "__main__":
    main()
