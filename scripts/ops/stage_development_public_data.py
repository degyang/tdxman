"""Stage existing ETF/index/public data beside the isolated migrated stock database."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

import duckdb
from snapshot_development_data import digest

PUBLIC_TABLES = (
    "universe", "security_lifecycle", "security_calendar", "index_coverage", "coverage",
)


def stage(source, target):
    source, target = source.resolve(), target.resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError("Source and target must be separate trees")
    manifest_path = source / "development-snapshot.json"
    manifest = json.loads(manifest_path.read_text())
    expected = {entry["path"]: entry for entry in manifest["files"]}
    if digest(source / "catalog.duckdb") != expected["catalog.duckdb"]["sha256"]:
        raise ValueError("Source catalog differs from frozen input")
    reports = target / "_reports"
    reports.mkdir(parents=True, exist_ok=True)
    final_manifest = reports / "public-data-manifest.json"
    if final_manifest.exists():
        completed = json.loads(final_manifest.read_text())
        if completed["source_manifest_sha256"] != digest(manifest_path):
            raise ValueError("Public-domain inputs changed")
        for entry in completed["files"]:
            if digest(target / entry["path"]) != entry["sha256"]:
                raise ValueError(f"Previously staged file changed: {entry['path']}")
        return completed
    if (target / "catalog.duckdb").exists():
        raise FileExistsError("Target catalog already exists without completion evidence")
    catalog = duckdb.connect(str(source / "catalog.duckdb"), read_only=True)
    catalog.execute("SET threads=1")
    catalog.execute("SET memory_limit='256MB'")
    classes = {f"{code}.{market}": asset for code, market, asset in catalog.execute(
        "SELECT symbol,market,asset_type FROM universe").fetchall()}
    entries = []
    for relative, entry in expected.items():
        parts = Path(relative).parts
        if parts[0] != "lake":
            continue
        if parts[:3] == ("lake", "bars", "daily"):
            if len(parts) < 6:
                raise ValueError(f"Unsupported daily path: {relative}")
            identity = f"{parts[4][7:]}.{parts[3][7:]}"
            if classes.get(identity) != "etf":
                continue
        output = target / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            if digest(output) != entry["sha256"]:
                raise ValueError(f"Refusing to overwrite differing public data: {relative}")
        else:
            temporary = output.with_name(output.name + ".copying")
            shutil.copyfile(source / relative, temporary)
            if digest(temporary) != entry["sha256"]:
                raise ValueError(f"Public data checksum mismatch: {relative}")
            temporary.replace(output)
        entries.append(entry)
    counts = {}
    with tempfile.TemporaryDirectory(prefix="public-catalog-", dir=reports) as work:
        built = Path(work) / "catalog.duckdb"
        conn = duckdb.connect(str(built))
        try:
            conn.execute("SET threads=1")
            conn.execute("SET memory_limit='256MB'")
            for table in PUBLIC_TABLES:
                ddl = catalog.execute(
                    "SELECT sql FROM duckdb_tables() WHERE table_name=?", [table]
                ).fetchone()[0]
                conn.execute(ddl)
                query = ("SELECT c.* FROM coverage c JOIN universe u ON "
                         "c.symbol=u.symbol AND c.market=u.market WHERE u.asset_type='etf'"
                         if table == "coverage" else f"SELECT * FROM {table}")
                frame = catalog.execute(query).fetch_arrow_table()
                conn.register("incoming", frame)
                conn.execute(f"INSERT INTO {table} SELECT * FROM incoming")
                restored = conn.execute(f"SELECT * FROM {table}").fetch_arrow_table()
                # Compare as unordered rows, preserving every field and NULL.
                source_rows = sorted(frame.to_pylist(), key=lambda row: repr(list(row.values())))
                target_rows = sorted(restored.to_pylist(), key=lambda row: repr(list(row.values())))
                if source_rows != target_rows:
                    raise ValueError(f"Public table verification failed: {table}")
                counts[table] = len(frame)
                conn.unregister("incoming")
            conn.execute("CHECKPOINT")
        finally:
            conn.close()
        built.replace(target / "catalog.duckdb")
    catalog.close()
    entries.append(dict(path="catalog.duckdb", bytes=(target / "catalog.duckdb").stat().st_size,
                        sha256=digest(target / "catalog.duckdb")))
    result = dict(source_manifest_sha256=digest(manifest_path), tables=counts, files=entries,
                  bytes=sum(entry["bytes"] for entry in entries),
                  stock_parquet_copied=False, legacy_publication_tables_copied=False)
    temporary = final_manifest.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, indent=2) + "\n")
    temporary.replace(final_manifest)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--target-root", type=Path, required=True)
    args = parser.parse_args()
    result = stage(args.source_root, args.target_root)
    print(json.dumps({k: v for k, v in result.items() if k != "files"}), flush=True)


if __name__ == "__main__":
    main()
