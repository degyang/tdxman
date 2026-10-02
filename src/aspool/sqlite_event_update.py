"""Merge recent same-source event revisions and rebuild only their factor suffix."""

import json
import math
import time
from datetime import date, timedelta

from .api_contract import DataPoolError
from .sqlite_reference_factors import (
    _coverage_event_value,
    _coverage_events,
    _coverage_previous_close,
    _event_ratio,
    _insert_action,
    normalize_actions,
)


class SymbolUpdateError(ValueError):
    """A rejected security can be isolated; database failures must still propagate."""

    def __init__(self, symbol, error, *, phase="validation"):
        self.symbol = symbol
        self.phase = phase
        super().__init__(f"{symbol}: {error}")


def _recent_events(events, source):
    # TDX 2..10 carry before/after share counts; price adjustments are category 1.
    # Expansion/contraction and warrant categories remain explicit unsupported inputs.
    return _coverage_events(events, source, categories=tuple(range(1, 11)))


def stored_event(day, source, key, category, payload):
    item = json.loads(payload)
    item.update(effective_date=day, source=source, source_key=key, category=category)
    if source == "tdx:xdxr" and category == 1 and key.startswith("category=1:slot="):
        for target, raw in (
            ("cash_dividend_per_share", "fenhong"),
            ("bonus_shares_per_share", "songzhuangu"),
            ("rights_shares_per_share", "peigu"),
            ("rights_price", "peigujia"),
        ):
            item.setdefault(target, item.get(raw))
    return item


