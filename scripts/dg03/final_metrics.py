#!/usr/bin/env python3
"""Validate completed new monthly workloads and render a compact ADR table."""

import json
import statistics

from bench import LAB


def main():
    rows = [json.loads(line) for line in (LAB / "samples.jsonl").read_text().splitlines()]

    def one(name, **matching):
        found = [
            r for r in rows if r["name"] == name and all(r.get(k) == v for k, v in matching.items())
        ]
        assert found, (name, matching)
        return found[-1]

    def samples(prefix):
        selected = [r for r in rows if r["name"].startswith(prefix) and r.get("status") == "passed"]
        assert len(selected) == 3, (prefix, len(selected))
        return statistics.median(r["result"].get("api_seconds", r["seconds"]) for r in selected)

    metrics = []
    for factor in [1, 2, 5]:
        built = one("monthly_built", factor=factor)
        assert built["rows"] == factor * 17862387
        reads = {
            name: samples(
                f"monthly_{factor}x_public_{name}" + ("_threads2_" if factor == 1 else "_")
            )
            for name in ["day", "60days", "single"]
        }
        writes = {
            name: one(f"monthly_{factor}x_{name}")
            for name in [
                "fullmarket_insert",
                "fullmarket_noop",
                "delete",
                "backfill_two_months",
                "backfill_noop",
                "checkpoint",
            ]
        }
        assert all(r["status"] == "passed" for r in writes.values())
        corrections = [one(f"monthly_{factor}x_correction_{i}") for i in range(12)]
        noops = [one(f"monthly_{factor}x_noop_{i}") for i in range(12)]
        assert all(r["status"] == "passed" for r in corrections + noops)
        assert all(
            r["io_delta"]["write_bytes"] == 0
            for r in noops + [writes["fullmarket_noop"], writes["backfill_noop"]]
        )
        scans = [
            one("monthly_scan_bounds", factor=factor, workload=name)
            for name in ["day", "60days", "fiveyears"]
        ]
        single = one("monthly_single_scan_bound", factor=factor)
        final = one("monthly_write_state", factor=factor)
        assert final["revision"] == [[1, 15]] and final["changes"] == 7299
        metrics.append(
            dict(
                factor=factor,
                rows=built["rows"],
                initial_bytes=built["disk"],
                final_bytes=final["root_disk"],
                read_seconds=reads,
                fullmarket_seconds=writes["fullmarket_insert"]["seconds"],
                fullmarket_write_bytes=writes["fullmarket_insert"]["io_delta"]["write_bytes"],
                correction_median_seconds=statistics.median(r["seconds"] for r in corrections),
                correction_write_bytes=statistics.median(
                    r["io_delta"]["write_bytes"] for r in corrections
                ),
                noop_median_seconds=statistics.median(r["seconds"] for r in noops),
                noop_write_bytes=0,
                delete_seconds=writes["delete"]["seconds"],
                backfill_seconds=writes["backfill_two_months"]["seconds"],
                scans=scans,
                single=single,
                all_assertions_passed=True,
            )
        )
    (LAB / "monthly-final-metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
    lines = [
        "| 按月候选（公开读2线程/512MB，写1线程/1GB） | 1× | 2× | 5× |",
        "|---|---:|---:|---:|",
    ]
    for label, key in [
        ("原始行数", "rows"),
        ("初始目录字节", "initial_bytes"),
        ("更新后含历史目录字节", "final_bytes"),
        ("全市场新增秒", "fullmarket_seconds"),
        ("全市场新增内核写字节", "fullmarket_write_bytes"),
        ("12次修订中位秒", "correction_median_seconds"),
        ("修订内核写字节中位", "correction_write_bytes"),
        ("12次no-op中位秒", "noop_median_seconds"),
        ("no-op写字节", "noop_write_bytes"),
        ("删除秒", "delete_seconds"),
        ("两月回补秒", "backfill_seconds"),
    ]:
        values = [m[key] for m in metrics]
        lines.append(
            "| "
            + label
            + " | "
            + " | ".join(f"{v:,.3f}" if isinstance(v, float) else f"{v:,}" for v in values)
            + " |"
        )
    for name, label in [
        ("day", "公开单日中位秒"),
        ("60days", "公开60日中位秒"),
        ("single", "公开单证券全史中位秒"),
    ]:
        lines.append(
            "| "
            + label
            + " | "
            + " | ".join(f"{m['read_seconds'][name]:.3f}" for m in metrics)
            + " |"
        )
    for name, label in [
        ("bound_files", "单证券绑定文件数"),
        ("row_group_upper_bound", "单证券42列一次扫描行组上界"),
    ]:
        lines.append(
            "| " + label + " | " + " | ".join(f"{m['single'][name]:,}" for m in metrics) + " |"
        )
    (LAB / "monthly-final-table.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
