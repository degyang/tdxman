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
# Additional copies commit at most 128 securities; progress keys are transactional.
# Completed source-year prefixes from failed earlier strategies remain reusable.
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
.venv/bin/python scripts/dg03/facts_growth.py
```

Phases creating roots fail if those roots already exist. Retain failed directories
and logs; do not silently overwrite evidence. JSONL samples append to
`/home/ubuntu/aspool-labs/dg03-20260927/samples.jsonl`, profiles and large databases
stay beside them. Each phase records commit, exact source hashes, interpreter,
imports, versions and the source manifest hash. No phase reruns DG00–02 suites.
New checks live only in `tests/unit/test_dg03_candidate.py` and
`tests/unit/test_dg03_harness.py`; select affected tests after a fix.

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
therefore prune most historical groups; actual decades are resolved through the code-only ART index, then complete rows
are materialized per decade before exact market/asset predicates for unbounded reads.
DuckDB 1.5.5 otherwise chooses a full sequential scan when market and code predicates
reach the same scan. Complete dated overlays run in SQL before one pandas conversion;
an all-NULL string compatibility fallback retains the original observable dtype.
These paths are measured separately from baseline Parquet and initial DuckDB layouts.

`facts_growth.py` holds the 17.86M raw rows and derived snapshots constant while
expanding only `security_daily_facts` from 410,467 to 820,934 / 2,052,335 rows.
Cold synthetic facts move 40 years per copy and carry an explicit synthetic source;
three complete recent-window reads must remain exactly equal to the real candidate.
`growth_bounds.py` streams all 42 raw/key columns with 65,536-row batches for day,
60-day and five-year windows at each factor, retaining plans and zonemap upper bounds.
A process-local diagnostic wrapper records the actual final overlay plan separately
from ordinary public-call timings. This does not claim derived recursion scales.

## Rejected global-table gate and completed monthly alternative

The tested 5x global table cannot commit a normal whole-market 7,266-row append
atomically under SQL 1GB. Do not split that production-shaped transaction into
partial commits or raise the budget. `native_remaining.py` continues only the
previously unexecuted point-update/no-op/checkpoint work after that failure;
`growth-5x-writes.log` and `failed-5x-write-state.json` retain its rollback evidence.

The final unbounded native single-security path first resolves actual decades
through a narrow ART date projection, then materializes complete requested rows
inside those date bounds. `history_profile.py` records the actual statements:
its 66.1M scan-counter sum at 5x demonstrates that passing memory limits does not
mean constant single-security physical I/O. `read_samples.py --factor 1|2|5
--workload single_history` can verify only that changed path.

`MonthlyPool(root)` in `dg03_monthly.py` is an independent alternative. Its
immutable full-schema monthly files and relative-path catalog manifest preserve
all original raw fields and catalog constraints. Publication copies the complete
catalog and atomically replaces it under `pool_lock`; this is deliberately costly
and is not a production integration. Per-month uniqueness and nonoverlapping dates
replace the global raw table constraint; the original catalog constraints remain.
The public reader, ETF error handling, amount batches and revision checks remain.
Symbol predicates are pushed onto physical keys; a concat-key public filter alone
was measured to cause severe single-history regression.

```sh
export DG03_CANDIDATE=month-clustered
export DG03_THREADS=1
export DG03_MEMORY_LIMIT=1GB
# Execute sequentially after obtaining the shared heavy-work slot.
.venv/bin/python scripts/dg03/monthly_bench.py build --factor 1
.venv/bin/python scripts/dg03/monthly_bench.py build --factor 2
.venv/bin/python scripts/dg03/monthly_bench.py build --factor 5
# Repeat the following per factor, before writes mutate that root.
.venv/bin/python scripts/dg03/monthly_bench.py reads --factor 1
.venv/bin/python scripts/dg03/monthly_bench.py bounds --factor 1
.venv/bin/python scripts/dg03/monthly_bench.py events --factor 1
.venv/bin/python scripts/dg03/monthly_recovery.py
.venv/bin/python scripts/dg03/monthly_bench.py writes --factor 1
.venv/bin/python scripts/dg03/summarize.py
```

Monthly construction keeps immutable `build-manifest.json` metadata. 2x reuses
independent copies of the verified 1x prefix; 5x reuses the verified 2x prefix.
Every copied file SHA is checked, newly shifted cold months receive full-column
bidirectional EXCEPT ALL/type/date/key checks, and synthetic coverage is derived
from actual raw rows. All original physical date/datetime/source extensions stay
unchanged: only logical trade_date moves by 40 years, so synthetic histories are
pressure data, not coherent new market observations. No scenario shares a writable
catalog. Monthly operations run one thread / SQL1GB (public daily512MB) with a
3GiB sampled process RSS interrupt guard in the main monthly harness.

The monthly write phase includes whole-market insert and exact42-column parity,
whole-market no-op, 12 corrections plus no-ops, boundary deletion, two-month
backfill/no-op and checkpoint. It records changed manifest months, rewritten rows,
new file bytes, complete-catalog copy bytes, and unchanged cold-file fingerprints.
A no-op still reads the touched month but never publishes files or revisions.
Catalog copy/checkpoint and old-version retention are real physical costs in the
reported kernel write bytes and directory sizes; monthly pruning does not remove
these costs.

Monthly tests include SIGKILL before/after a two-month+facts publication, all six
state domains, boundary coverage repair, untouched/no-op month identity, facts-only
mixed no-op, stable ETF errors, symbol pushdown and portable relative manifests.
`monthly_recovery.py` copies and hashes every active real part into a new root,
then controls an actual publishing writer and waiting reader with pipes. A
nonempty event iterator must reject the changed revision. This does not implement
orphan GC, remote recovery, power-loss simulation or live monotonic rollback.

Both prototypes remain **not admitted for production migration**. See the ADR for
all measured regressions and missing integration gates. These commands document
reproduction, not permission to repeat already passed runs in this lab: root
creation fails when it already exists, and write phases expect their initial
unmodified dataset. Reuse archived evidence unless a changed path needs validation.
