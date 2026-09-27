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
  It retains the original source fields. Unknown required amounts are errors.
- build_selected_factors(anchors=..., events=..., prior_closes=...,
  through=...) chooses the cumulative-anchor route whenever anchors are
  present. It copies source cumulative values directly; it never cumprods them.
  Without anchors, it builds an event route from known prior closes. Same-day
  events are combined before calculating a factor. A factor interval starts at
  its effective date and ends before the next factor, or at the caller's
  explicitly verified through date. No factor means no claim of coverage.
- select_reference_pre_close(...) selects reliable dated reference,
  action-adjusted previous close, then a raw fallback. Conflicting reliable
  dated candidates fail explicitly.
- select_is_st(...) selects reliable dated ST, then a name whose
  name_as_of exactly matches the requested day, otherwise None. Historical
  names are never filled from a current snapshot. Migrated
  raw_fallback:* ST values do not count as dated evidence.
- update_reference_factors(conn, *, symbol, dates, actions, anchors,
  prior_closes, factor_through, dated_pre_close, dated_st) writes within a
  SQLite savepoint. Pass the complete selected factor inputs for the affected
  symbol and verified range. The caller controls the outer transaction. The
  writer updates only source candidate/effective reference and ST columns,
  action rows, and its selected factor cache. It preserves other daily feature
  fields. An identical call does not update timestamps or write factor rows.

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
            anchors=[],
            factor_through="2024-06-14",
        )
        conn.commit()

For same-day same-category duplicate actions, supply a stable source_key,
event_id, or event_slot; otherwise the writer rejects the input to avoid
silently replacing one event with another. Unsupported event categories and
conflicting reliable source values also fail. No deletion or full-source-set
replacement protocol is added.

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

Targeted verification used the main project environment and confirmed the
module import resolves to this worktree:

    PYTHONPATH=/home/ubuntu/Services/tdxman-fw03-reference-factors/src \
      /home/ubuntu/Services/tdxman/.venv/bin/python -c \
      'import aspool.sqlite_reference_factors as m; print(m.__file__)'
    /home/ubuntu/Services/tdxman-fw03-reference-factors/src/aspool/sqlite_reference_factors.py

    PYTHONPATH=/home/ubuntu/Services/tdxman-fw03-reference-factors/src \
      /home/ubuntu/Services/tdxman/.venv/bin/python -m pytest -q \
      tests/unit/test_sqlite_reference_factors.py
    6 passed

    /home/ubuntu/Services/tdxman/.venv/bin/ruff check \
      src/aspool/sqlite_reference_factors.py tests/unit/test_sqlite_reference_factors.py
    All checks passed!

    git diff --check
    passed

The six new tests cover cash dividends, bonus shares, rights issues, merged
same-day actions, multiple event dates, non-cumprod source anchors, valid
intervals, unknown/conflicting sources, dated ST/name/unknown precedence,
SQLite rollback, preserved unrelated fields, and repeat-call no-op. The
previously completed M0 migration reconciliation, 10 existing migration cases,
full value reconciliation, integrity checks, and remote hash/structure/read
acceptance were reused as instructed and not repeated.

## Remaining FW-03 work

This execution is FW-03a only. It does not implement limit prices, streaks,
MA20, daily market summaries, public APIs, Fundwise integration, production
switching, or historical backfill. The writer requires a caller to supply
complete selected factor inputs and an evidence-backed factor coverage end for
the affected symbol. It does not establish source coverage from absence of
rows. Legacy reference-price Parquet remains migration evidence and is not
promoted to a permanent source fact.

An Orca workspace comment could not be updated: /home/ubuntu/.orca-relay/bin/orca
reported that no owning Orca client is connected to the relay.