def merge_recent_events(conn, *, symbol, verified_start, verified_end, events, source="tdx:xdxr"):
    """Successful incoming keys win; absence never means deletion.

    Keep the selected scale immediately before a revision. Rebuild the finite
    suffix from dated actions and traded closes, preserving all earlier factors.
    The caller owns the transaction, including dependent features and summaries.
    """
    if not conn.in_transaction:
        raise ValueError("Event updates require an outer transaction")
    start, end = date.fromisoformat(verified_start), date.fromisoformat(verified_end)
    if not 0 <= (end - start).days <= 30:
        raise ValueError("Event refresh requires a 1..31 day window")
    incoming = _recent_events(events, source)
    if any(not verified_start <= day <= verified_end for day, _ in incoming):
        raise ValueError("Event outside requested window")
    if hasattr(conn, "root"):
        from .base_delta import store_event_coverage

        store_event_coverage(
            conn.root,
            symbol=symbol,
            verified_start=verified_start,
            verified_end=verified_end,
            source=source,
            events=incoming.values(),
        )
    stored = {}
    stamps = {}
    for day, origin, key, category, payload, stamp in conn.execute(
        "SELECT effective_date,source,source_key,category,payload_json,updated_at "
        "FROM corporate_actions WHERE symbol=? AND record_kind='event' "
        "AND effective_date BETWEEN ? AND ? ORDER BY effective_date,source_key",
        (symbol, verified_start, verified_end),
    ):
        if origin != source:
            raise ValueError("Overlapping events from a different source")
        stored.update(_recent_events([stored_event(day, origin, key, category, payload)], source))
        stamps[(day, key)] = stamp
    changes = {
        key: event
        for key, event in incoming.items()
        if key not in stored or _coverage_event_value(event) != _coverage_event_value(stored[key])
    }
    price_days = [day for (day, _), item in changes.items() if item["category"] == 1]
    for key, event in changes.items():
        _insert_action(
            conn,
            symbol,
            event,
            source,
            updated_at=max(time.time_ns() // 1000, stamps.get(key, 0) + 1),
        )
    result = dict(
        changed_rows=len(changes),
        changed_event_rows=len(changes),
        affected_from=None,
        affected_through=None,
        changed_factor_dates=[],
    )
    old_end = conn.execute(
        "SELECT max(valid_through) FROM corporate_actions WHERE symbol=? AND record_kind='factor'",
        (symbol,),
    ).fetchone()[0]
    if old_end is None:
        return result
    # Quotes can still update without inventing a missing original factor scale.
    first = min(price_days) if price_days else None
    if verified_end > old_end:
        next_day = (date.fromisoformat(old_end) + timedelta(days=1)).isoformat()
        if next_day < verified_start:
            raise ValueError("Factor coverage gap exceeds verified event window")
        first = min(first, next_day) if first else next_day
    if first is None:
        return result
    through = max(old_end, verified_end)
    if (date.fromisoformat(through) - date.fromisoformat(first)).days > 93:
        raise DataPoolError("LOCAL_UPDATE_BUDGET_EXCEEDED", "Event revision suffix exceeds budget")
    if (
        conn.execute(
            "SELECT count(*) FROM (SELECT 1 FROM daily_features WHERE symbol=? "
            "AND trade_date BETWEEN ? AND ? LIMIT 61)",
            (symbol, first, through),
        ).fetchone()[0]
        > 60
    ):
        raise DataPoolError("LOCAL_UPDATE_BUDGET_EXCEEDED", "Event revision exceeds 60 sessions")
    columns = (
        "effective_date,source,source_key,event_factor,cumulative_factor,"
        "valid_from,valid_through,factor_basis,payload_json,updated_at"
    )
    row = conn.execute(
        f"SELECT {columns} FROM corporate_actions WHERE symbol=? "
        "AND record_kind='factor' AND effective_date<? "
        "ORDER BY effective_date DESC LIMIT 1",
        (symbol, first),
    ).fetchone()
    if row is None:
        raise ValueError("Revision needs a preceding verified factor anchor")
    base = dict(zip(columns.split(","), row))
    if (
        not base["cumulative_factor"]
        or not math.isfinite(base["cumulative_factor"])
        or base["cumulative_factor"] <= 0
    ):
        raise ValueError("Invalid preceding factor anchor")
    if base["valid_through"] < (date.fromisoformat(first) - timedelta(days=1)).isoformat():
        raise ValueError("Preceding factor scale has a coverage gap")
    # Preserve source observations; only the selected factor cache is revised.
    merged = dict(stored)
    merged.update(incoming)
    if through > verified_end:
        for day, origin, key, category, payload in conn.execute(
            "SELECT effective_date,source,source_key,category,payload_json FROM corporate_actions "
            "WHERE symbol=? AND record_kind='event' AND effective_date>? AND effective_date<=?",
            (symbol, verified_end, through),
        ):
            if origin != source:
                raise ValueError("Unverified suffix event source")
            merged.update(
                _recent_events([stored_event(day, origin, key, category, payload)], source)
            )
    desired = [dict(base)]
    running = base["cumulative_factor"]
    for group in normalize_actions(
        item for (day, _), item in merged.items() if first <= day <= through
    ):
        if not any(e["category"] == 1 for e in group["events"]):
            continue
        day = group["effective_date"]
        prior = _coverage_previous_close(conn, symbol, day)
        if prior is None:
            raise ValueError(f"Missing preceding traded close for event {day}")
        prior_day, price = prior
        if prior_day >= base["effective_date"]:
            prior_factor = next(
                item["cumulative_factor"]
                for item in reversed(desired)
                if item["effective_date"] <= prior_day
            )
        else:
            found = conn.execute(
                "SELECT cumulative_factor FROM corporate_actions WHERE symbol=? "
                "AND record_kind='factor' AND valid_from<=? AND valid_through>=?",
                (symbol, prior_day, prior_day),
            ).fetchone()
            if found is None:
                raise ValueError("Event predecessor has no verified factor")
            prior_factor = found[0]
        ratio = _event_ratio(
            price * prior_factor / running,
            [event for event in group["events"] if event["category"] == 1],
        )
        if ratio == 1:
            continue
        desired[-1]["valid_through"] = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
        running *= ratio
        if not math.isfinite(running) or running <= 0:
            raise ValueError("Revised cumulative factor must be finite and positive")
        desired.append(
            dict(
                effective_date=day,
                source="derived:event_chain",
                source_key="selected",
                event_factor=ratio,
                cumulative_factor=running,
                valid_from=day,
                valid_through=through,
                factor_basis="anchor_continuation:daily_revision",
                payload_json="{}",
                updated_at=0,
            )
        )
    desired[-1]["valid_through"] = through
    current = {
        r[0]: dict(zip(columns.split(","), r))
        for r in conn.execute(
            f"SELECT {columns} FROM corporate_actions WHERE symbol=? AND record_kind='factor' "
            "AND effective_date>=?",
            (symbol, base["effective_date"]),
        )
    }
    wanted = {r["effective_date"] for r in desired}
    for day in current.keys() - wanted:
        conn.execute(
            "DELETE FROM corporate_actions WHERE symbol=? AND record_kind='factor' "
            "AND effective_date=?",
            (symbol, day),
        )
        result["changed_rows"] += 1
        result["changed_factor_dates"].append(day)
    for item in desired:
        day = item["effective_date"]
        old = current.get(day)
        if old and all(
            old[k] == item[k]
            for k in ("event_factor", "cumulative_factor", "valid_from", "valid_through")
        ):
            continue
        stamp = max(time.time_ns() // 1000, (old or {}).get("updated_at", 0) + 1)
        payload = json.loads(item["payload_json"] or "{}")
        payload.pop("fw03_advance", None)
        if old:
            conn.execute(
                "UPDATE corporate_actions SET event_factor=?,cumulative_factor=?,valid_from=?,"
                "valid_through=?,source=?,source_key=?,factor_basis=?,payload_json=?,updated_at=? "
                "WHERE symbol=? AND record_kind='factor' "
                "AND effective_date=?",
                (
                    item["event_factor"],
                    item["cumulative_factor"],
                    item["valid_from"],
                    item["valid_through"],
                    item["source"],
                    item["source_key"],
                    item["factor_basis"],
                    json.dumps(payload),
                    stamp,
                    symbol,
                    day,
                ),
            )
        else:
            conn.execute(
                "INSERT INTO corporate_actions(symbol,effective_date,record_kind,source,source_key,"
                "event_factor,cumulative_factor,valid_from,valid_through,factor_basis,"
                "payload_json,updated_at) "
                "VALUES (?,?,'factor',?,?,?,?,?,?,?,?,?)",
                (
                    symbol,
                    day,
                    item["source"],
                    item["source_key"],
                    item["event_factor"],
                    item["cumulative_factor"],
                    item["valid_from"],
                    item["valid_through"],
                    item["factor_basis"],
                    json.dumps(payload),
                    stamp,
                ),
            )
        result["changed_rows"] += 1
        result["changed_factor_dates"].append(day)
    if result["changed_factor_dates"] or price_days:
        result.update(affected_from=first, affected_through=through)
    return result
