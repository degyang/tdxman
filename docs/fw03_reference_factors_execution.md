# FW-03a reference factors execution

Date: 2026-09-28. Branch: fw03-reference-factors.

## Delivered

aspool.sqlite_reference_factors provides the first isolated implementation
slice for corporate-action normalization, selected sparse factors, effective
reference pre-close, dated ST, and a finite-key atomic SQLite writer.

- normalize_actions(events) validates ISO dates, normalizes the legacy TDX
  fenhong, songzhuangu, peigu, and peigujia fields to per-share
  cash_dividend_per_share, bonus_shares_per_share,
  rights_shares_per_share, and rights_price, and groups same-day payloads.
  Stable event keys are deduplicated across every calculation path; differing
  values under one key are rejected. Unknown required amounts are errors.
- build_selected_factors(anchors=..., events=..., prior_closes=...,
  through=...) chooses one route. Cumulative anchors must use one explicit,
  known source and their values are copied directly, never cumprodded. Event
  chains also require one explicit, known source. Same-day events are combined
  after stable-key deduplication. A verified no-event interval can create a
  factor 1 baseline at its explicit start and is bounded by its verified end.
  Other factor intervals end before the next factor or at the verified through
  date. No factor means no claim of coverage.
- select_reference_pre_close(...) selects reliable dated reference,
  action-adjusted previous close, then a raw fallback. Use actions_covered=True
  only when the event source confirms the entire interval; an empty event list
  alone does not prove that no action occurred. Conflicting reliable dated
  candidates fail explicitly.
- select_is_st(...) selects reliable dated ST, then a name whose
  name_as_of exactly matches the requested day, otherwise None. Historical
  names are never filled from a current snapshot. Migrated
  raw_fallback:* ST values do not count as dated evidence.
- update_reference_factors(conn, *, symbol, dates, actions=None,
  anchors=None, factor_through=None, dated_pre_close, dated_st,
  actions_covered_dates, verified_no_event_range, *_complete_range) writes
  within a SQLite savepoint; its reads and writes share that transaction
  snapshot. Omitted actions/anchors mean no source update. Supplied rows are
  finite upserts, while `actions_complete_range=(source,start,end)` and
  `anchors_complete_range=(source,start,end)` explicitly authorize replacement
  only inside that source/date range. `factors_complete_range=(start,end)` is
  the explicit factor-cache replacement boundary. This supports partial
  incremental updates without requiring a full symbol history. The writer
  diffs factor rows by effective date, leaves unaffected rows and timestamps
  alone, and counts anchor changes as well as action, factor, and feature
  changes. The caller controls the outer transaction.

The minimal reference-only integration call remains
`update_reference_factors(conn, symbol=..., dates=[...])`; it does not rebuild
or delete selected factor rows. If a caller revokes an existing KNOWN
`pre_close`, that caller must first clear dependent fields according to its
daily-feature contract before invoking this writer.

Reliable stored dated pre-close and ST candidates are included in precedence
selection, and conflicting reliable dated values fail. The previous close is
read only from a valid OHLC bar whose feature status is `TRADED`; NO_TRADE and
INVALID placeholders are skipped. An action-based reference needs an explicit
known action source and evidence of action coverage for the date. Raw fallback
values are not treated as reliable dated evidence.

Example call:

    from aspool.sqlite_stock_store import stock_connection
    from aspool.sqlite_reference_factors import update_reference_factors

    with stock_connection(root, read_only=False) as conn:
        result = update_reference_factors(
            conn,
            symbol="000001.SZ",
            dates=["2024-06-14"],
            actions=[{
                "effective_date": "2024-06-14",
                "category": 1,
                "fenhong": 0.719,
                "songzhuangu": 0,
                "peigu": 0,
                "peigujia": 0,
                "source_key": "tdx:000001:2024-06-14:1",
            }],
            factor_through="2024-06-14",
            actions_covered_dates=["2024-06-14"],
        )
        conn.commit()

