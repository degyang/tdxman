# FW-04 complete replica verification

This auxiliary script verifies a closed copied artifact against the main
developer's trusted manifest and accepted `derived-result.json`. It does not
prepare, transfer, recalculate, publish, or install data. It uses only the Python
standard library and never writes the source database or opens the catalog for
mutation. No existing Jakarta data was used to develop or test this script.

## Interface

```python
verify(root, manifest_path, integrity_evidence_path) -> dict
```

Import `scripts/ops/verify_data_replica.py` or invoke it directly:

```sh
python scripts/ops/verify_data_replica.py \
  --root /absolute/path/to/closed-replica \
  --manifest /absolute/project/.local/reports/replica-manifest.json \
  --integrity-evidence /absolute/project/.local/reports/accepted-derived-result.json \
  --report /absolute/project/.local/reports/replica-verification.json
```

All CLI arguments are required. A successful invocation returns exit status 0
and writes `status: verified`; a rejected or interrupted invocation returns
nonzero and reports `status: failed`. Before hashing, the CLI replaces any old
report with `status: running`, so interruption cannot leave an old success
looking like the current result. An unwritable/unsafe report path is an error.
Always check the process exit status as well as the current report.

Reports must be separate `.json` files outside the data root. A report under
the data root's `_reports/` directory is also rejected. The CLI refuses to overwrite manifest/evidence inputs
or a hardlinked report target. Report writes are the only authorized
filesystem output; data files are never deleted or repaired.

## Required inputs and coverage

Manifest schema:

```json
{
  "format_version": 1,
  "files": [
    {"path": "stocks.sqlite", "bytes": 123, "sha256": "64 hexadecimal characters"},
    {"path": "catalog.duckdb", "bytes": 456, "sha256": "64 hexadecimal characters"},
    {"path": "lake/example.parquet", "bytes": 789, "sha256": "64 hexadecimal characters"}
  ],
  "bytes": 1368
}
```

Paths must be canonical relative POSIX paths. Duplicate paths, absolute paths,
Windows drive paths, backslashes, `.`/`..` components and NULs are rejected.
Sizes and total bytes must be nonnegative integers, and each SHA-256 must have
64 hex characters. Both databases are mandatory and the lake directory must
exist. The manifest must include every lake file, even when a control input
happens to be inside the lake.

Inventory covers the replica root and rejects symlink files/directories/root
ancestors, nonregular files, root escape, missing entries and unlisted business
files. Every regular file except the two named SQLite sidecars requires a
manifest entry; no report directory is ignored. Keep manifest, evidence and
reports in `.local/reports` outside the data root. The sender currently reports
8158 business files; that count is not hardcoded into the verifier.

Existing regular `stocks.sqlite-wal` and `stocks.sqlite-shm` are recognized
sidecars and must not be manifest entries. WAL must be empty. Existing sidecars
are preserved and their identities/content stats must remain stable; no
sidecars may appear or disappear during verification. Transfer excludes both
sidecars; verification also accepts the explicitly named existing sidecars.
The sender must
checkpoint and close the writer before packaging the database.

The trusted source acceptance evidence must contain:

```text
database_sha256: full accepted stock SHA-256
database_bytes: accepted stock file size
integrity_check: "ok"
daily_calculation_complete: true
rows: nonnegative integer counts for exactly the four stock tables
```

The bar, feature and summary evidence counts must be nonzero. These counts are
source acceptance facts, not locally recomputed numbers. This verifies byte
identity to the supplied trusted evidence; it cannot authenticate that evidence
or independently repeat its underlying semantic acceptance.

## Verification and read-only behavior

Each manifest file is hashed in 1 MiB blocks. Before/after file and open
descriptor stats compare device, inode, mode, size, mtime and ctime; atime is
excluded because reading may update it. A second inventory detects changes
after individual hashing, new business files and sidecar changes. Control
inputs and root identity are also checked again before success.

Only after the full stock checksum and size match both the manifest and source
evidence does the script reuse `integrity_check: ok` and source row counts. The
report explicitly records `source_reused_after_sha256_match`,
`local_full_integrity_run: false` and `local_row_counts_run: false`. It never
executes `PRAGMA integrity_check` or `SELECT count(*)`.

SQLite opens with `mode=ro&immutable=1`, `query_only=ON`, a 16 MiB cache and
disabled mmap. It verifies `user_version=1`, exactly `daily_bars`,
`daily_features`, `corporate_actions`, `market_daily_summary`, and columns
required for its reads. Three primary-key-ordered queries read at most five
rows each: bars, features, and daily (`frequency='D'`) summaries. Missing sample
rows, bad structure/version or any read error reject the copy. Immutable access
does not create or update WAL/SHM. Catalog and lake files receive checksum/size
verification; this script does not open DuckDB or repeat lake content semantics.

## Independent fixture evidence

Only the new test file is run:

```sh
PYTHONPATH=/home/ubuntu/Services/tdxman-fw03-reference-factors/src \
  /home/ubuntu/Services/tdxman/.venv/bin/python -m pytest -q \
  tests/unit/test_data_replica_verification.py

/home/ubuntu/Services/tdxman/.venv/bin/ruff check \
  scripts/ops/verify_data_replica.py tests/unit/test_data_replica_verification.py

git diff --check
```

Fixture cases cover success, bounded SQL and evidence reuse, unchanged source
stats/no new sidecars, size/hash/total mismatch, extra business files, duplicate
paths, traversal/absolute paths, symlinks, nonempty WAL, mismatched/incomplete
evidence, invalid schema/version/empty samples, changes during hashing and
after hashing, CLI failures replacing stale success, and protection of source
artifacts from report writes, including refusal to report inside the data root.
All inputs are small files in pytest temporary
directories; the catalog/lake payloads are opaque checksum fixtures. No
existing tests or existing remote data were read or run for this task.

Validation on 2026-09-28: **40 passed in 1.43s** for this new test file;
Ruff passed and `git diff --check` passed. These are fixture results, not
acceptance evidence for the complete transferred artifact.

Actual whole-copy acceptance awaits the main developer's closed transferred
artifact and authoritative manifest/evidence. Data organization, transfer,
API/Fundwise work and final integration remain with the main developer.
