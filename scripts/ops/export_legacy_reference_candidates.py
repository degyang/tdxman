"""Keep legacy effective reference candidates as ops evidence, not new source facts."""

import argparse
import json
from pathlib import Path

import duckdb
import pyarrow.parquet as pq
from snapshot_development_data import digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--target-root", type=Path, required=True)
    args = parser.parse_args()
    source, target = args.source_root.resolve(), args.target_root.resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError("Source and target must be separate trees")
    original = json.loads((source / "development-snapshot.json").read_text())
    expected = next(row for row in original["files"] if row["path"] == "catalog.duckdb")
    if digest(source / "catalog.duckdb") != expected["sha256"]:
        raise ValueError("Source catalog changed")
    output = target / "_reports/legacy-reference-candidates.parquet"
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(source / "catalog.duckdb"), read_only=True) as conn:
        conn.execute("SET threads=1")
        conn.execute("SET memory_limit='256MB'")
        frame = conn.execute("""SELECT r.* EXCLUDE (batch_id),
            st.trade_date IS NOT NULL AS was_stale
            FROM daily_limit_references r
            JOIN daily_limit_publication p USING (trade_date,batch_id)
            LEFT JOIN daily_limit_staleness st ON st.trade_date=r.trade_date
            ORDER BY r.symbol,r.trade_date""").fetch_arrow_table()
    temporary = output.with_suffix(".tmp")
    pq.write_table(frame, temporary, compression="zstd")
    if not pq.ParquetFile(temporary).read().equals(frame):
        raise ValueError("Reference artifact roundtrip differs")
    temporary.replace(output)
    result = dict(path=output.relative_to(target).as_posix(), rows=len(frame),
                  bytes=output.stat().st_size, sha256=digest(output),
                  status="legacy_calculation_evidence_not_authoritative_source",
                  source_manifest_sha256=digest(source / "development-snapshot.json"))
    (target / "_reports/legacy-reference-candidates.json").write_text(
        json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