For same-day same-category duplicate actions, supply a stable source_key,
event_id, or event_slot; otherwise the writer rejects the input to avoid
silently replacing one event with another. Unsupported event categories and
conflicting reliable source values also fail. Range replacement is opt-in and
bounded by the corresponding explicit complete-range argument. An omitted
action or anchor collection is not interpreted as a complete empty history.

## Evidence

The FW-02 database was accessed only through mode=ro and small indexed reads;
it was not copied, permission-changed, or written. A real source row for
000001.SZ, event date 2024-06-14, had TDX category 1 payload
fenhong=0.7190000057, songzhuangu=0, peigu=0, and preceding close 10.8.
The rule produced reference 10.72809999943. Two real cumulative anchors for
000001.SZ were 130.623 on 2025-10-15 and 134.934 on 2026-06-12; the selected
values remained those exact cumulative levels, with an interval from
2025-10-15 through 2026-06-11 and the next through the verified 2026-09-24
sample boundary. This is a rule sample only, not a historical rewrite or
full-range factor acceptance.

Original implementation evidence above was not repeated. This repair round's
targeted verification used the main project environment and confirmed the
module import resolves to this worktree:

    PYTHONPATH=/home/ubuntu/Services/tdxman-fw03-reference-factors/src \
      /home/ubuntu/Services/tdxman/.venv/bin/python -c \
      'import aspool.sqlite_reference_factors as m; print(m.__file__)'
    /home/ubuntu/Services/tdxman-fw03-reference-factors/src/aspool/sqlite_reference_factors.py

    PYTHONPATH=/home/ubuntu/Services/tdxman-fw03-reference-factors/src \
      /home/ubuntu/Services/tdxman/.venv/bin/python -m pytest -q \
      tests/unit/test_sqlite_reference_factors.py
    11 passed

    /home/ubuntu/Services/tdxman/.venv/bin/ruff check \
      src/aspool/sqlite_reference_factors.py tests/unit/test_sqlite_reference_factors.py
    All checks passed!

    git diff --check
    passed

The 11 module tests cover the original cash dividend, bonus, rights, interval,
ST, rollback and no-op behavior, plus the repair cases: stored dated reference
precedence/conflict, unknown-source rejection, stable event deduplication and
conflicting duplicate rejection, cross-source cumulative-anchor rejection,
explicit factor-1 no-event baseline, finite versus complete-range updates,
incremental anchor counts and preservation of unaffected keys, and skipping
NO_TRADE/INVALID prior bars. Ruff and `git diff --check` passed. The previously
completed M0 migration reconciliation, 10 existing migration cases, full value
reconciliation, integrity checks, and remote hash/structure/read acceptance
were reused as instructed and not repeated.

## Remaining FW-03 work

This execution is FW-03a only. It does not implement limit prices, streaks,
MA20, daily market summaries, public APIs, Fundwise integration, production
switching, or historical backfill. The caller still supplies explicit evidence
for any claimed complete range and for action coverage; absence of rows does not
establish source completeness. Legacy reference-price Parquet remains
migration evidence and is not promoted to a permanent source fact.

An Orca workspace comment could not be updated: /home/ubuntu/.orca-relay/bin/orca
reported that no owning Orca client is connected to the relay.

## Incremental factor coverage extension (new auxiliary slice)

`advance_factor_coverage(conn, *, symbol, verified_start, verified_end, events,
source="tdx:xdxr")` advances an already selected, continuous sparse factor
cache. The caller must open an outer transaction (`BEGIN IMMEDIATE` is the
recommended entry). The function takes a writer snapshot inside its own
savepoint, rolls back only its work on failure, and never commits the caller.
It neither constructs missing history nor guesses a factor-one baseline.

Example, after the caller has selected factors through 2024-01-02:

```python
conn.execute("BEGIN IMMEDIATE")
result = advance_factor_coverage(
    conn,
    symbol="000001.SZ",
    verified_start="2024-01-03",
    verified_end="2024-01-06",
    events=[{
        "effective_date": "2024-01-04",
        "category": 1,
        "source_key": "cash-2024-01-04",
        "cash_dividend_per_share": 1.0,
        "bonus_shares_per_share": 0.0,
        "rights_shares_per_share": 0.0,
    }],
)
# Recompute downstream fields from result["affected_from"] through
# result["affected_through"] in this same transaction, then commit here.
```

