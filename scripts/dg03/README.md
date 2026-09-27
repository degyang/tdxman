# DG-03 isolated candidate experiment

This is an explicit research backend. Production `DataPool`, `DailyStorage`, CLI,
DG04 and the production data root never select it. `CandidatePool(root)` requires
an independent schema manifest and accepts only validated, explicit change sets;
it is not a replacement for network ingestion or the DG01 merge/quality API.

The immutable input and output roots are fixed in `bench.py`. Run in the DG03
worktree's independent `.venv`, installed with `/tmp/tdxman-dg00-constraints.txt`.
The experiment deliberately copies the complete catalog, including every original
constraint, dated fact, publication, and source. It stores all physical Parquet
columns plus independent path keys. Original Arrow schema metadata is retained in
`source-schema.json`; public readers use canonical path identities without losing
physical source fields. The frozen public reader is AST-compared to `be60d78` in
the new tests, allowing only the storage binding and SQL overlay substitutions. It retains validation, row limits, metadata and dated overlay.

Obtain the coordinator's heavy-work window before full phases. The actual final
layout in this run is `month-clustered`; `candidate` retains the initial date-sorted
layout as evidence. New `build()` defaults to month/security ordering. Use an
explicit `DG03_CANDIDATE=month-clustered` for the commands below in this lab.

```sh
export DG03_CANDIDATE=month-clustered
# Initial build/parity/reads already passed; do not repeat just for handoff.
# Split construction from performance sampling. Preparation alone may run in
# parallel with DG04 only under its coordinator's explicit resource approval.
nice -n 19 ionice -c 3 env DG03_THREADS=1 DG03_MEMORY_LIMIT=1GB \
  DG03_PREP_RSS_LIMIT=3221225472 .venv/bin/python scripts/dg03/bench.py prepare-growth --factor 1
# Repeat preparation sequentially with --factor 2 and --factor 5.
# 5x reuses the committed 2x prefix, preserving all 42 raw/key columns.
# An interrupted preparation supports --resume after diagnosis, never overwrite.
nice -n 19 ionice -c 3 env DG03_THREADS=1 DG03_MEMORY_LIMIT=1GB \
  DG03_PREP_RSS_LIMIT=3221225472 .venv/bin/python scripts/dg03/bench.py prepare-securities
# The following sampling phases require the exclusive resource window.
.venv/bin/python scripts/dg03/bench.py growth-samples --factor 1
.venv/bin/python scripts/dg03/bench.py growth-samples --factor 2
.venv/bin/python scripts/dg03/bench.py growth-samples --factor 5
.venv/bin/python scripts/dg03/bench.py securities-samples
.venv/bin/python scripts/dg03/bench.py mutations
.venv/bin/python scripts/dg03/bench.py recovery
.venv/bin/python scripts/dg03/bench.py sparse
```

Phases creating roots fail if those roots already exist. Retain failed directories
and logs; do not silently overwrite evidence. JSONL samples append to
`/home/ubuntu/aspool-labs/dg03-20260927/samples.jsonl`, profiles and large databases
stay beside them. Each phase records commit, exact source hashes, interpreter,
imports, versions and the source manifest hash. No phase reruns DG00–02 suites.
Run only `pytest tests/unit/test_dg03_candidate.py` for this candidate's new tests.

Interpretation limits:

- Three warm/mixed-cache samples are not cold-start or P95 evidence. Five-year
  public reads use 60-natural-day windows because the 500,000-row guard is kept.
- SQL operator rows-scanned is a counter, not unique physical rows or device I/O.
  Date segment statistics give a conservative eligible-row bound; JSON profiles,
  `/proc/self/io`, RSS high-water marks and sampled file sizes are separate data.
- Kernel `write_bytes` includes WAL/checkpoint writes but is not SSD wear; 100 ms
  disk sampling may miss shorter temporary peaks. `maxrss_bytes` is a process
  high-water mark; `rss_start/end` and `peak_sampled_rss` delimit each operation.
  SQL memory limits are not RSS limits. The preparation guard interrupts the
  private connection if sampled process RSS exceeds its explicit threshold.
- History multipliers copy the entire complete schema at 40-year offsets. The
  independent securities scenario doubles internal identities. Both are pressure
  inputs, not forecasts or valid new market histories.
- Mutation logging, revision, coverage and conservative published-date stale
  suffixes share one transaction. Missing merge values are ignored; `clear` and
  `delete` are explicit. Date facts are a distinct `domain='facts'` in the same
  batch. Universe/lifecycle/calendar/source snapshots are retained, not invented
  for new synthetic securities. Recompute/publication remains DG05/DG06 work.
- Process crash tests kill a real subprocess before/after COMMIT and check raw
  amount, revision and audit visibility. A local full-file copy/restore is not
  power-loss, disk-loss or remote-backup proof.

See `docs/architecture-decision.md` for the measured decision and integration gates.

The monthly sort clusters securities inside calendar months. Recent date bounds
therefore prune most historical groups; the code-only ART population is materialized
once before exact market/asset predicates for unbounded single-security reads.
DuckDB 1.5.5 otherwise chooses a full sequential scan when market and code predicates
reach the same scan. Complete dated overlays run in SQL before one pandas conversion;
an all-NULL string compatibility fallback retains the original observable dtype.
These paths are measured separately from baseline Parquet and initial DuckDB layouts.
