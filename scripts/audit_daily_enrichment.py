"""Audit production indicator coverage and raw-field changes against a backup."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from aspool.enrichment import METRICS
from aspool.pool import pool_lock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    output = args.report_dir
    approved = json.loads((output / "price-repair.json").read_text())
    approved_dates = {entry["date"] for entry in approved["evidence"]}
    enrichment = json.loads(
        (args.root / "reports/maintenance/enrichment-latest.json").read_text())
    symbols = {entry["symbol"] for entry in enrichment["results"]}
    before_root = output / "daily.before"
    after_root = args.root / "lake/bars/daily"
    coverage, sources = Counter(), Counter()
    raw_changes, unexpected, latest = [], [], []
    with pool_lock(args.root):
        for before in sorted(before_root.rglob("*.parquet")):
            relative = before.relative_to(before_root)
            after = after_root / relative
            if not after.exists():
                unexpected.append({"path": str(relative), "reason": "file_missing"})
                continue
            original = pq.ParquetFile(before).read(
                columns=["trade_date", "open", "high", "low", "close", "volume", "amount"])
            current = pq.ParquetFile(after).read()
            if not original["trade_date"].equals(current["trade_date"]):
                unexpected.append({"path": str(relative), "reason": "date_axis_changed"})
                continue
            for field in ("open", "high", "low", "close", "volume", "amount"):
                left = original[field].cast(pa.float64())
                right = current[field].cast(pa.float64())
                changed = pc.or_(
                    pc.not_equal(pc.is_valid(left), pc.is_valid(right)),
                    pc.fill_null(pc.not_equal(left, right), False),
                )
                count = pc.sum(changed).as_py() or 0
                if count:
                    dates = current["trade_date"].filter(changed).to_pylist()
                    raw_changes.append({"path": str(relative), "field": field, "rows": count})
                    if ("symbol=302132" not in str(relative)
                            or field in {"volume", "amount"}
                            or any(str(d) not in approved_dates for d in dates)):
                        unexpected.append({"path": str(relative), "field": field, "rows": count})
            partitions = dict(part.split("=", 1) for part in relative.parts if "=" in part)
            if f"{partitions.get('symbol')}.{partitions.get('market')}" not in symbols:
                continue
            bounded = current.filter(pc.and_(
                pc.greater_equal(current["trade_date"], pa.scalar(args.start)),
                pc.less_equal(current["trade_date"], pa.scalar(args.end)),
            ))
            years = pc.year(bounded["trade_date"])
            for year in range(args.start.year, args.end.year + 1):
                table = bounded.filter(pc.equal(years, year))
                coverage[(year, "rows")] += len(table)
                for field in METRICS:
                    if field not in table.column_names:
                        continue
                    values = table[field].cast(pa.float64())
                    if field == "turnover_rate" and "turnover" in table.column_names:
                        values = pc.coalesce(values, table["turnover"].cast(pa.float64()))
                    valid = pc.fill_null(pc.is_finite(values.cast(pa.float64())), False)
                    if field not in {"pct_chg"}:
                        valid = pc.and_(valid, pc.fill_null(pc.greater_equal(values, 0), False))
                    coverage[(year, field)] += pc.sum(valid).as_py() or 0
            for field in METRICS:
                source = field + "_source"
                if source in bounded.column_names:
                    for entry in pc.value_counts(bounded[source]).to_pylist():
                        sources[(field, entry["values"] or "unmarked")] += entry["counts"]
            last = bounded.filter(pc.equal(bounded["trade_date"], pa.scalar(args.end)))
            fields = [k for k in ("symbol", "market", "trade_date", *METRICS,
                                  *(m + "_source" for m in METRICS)) if k in last.column_names]
            latest.extend(last.select(fields).to_pylist())
    rows = []
    for year in range(args.start.year, args.end.year + 1):
        total = coverage[(year, "rows")]
        for field in METRICS:
            count = coverage[(year, field)]
            rows.append(dict(year=year, field=field, stored_rows=total, valid=count,
                             missing=total-count, valid_ratio=count/total if total else None))
    pd.DataFrame(rows).to_csv(output / "field_coverage.csv", index=False)
    pd.DataFrame(latest).to_csv(output / "latest_indicators.csv", index=False)
    totals = {field: sum(coverage[(y, field)] for y in range(args.start.year, args.end.year+1))
              for field in ("rows", *METRICS)}
    report = dict(status="passed" if not unexpected else "failed", totals=totals,
                  requested_stock_count=len(symbols),
                  raw_changes=raw_changes, unexpected_raw_changes=unexpected,
                  approved_price_repair_rows=approved["corrected_price_rows"],
                  source_counts=[dict(field=f, source=s, rows=n) for (f, s), n in sources.items()],
                  denominator="Stored stock-day rows; not a complete historical market universe")
    (output / "validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "source_counts"}, ensure_ascii=False))
    if unexpected:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
