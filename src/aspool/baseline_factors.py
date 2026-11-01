"""Rebuild sparse selected factors from the authoritative event baseline."""

import argparse
import hashlib
import json
import math
import sqlite3
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from .event_baseline import FILE


def reference_price(close, cash, bonus, rights, price):
    values = (close, cash, bonus, rights, price)
    if not all(math.isfinite(v) for v in values) or close <= 0 or min(cash, rights, price) < 0:
        raise ValueError("Invalid event prices or ratios")
    denominator = 1 + bonus + rights
    result = (close - cash + rights * price) / denominator if denominator > 0 else 0
    if result <= 0 or not math.isfinite(result):
        raise ValueError("Nonpositive theoretical ex-right price")
    return result


def calculate(events, first, through, previous_close, resolve_conflict=None):
    grouped = defaultdict(dict)
    for day, payload in events:
        if first < day <= through:
            economic = tuple(
                payload[k]
                for k in (
                    "dividend_per_share",
                    "per_share_bonus",
                    "allotment_ratio",
                    "allotment_price",
                    "currency",
                )
            )
            grouped[day][economic] = payload
    factors = [dict(day=first, cumulative=1.0, event_factor=1.0)]
    running, last_quote, carried = 1.0, None, None
    issue = None
    for day, unique in sorted(grouped.items()):
        try:
            parts = list(unique)
            if any(p[4] != "CNY" for p in parts):
                raise ValueError("Unsupported currency")
            # Independent disjoint components combine; competing values do not.
            if any(sum(p[i] != 0 for p in parts) > 1 for i in range(3)):
                if resolve_conflict is None or not resolve_conflict(day, parts):
                    raise ValueError("Conflicting same-day event components")
            cash, bonus, rights = (sum(p[i] for p in parts) for i in range(3))
            subscription = sum(p[2] * p[3] for p in parts)
            if cash == bonus == rights == 0:
                continue
            prior = previous_close(day)
            if prior is None:
                raise ValueError("Missing preceding raw close")
            quote_day, close = prior
            before = carried if quote_day == last_quote else close
            after = reference_price(
                before, cash, bonus, rights, subscription / rights if rights else 0.0
            )
            multiplier = before / after
            running *= multiplier
            if not math.isfinite(running) or running <= 0:
                raise ValueError("Invalid cumulative factor")
            factors.append(dict(day=day, cumulative=running, event_factor=multiplier))
            last_quote, carried = quote_day, after
        except ValueError as exc:
            issue = dict(date=day, reason=str(exc))
            through = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
            break
    for index, row in enumerate(factors):
        row["through"] = (
            (date.fromisoformat(factors[index + 1]["day"]) - timedelta(days=1)).isoformat()
            if index + 1 < len(factors)
            else through
        )
    return factors, issue


