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