`events` is the explicitly verified complete event set for the inclusive
interval, not an incremental list of newly discovered events. Canonical
per-share fields take precedence over legacy per-ten-share TDX fields. The
main source adapter owns that unit conversion and source completeness proof.
Categories 1, 2 and 5 retain the existing pure-rule policy; unsupported
categories, source disagreements, out-of-range events, conflicting stable
keys and unidentifiable multiple same-category events are rejected.

Empty verified intervals extend only the terminal factor interval (plus its
receipt metadata and timestamp). Event days create sparse continuation rows
with `source=derived:anchor_continuation:<event source>` and an
`anchor_continuation:` factor basis identifying the original selected scale.
They are never written as `factor_anchor` source cumulative observations.
Unchanged historical prefix rows keep their original values and timestamps.
New factor anchors inside the requested extension require explicit maintenance.

Prior closes use actual valid OHLC and source trading status/positive turnover,
without reading derived `calc_status`. Suspended placeholders are excluded;
unknown status with no positive turnover does not establish a traded close.
The prior traded day must itself have known factor coverage. Its raw close is
converted into the preceding event segment's scale as
`raw_close * factor_at_close_day / factor_before_event`. Thus two separate
one-yuan dividends while suspended change a ten-yuan reference to eight yuan:
the cumulative multiplier is `10/8`, rather than `(10/9)**2`, even across
separate calls. A new traded close already uses its own segment's scale.

The terminal factor's existing `payload_json` carries `fw03_advance` metadata:
verified source/range, original selected-scale identity, and signatures of
verified event keys and values. No table or daily factor expansion is added.
Repeat and overlapping messages are accepted only within a prior recorded
verified interval and with identical original event evidence, including when
an external source writer has already changed the current event payload.
An initial overlap with older cache coverage lacking such evidence is refused;
start the first extension at old `valid_through + 1`, or use explicit
maintenance. Historical insertion, omission, revision, or changed root anchor
also requires explicit maintenance.

Return fields:

- `changed_factor_dates`: keys of factor rows actually inserted or extended;
  these may include an older terminal row and are not the recomputation start.
- `changed_event_rows`: number of newly inserted source event rows.
- `changed_rows`: changed factor rows plus newly inserted event rows.
- `affected_from`: old `valid_through + 1` for real additional coverage.
- `affected_through`: new verified coverage end.
- `valid_through`: resulting coverage end, including on a no-op.

An identical replay returns empty changed dates, zero counts and
`affected_from=affected_through=None`. Changed timestamps exceed previous
corporate-action timestamps even if those timestamps are ahead of wall time.
The existing `update_reference_factors` remains available for its prior scope;
callers should use explicit maintenance for rebuilding or replacing an
advanced continuation route.

New evidence for this slice only:

```text
PYTHONPATH=/home/ubuntu/Services/tdxman-fw03-reference-factors/src \
  /home/ubuntu/Services/tdxman/.venv/bin/python -m pytest -q \
  tests/unit/test_sqlite_reference_factors.py -k test_advance_
6 passed, 11 deselected

/home/ubuntu/Services/tdxman/.venv/bin/ruff check \
  src/aspool/sqlite_reference_factors.py tests/unit/test_sqlite_reference_factors.py
All checks passed!

git diff --check
passed
```

The six new independent SQLite tests exercise empty extension, outer
transaction ownership and monotonic timestamps; per-share cash/bonus scale
continuation with stale derived statuses; duplicate/overlap zero writes;
missing coverage, gaps and bounds rejection; historical event revision and
injected write-failure savepoint rollback; and two dividends while suspended.
The existing 11 tests were excluded. All test writes used in-memory databases;
the verified full database was not accessed or modified in this slice. Main
updater integration, source fetching, downstream recalculation and final
acceptance remain the main developer's responsibility.