def rebuild(root, out, through, apply=False):
    from .pool import pool_lock
    from .qfq_audit_repair import ACTIVE
    from .sqlite_canonical import canonical_operation
    from .sqlite_publication import migration_path, pending_path
    from .sqlite_stock_store import stock_connection

    root, out = root.resolve(), out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    marker = json.loads(migration_path(root).read_text())
    if marker.get("operation") != "qfq-history-audit" or pending_path(root).exists():
        raise ValueError("Requires the safely paused history task with no pending publication")
    with sqlite3.connect((root / FILE).as_uri() + "?mode=ro", uri=True) as base:
        metadata = json.loads(base.execute("SELECT payload FROM metadata").fetchone()[0])
        events = defaultdict(list)
        for symbol, day, payload in base.execute(
            "SELECT symbol,effective_date,payload FROM events"
        ):
            events[symbol].append((day, json.loads(payload)))
    token = ACTIVE.set(str(root))
    report = dict(
        source_sha256=metadata["sha256"],
        through=through,
        applied=apply,
        symbols=0,
        factor_rows=0,
        unresolved=[],
        no_bars=[],
        enriched_status="requires_rebuild",
        resolved_conflicts=[],
    )
    try:
        with pool_lock(root, write=True), stock_connection(root, read_only=not apply) as conn:
            symbols = set(events)
            symbols.update(
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT symbol FROM corporate_actions WHERE record_kind='factor'"
                )
            )
            last_symbol = ""
            while True:
                found = conn.execute(
                    "SELECT symbol FROM daily_bars WHERE symbol>? ORDER BY symbol LIMIT 1",
                    (last_symbol,),
                ).fetchone()
                if found is None:
                    break
                last_symbol = found[0]
                symbols.add(last_symbol)
            from .invalid_securities import invalid_symbols

            report["excluded_symbols"] = sorted(symbols & invalid_symbols(root))
            for symbol in sorted(symbols - invalid_symbols(root)):
                first_row = conn.execute(
                    "SELECT trade_date FROM daily_bars WHERE symbol=? "
                    "AND trade_date<=? AND close>0 AND volume>0 "
                    "ORDER BY trade_date LIMIT 1",
                    (symbol, through),
                ).fetchone()
                if first_row is None:
                    report["no_bars"].append(symbol)
                    continue

                def previous(day):
                    return conn.execute(
                        "SELECT trade_date,close FROM daily_bars WHERE symbol=? "
                        "AND trade_date<? AND close>0 AND volume>0 "
                        "ORDER BY trade_date DESC LIMIT 1",
                        (symbol, day),
                    ).fetchone()

                def resolve_conflict(day, parts):
                    from .sqlite_qfq import paired_reference

                    prior = previous(day)
                    if prior is None:
                        return False
                    observations = []
                    for table, when in [
                        ("daily_bars", day),
                        ("stock_qfq_bars", day),
                        ("daily_bars", prior[0]),
                        ("stock_qfq_bars", prior[0]),
                    ]:
                        cursor = conn.execute(
                            f"SELECT * FROM {table} WHERE symbol=? AND trade_date=?", (symbol, when)
                        )
                        value = cursor.fetchone()
                        if value is None:
                            return False
                        observations.append(dict(zip([r[0] for r in cursor.description], value)))
                    reference, origin = paired_reference(*observations)
                    if origin != "derived:paired_qfq_affine":
                        return False
                    cash, bonus, rights = (sum(p[i] for p in parts) for i in range(3))
                    price = sum(p[2] * p[3] for p in parts) / rights if rights else 0
                    combined = reference_price(prior[1], cash, bonus, rights, price)
                    single_matches = any(
                        abs(reference_price(prior[1], *p[:4]) - reference) <= 0.02 for p in parts
                    )
                    if abs(combined - reference) <= 0.02 and not single_matches:
                        report["resolved_conflicts"].append(
                            dict(
                                symbol=symbol,
                                date=day,
                                evidence="paired_raw_qfq",
                                observed_reference=reference,
                                combined_reference=combined,
                            )
                        )
                        return True
                    return False

                rows, issue = calculate(
                    events.get(symbol, []), first_row[0], through, previous, resolve_conflict
                )
                if issue:
                    report["unresolved"].append(dict(symbol=symbol, **issue))
                existing = conn.execute(
                    "SELECT effective_date,event_factor,cumulative_factor,valid_through,"
                    "source,factor_basis FROM corporate_actions WHERE symbol=? "
                    "AND record_kind='factor' ORDER BY effective_date",
                    (symbol,),
                ).fetchall()
                expected = [
                    (
                        r["day"],
                        r["event_factor"],
                        r["cumulative"],
                        r["through"],
                        "derived:baseline_event_chain",
                        "event_baseline:" + metadata["sha256"],
                    )
                    for r in rows
                ]
                if apply and existing != expected:
                    with canonical_operation(conn, "factor_bootstrap"):
                        if not conn.in_transaction:
                            conn.execute("BEGIN IMMEDIATE")
                        try:
                            conn.execute(
                                "DELETE FROM corporate_actions WHERE symbol=? "
                                "AND record_kind='factor'",
                                (symbol,),
                            )
                            for row in rows:
                                proof = hashlib.sha256(
                                    json.dumps(
                                        [metadata["sha256"], symbol, row], sort_keys=True
                                    ).encode()
                                ).hexdigest()
                                conn.execute(
                                    "INSERT INTO corporate_actions(symbol,effective_date,"
                                    "record_kind,"
                                    "source,source_key,event_factor,cumulative_factor,valid_from,"
                                    "valid_through,factor_basis,updated_at,input_hash,"
                                    "algorithm_version) "
                                    "VALUES (?,?,'factor','derived:baseline_event_chain',"
                                    "'selected',"
                                    "?,?,?,?,?,?,?,'baseline-events-v1')",
                                    (
                                        symbol,
                                        row["day"],
                                        row["event_factor"],
                                        row["cumulative"],
                                        row["day"],
                                        row["through"],
                                        "event_baseline:" + metadata["sha256"],
                                        time.time_ns() // 1000,
                                        proof,
                                    ),
                                )
                            if (report["symbols"] + 1) % 100 == 0:
                                conn.commit()
                        except BaseException:
                            conn.rollback()
                            raise
                report["symbols"] += 1
                report["factor_rows"] += len(rows)
                if report["symbols"] % 100 == 0:
                    print(
                        json.dumps({k: report[k] for k in ("symbols", "factor_rows", "applied")}),
                        flush=True,
                    )
            if apply and conn.in_transaction:
                with canonical_operation(conn, "factor_bootstrap"):
                    conn.commit()
            (out / ("applied.json" if apply else "preview.json")).write_text(
                json.dumps(report, indent=2)
            )
    finally:
        ACTIVE.reset(token)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--through", default="2026-09-30")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(rebuild(args.root, args.report_dir, args.through, args.apply)))
