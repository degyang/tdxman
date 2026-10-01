"""Narrow SQLite rules and atomic writer for selected reference factors and facts.

This module intentionally does not calculate limits, streaks, moving averages, or
market summaries. It handles explicitly selected securities and dates.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from .st_source import classify_st_name

_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EVENT_SOURCE = "derived:event_chain"
_ANCHOR_SOURCE = "selected:source_cumulative_factor"


def _known_source(value: Any) -> bool:
    normalized = str(value or "").strip().lower()
    return (
        bool(normalized)
        and normalized not in {"unknown", "unknown-source", "unknown source", "unspecified"}
        and not normalized.startswith("unknown:")
    )


def _day(value: Any) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str) or not _ISO_DAY.fullmatch(value):
        raise ValueError(f"Expected YYYY-MM-DD date, got {value!r}")
    date.fromisoformat(value)
    return value


def _positive(value: Any, label: str) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{label} must be finite and positive")
    return number


def _canonical_event(raw: Mapping[str, Any]) -> dict[str, Any]:
    event = dict(raw)
    if int(event["category"]) != 1:
        return event
    names = (
        ("cash_dividend_per_share", "fenhong", 10.0),
        ("bonus_shares_per_share", "songzhuangu", 10.0),
        ("rights_shares_per_share", "peigu", 10.0),
    )
    for canonical, source, divisor in names:
        value = event.get(canonical)
        if value is None:
            raw_value = event.get(source)
            if raw_value is None:
                raise ValueError(f"Unknown corporate action amount: {source}")
            value = float(raw_value) / divisor
        number = float(value)
        if not math.isfinite(number) or number < 0:
            raise ValueError(f"Invalid corporate action amount: {canonical}")
        event[canonical] = number
    rights = event["rights_shares_per_share"]
    price = event.get("rights_price")
    if price is None:
        price = event.get("peigujia")
    if rights and price is None:
        raise ValueError("Unknown corporate action amount: peigujia")
    if price is not None and (not math.isfinite(float(price)) or float(price) < 0):
        raise ValueError("Invalid corporate action amount: rights_price")
    if price is not None:
        event["rights_price"] = float(price)
    return event


def normalize_actions(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Normalize events and group the original payloads by effective date."""
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for raw in events:
        event = dict(raw)
        effective = _day(event.get("effective_date", event.get("date")))
        category = event.get("category")
        if category is None:
            raise ValueError("Corporate action category is required")
        event["effective_date"] = effective
        event["category"] = int(category)
        event = _canonical_event(event)
        if event.get("source"):
            event.setdefault("source_origins", [str(event["source"])])
        business_key = event.get("business_key")
        stable_key = business_key or event.get("event_id") or event.get("source_key")
        if stable_key is not None:
            key = (
                effective,
                "business" if business_key else str(event.get("source") or ""),
                str(stable_key),
            )
            previous = by_key.get(key)
            if previous is not None:

                def comparable(row):
                    return {
                        name: value
                        for name, value in row.items()
                        if name
                        not in {"source", "source_name", "source_key", "event_id", "source_origins"}
                    }

                if comparable(previous) != comparable(event):
                    raise ValueError(
                        f"Conflicting corporate action values for stable key {stable_key}"
                    )
                origins = set(previous.get("source_origins", (previous.get("source"),)))
                origins.update(event.get("source_origins", (event.get("source"),)))
                origins.discard(None)
                previous["source_origins"] = sorted(str(source) for source in origins)
                previous["source"] = min(previous["source_origins"])
                continue
            by_key[key] = event
        by_day[effective].append(event)
    return [
        {
            "effective_date": effective,
            "events": sorted(group, key=lambda x: json.dumps(x, sort_keys=True, default=str)),
        }
        for effective, group in sorted(by_day.items())
    ]


def _event_ratio(previous_close: float, events: Sequence[Mapping[str, Any]]) -> float:
    """Return previous close / ex-right reference for TDX category-1 payloads.

    fenhong, songzhuangu and peigu are per ten shares; fenhong is yuan.
    """
    cash = bonus = rights = rights_value = 0.0
    relevant = False
    for event in events:
        category = int(event["category"])
        if category == 1:
            relevant = True
            item = _canonical_event(event)
            event_rights = item["rights_shares_per_share"]
            cash += item["cash_dividend_per_share"]
            bonus += item["bonus_shares_per_share"]
            rights += event_rights
            if item.get("rights_price") is not None:
                rights_value += item["rights_price"] * event_rights
        elif category not in (2, 5):
            raise ValueError(f"Unsupported corporate action category {category}")
    values = (cash, bonus, rights, rights_value)
    if any(not math.isfinite(v) for v in values) or min(cash, bonus, rights) < 0:
        raise ValueError("Invalid TDX corporate action amounts")
    if not relevant:
        return 1.0
    price = rights_value / rights if rights else 0.0
    denominator = 1.0 + bonus + rights
    reference = (previous_close - cash + rights * price) / denominator
    if not math.isfinite(reference) or reference <= 0:
        raise ValueError("Corporate action produces an invalid reference price")
    return previous_close / reference


def select_reference_pre_close(
    *,
    dated: Sequence[tuple[float | None, str | None]] = (),
    previous_close: float | None = None,
    actions: Sequence[Mapping[str, Any]] = (),
    actions_covered: bool = False,
    raw_candidate: tuple[float | None, str | None] = (None, None),
) -> tuple[float | None, str | None]:
    """Choose dated source, covered prior close plus actions, then raw fallback."""
    reliable = []
    for value, source in dated:
        number = _positive(value, "dated pre_close")
        if number is not None and _known_source(source) and not source.startswith("raw_fallback:"):
            reliable.append((number, source))
    if len({value for value, _ in reliable}) > 1:
        raise ValueError("Conflicting reliable dated reference prices")
    if reliable:
        return reliable[0]
    prior = _positive(previous_close, "previous close")
    if prior is not None and actions_covered:
        normalized = normalize_actions(actions)
        event_sources = {
            str(event.get("source") or "") for group in normalized for event in group["events"]
        }
        if event_sources and (
            len(event_sources) != 1 or not _known_source(next(iter(event_sources)))
        ):
            raise ValueError("Reference-price actions need one explicit, reliable source")
        reference = prior
        for batch in normalized:
            reference /= _event_ratio(reference, batch["events"])
        if not math.isfinite(reference) or reference <= 0:
            raise ValueError("Invalid combined corporate action reference")
        return reference, "derived:previous_close+corporate_actions"
    value, source = raw_candidate
    value = _positive(value, "raw pre_close")
    return (value, source or "raw_fallback:unspecified") if value is not None else (None, None)


def select_is_st(
    *,
    dated: Sequence[tuple[bool | None, str | None]] = (),
    name: str | None = None,
    name_as_of: str | None = None,
    trade_date: str,
) -> tuple[bool | None, str | None, str | None]:
    """Use dated ST, then a same-day name; default missing evidence to non-ST."""
    day = _day(trade_date)
    known = [
        (bool(value), source)
        for value, source in dated
        if value is not None and _known_source(source) and not source.startswith("raw_fallback:")
    ]
    if len({value for value, _ in known}) > 1:
        raise ValueError("Conflicting reliable dated ST values")
    if known:
        return known[0][0], known[0][1], None
    if name is not None and name_as_of is not None and _day(name_as_of) == day:
        value = classify_st_name(name)
        if value is not None:
            return value, "dated_name", day
    return False, "assumed:not_st", None


def build_selected_factors(
    *,
    anchors: Iterable[Mapping[str, Any]] = (),
    events: Iterable[Mapping[str, Any]] = (),
    prior_closes: Mapping[str, float] | None = None,
    verified_no_event_range: tuple[str, str, str] | None = None,
    through: str,
) -> list[dict[str, Any]]:
    """Build sparse intervals from exactly one route; source anchors are not cumprod'd."""
    end = _day(through)
    anchor_rows = list(anchors)
    if anchor_rows:
        if verified_no_event_range is not None:
            raise ValueError("Source cumulative anchors cannot be mixed with an event baseline")
        sources = {str(item.get("source") or "") for item in anchor_rows}
        if len(sources) != 1 or not _known_source(next(iter(sources))):
            raise ValueError("Source cumulative factor anchors need one explicit, reliable source")
        grouped: dict[str, list[tuple[str, float]]] = defaultdict(list)
        for item in anchor_rows:
            day = _day(item["effective_date"])
            value = _positive(item.get("source_cumulative_factor"), "source factor")
            if day <= end and value is not None:
                grouped[day].append((str(item.get("source") or "unknown"), value))
        selected = []
        for day, values in sorted(grouped.items()):
            if len({value for _, value in values}) > 1:
                raise ValueError(f"Conflicting source cumulative factors on {day}")
            sources = ",".join(sorted({source for source, _ in values}))
            selected.append((day, values[0][1], f"source_anchor:{sources}"))
        route = "source_cumulative_factor"
    else:
        grouped_events: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        normalized_events = [
            event for group in normalize_actions(events) for event in group["events"]
        ]
        event_sources = {str(item.get("source") or "") for item in normalized_events}
        if normalized_events and (
            len(event_sources) != 1 or not _known_source(next(iter(event_sources)))
        ):
            raise ValueError("Corporate action factors need one explicit, reliable source")
        for item in normalized_events:
            category = int(item["category"])
            if category in (2, 5):
                continue
            grouped_events[_day(item.get("effective_date", item.get("date")))].append(dict(item))
        selected = []
        running = 1.0
        baseline_through = None
        if verified_no_event_range is not None:
            baseline_from, baseline_through, baseline_source = verified_no_event_range
            baseline_from, baseline_through = _day(baseline_from), _day(baseline_through)
            if not _known_source(baseline_source):
                raise ValueError("Confirmed no-event baseline needs a reliable source")
            if baseline_from > baseline_through or baseline_through > end:
                raise ValueError("Invalid confirmed no-event baseline range")
            if any(baseline_from <= day <= baseline_through for day in grouped_events):
                raise ValueError("Confirmed no-event range contains a stored action")
            selected.append((baseline_from, 1.0, f"verified_no_events:{baseline_source}"))
        for day, same_day in sorted(grouped_events.items()):
            if day > end:
                continue
            if selected and day <= selected[-1][0]:
                raise ValueError("Event date must follow its factor baseline date")
            close = _positive((prior_closes or {}).get(day), f"prior close for {day}")
            if close is None:
                raise ValueError(f"Missing prior close for event factor on {day}")
            if selected:
                running = selected[-1][1]
            running *= _event_ratio(close, same_day)
            if not math.isfinite(running) or running <= 0:
                raise ValueError("Invalid event cumulative factor")
            sources = ",".join(sorted({str(item["source"]) for item in same_day}))
            selected.append((day, running, sources))
        route = "event_chain"
    result = []
    for index, (day, cumulative, basis) in enumerate(selected):
        next_day = selected[index + 1][0] if index + 1 < len(selected) else None
        through_day = (
            min(date.fromisoformat(end), date.fromisoformat(next_day) - timedelta(days=1))
            if next_day
            else date.fromisoformat(end)
        )
        if basis.startswith("verified_no_events:"):
            through_day = min(through_day, date.fromisoformat(baseline_through))
        previous = selected[index - 1][1] if index else None
        result.append(
            {
                "effective_date": day,
                "event_factor": cumulative / previous if previous is not None else None,
                "cumulative_factor": cumulative,
                "valid_from": day,
                "valid_through": through_day.isoformat(),
                "factor_basis": f"{route}:{basis}",
            }
        )
    return result


def _insert_action(
    conn: sqlite3.Connection,
    symbol: str,
    item: Mapping[str, Any],
    source: str,
    *,
    updated_at: int | None = None,
) -> None:
    day = _day(item.get("effective_date", item.get("date")))
    category = int(item["category"])
    payload = json.dumps(
        dict(item), ensure_ascii=False, sort_keys=True, allow_nan=False, default=str
    )
    supplied_key = item.get("source_key") or item.get("event_id") or item.get("business_key")
    slot = item.get("event_slot", "0")
    key = str(supplied_key if supplied_key is not None else f"{source}:{category}:{day}:{slot}")
    conn.execute(
        """INSERT INTO corporate_actions
        (symbol,effective_date,record_kind,source,source_key,category,payload_json,updated_at)
        VALUES (?,?,'event',?,?,?,?,?)
        ON CONFLICT(symbol,effective_date,record_kind,source,source_key)
        DO UPDATE SET category=excluded.category,payload_json=excluded.payload_json,
                      updated_at=excluded.updated_at
        WHERE category IS NOT excluded.category OR payload_json IS NOT excluded.payload_json""",
        (symbol, day, source, key, category, payload, updated_at or time.time_ns() // 1000),
    )


def _previous_valid_close(
    conn: sqlite3.Connection, symbol: str, before_date: str
) -> tuple[str, float] | None:
    """Read the latest close whose same-day feature is confirmed TRADED and OHLC is valid."""
    return conn.execute(
        """SELECT b.trade_date,b.close
        FROM daily_bars b JOIN daily_features f USING(symbol,trade_date)
        WHERE b.symbol=? AND b.trade_date<? AND f.calc_status='TRADED'
          AND b.open>0 AND b.high>0 AND b.low>0 AND b.close>0
          AND abs(b.open)<1e308 AND abs(b.high)<1e308
          AND abs(b.low)<1e308 AND abs(b.close)<1e308
          AND b.low<=b.high AND b.low<=min(b.open,b.close)
          AND b.high>=max(b.open,b.close)
        ORDER BY b.trade_date DESC LIMIT 1""",
        (symbol, before_date),
    ).fetchone()


def _range(value: tuple[str, str] | tuple[str, str, str], *, with_source: bool = False):
    if with_source:
        source, start, end = value
        if not _known_source(source):
            raise ValueError("Complete source range needs an explicit reliable source")
    else:
        start, end = value
        source = None
    start, end = _day(start), _day(end)
    if start > end:
        raise ValueError("Invalid complete range")
    return source, start, end


def update_reference_factors(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    dates: Iterable[str],
    actions: Iterable[Mapping[str, Any]] | None = None,
    anchors: Iterable[Mapping[str, Any]] | None = None,
    factor_through: str | None = None,
    dated_pre_close: Mapping[str, Sequence[tuple[float | None, str | None]]] | None = None,
    dated_st: Mapping[str, Sequence[tuple[bool | None, str | None]]] | None = None,
    actions_covered_dates: Iterable[str] = (),
    verified_no_event_range: tuple[str, str, str] | None = None,
    actions_complete_range: tuple[str, str, str] | None = None,
    anchors_complete_range: tuple[str, str, str] | None = None,
    factors_complete_range: tuple[str, str] | None = None,
) -> dict[str, int]:
    """Apply sparse source changes and derived values atomically for one symbol.

    Omitted actions/anchors mean no update. Supplied rows are finite upserts.
    Complete-range arguments explicitly authorize replacement within only that
    source/date range. Selected factors are diffed by date and changed in place.
    """
    selected_dates = sorted({_day(value) for value in dates})
    if not symbol or not selected_dates:
        return {"actions": 0, "anchors": 0, "factors": 0, "features": 0}
    actions_supplied, anchors_supplied = actions is not None, anchors is not None
    action_rows = [dict(row) for row in actions] if actions_supplied else []
    anchor_rows = [dict(row) for row in anchors] if anchors_supplied else []
    for item in action_rows:
        item.setdefault("source", "tdx:xdxr")
    action_rows = [event for group in normalize_actions(action_rows) for event in group["events"]]
    same_day_actions: dict[tuple[str, str, int], list[Mapping[str, Any]]] = defaultdict(list)
    for item in action_rows:
        key = (str(item.get("source") or ""), item["effective_date"], int(item["category"]))
        same_day_actions[key].append(item)
    for (_, event_day, _), group in same_day_actions.items():
        if len(group) > 1 and any(
            not (
                row.get("source_key")
                or row.get("event_id")
                or row.get("business_key")
                or row.get("event_slot") is not None
            )
            for row in group
        ):
            raise ValueError(f"Same-day same-category actions on {event_day} need stable keys")
    if actions_complete_range is not None and not actions_supplied:
        raise ValueError("actions_complete_range requires an explicit actions collection")
    if anchors_complete_range is not None and not anchors_supplied:
        raise ValueError("anchors_complete_range requires an explicit anchors collection")
    action_range = (
        _range(actions_complete_range, with_source=True) if actions_complete_range else None
    )
    anchor_range = (
        _range(anchors_complete_range, with_source=True) if anchors_complete_range else None
    )
    factor_range = _range(factors_complete_range) if factors_complete_range else None
    covered_action_days = {_day(value) for value in actions_covered_dates}
    dated_pre_close, dated_st = dated_pre_close or {}, dated_st or {}
    changed_actions = changed_anchors = changed_features = changed_factors = 0
    savepoint = "fw03_reference_factors"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        # Source upserts and every read below share the same SQLite snapshot.
        incoming_sources = {str(item.get("source") or "") for item in action_rows}
        if len(incoming_sources) > 1 or (
            incoming_sources and not _known_source(next(iter(incoming_sources)))
        ):
            raise ValueError("A finite action update must use one explicit source")
        for item in action_rows:
            before = conn.total_changes
            _insert_action(conn, symbol, item, str(item["source"]))
            changed_actions += conn.total_changes - before
        if action_range:
            source, start, end = action_range
            if incoming_sources and incoming_sources != {source}:
                raise ValueError("actions_complete_range source must match its input collection")
            incoming_keys = {
                str(
                    item.get("source_key")
                    or item.get("event_id")
                    or item.get("business_key")
                    or (
                        f"{source}:{item['category']}:{item['effective_date']}:"
                        f"{item.get('event_slot', '0')}"
                    )
                )
                for item in action_rows
            }
            existing = conn.execute(
                """SELECT effective_date,source_key FROM corporate_actions
                WHERE symbol=? AND record_kind='event' AND source=?
                  AND effective_date BETWEEN ? AND ?""",
                (symbol, source, start, end),
            ).fetchall()
            for event_day, key in existing:
                if key not in incoming_keys:
                    before = conn.total_changes
                    conn.execute(
                        """DELETE FROM corporate_actions WHERE symbol=? AND effective_date=?
                        AND record_kind='event' AND source=? AND source_key=?""",
                        (symbol, event_day, source, key),
                    )
                    changed_actions += conn.total_changes - before

        incoming_anchor_sources = {str(item.get("source") or "") for item in anchor_rows}
        if len(incoming_anchor_sources) > 1 or (
            incoming_anchor_sources and not _known_source(next(iter(incoming_anchor_sources)))
        ):
            raise ValueError("A finite anchor update must use one explicit source")
        for anchor in anchor_rows:
            day = _day(anchor["effective_date"])
            source = str(anchor.get("source") or "")
            value = _positive(anchor.get("source_cumulative_factor"), "source factor")
            key = str(anchor.get("source_key") or f"{source}:{day}")
            payload = json.dumps(
                dict(anchor), sort_keys=True, ensure_ascii=False, allow_nan=False, default=str
            )
            before = conn.total_changes
            conn.execute(
                """INSERT INTO corporate_actions
                (symbol,effective_date,record_kind,source,source_key,payload_json,
                 source_cumulative_factor,updated_at) VALUES (?,?,'factor_anchor',?,?,?,?,?)
                ON CONFLICT(symbol,effective_date,record_kind,source,source_key) DO UPDATE SET
                payload_json=excluded.payload_json,
                source_cumulative_factor=excluded.source_cumulative_factor,
                updated_at=excluded.updated_at
                WHERE payload_json IS NOT excluded.payload_json
                   OR source_cumulative_factor IS NOT excluded.source_cumulative_factor""",
                (symbol, day, source, key, payload, value, time.time_ns() // 1000),
            )
            changed_anchors += conn.total_changes - before
        if anchor_range:
            source, start, end = anchor_range
            if incoming_anchor_sources and incoming_anchor_sources != {source}:
                raise ValueError("anchors_complete_range source must match its input collection")
            incoming_keys = {
                str(item.get("source_key") or f"{source}:{_day(item['effective_date'])}")
                for item in anchor_rows
            }
            existing = conn.execute(
                """SELECT effective_date,source_key FROM corporate_actions
                WHERE symbol=? AND record_kind='factor_anchor' AND source=?
                  AND effective_date BETWEEN ? AND ?""",
                (symbol, source, start, end),
            ).fetchall()
            for anchor_day, key in existing:
                if key not in incoming_keys:
                    before = conn.total_changes
                    conn.execute(
                        """DELETE FROM corporate_actions WHERE symbol=? AND effective_date=?
                        AND record_kind='factor_anchor' AND source=? AND source_key=?""",
                        (symbol, anchor_day, source, key),
                    )
                    changed_anchors += conn.total_changes - before

        factor_update_requested = (
            actions_supplied
            or anchors_supplied
            or verified_no_event_range is not None
            or factor_range is not None
        )
        if factor_update_requested:
            stored_anchors = [
                {
                    "effective_date": day,
                    "source": source,
                    "source_key": key,
                    "source_cumulative_factor": value,
                }
                for day, source, key, value in conn.execute(
                    """SELECT effective_date,source,source_key,source_cumulative_factor
                    FROM corporate_actions WHERE symbol=? AND record_kind='factor_anchor'
                    ORDER BY effective_date""",
                    (symbol,),
                )
            ]
            stored_events = []
            for day, source, key, category, payload in conn.execute(
                """SELECT effective_date,source,source_key,category,payload_json
                FROM corporate_actions WHERE symbol=? AND record_kind='event'
                ORDER BY effective_date,source,source_key""",
                (symbol,),
            ):
                item = json.loads(payload)
                item.update(effective_date=day, source=source, source_key=key, category=category)
                stored_events.append(item)
            latest_coverage = conn.execute(
                """SELECT max(valid_through) FROM corporate_actions
                WHERE symbol=? AND record_kind='factor'""",
                (symbol,),
            ).fetchone()[0]
            persisted_baseline = conn.execute(
                """SELECT effective_date,valid_through,factor_basis
                FROM corporate_actions WHERE symbol=? AND record_kind='factor'
                  AND factor_basis LIKE '%verified_no_events:%'
                ORDER BY effective_date LIMIT 1""",
                (symbol,),
            ).fetchone()
            effective_baseline = verified_no_event_range
            if effective_baseline is None and persisted_baseline:
                marker = "verified_no_events:"
                baseline_source = persisted_baseline[2].split(marker, 1)[1]
                effective_baseline = (persisted_baseline[0], persisted_baseline[1], baseline_source)
            if stored_anchors and effective_baseline:
                raise ValueError("Source cumulative anchors cannot be mixed with an event baseline")
            baseline_for_end = effective_baseline[1] if effective_baseline else None
            through_value = factor_through or latest_coverage or baseline_for_end
            through = _day(through_value) if through_value else None
            if through is None:
                raise ValueError("A new factor chain requires factor_through")
            if latest_coverage and through < _day(latest_coverage) and not factor_range:
                raise ValueError("factor_through cannot shorten coverage without a complete range")
            new_input_dates = [_day(row["effective_date"]) for row in [*action_rows, *anchor_rows]]
            if (
                factor_through is None
                and latest_coverage
                and new_input_dates
                and max(new_input_dates) > _day(latest_coverage)
            ):
                raise ValueError("Extending factor coverage requires an explicit factor_through")
            close_map = dict()
            for event in stored_events:
                event_day = _day(event["effective_date"])
                previous = _previous_valid_close(conn, symbol, event_day)
                if previous:
                    close_map[event_day] = previous[1]
            desired = build_selected_factors(
                anchors=stored_anchors,
                events=stored_events,
                prior_closes=close_map,
                verified_no_event_range=effective_baseline,
                through=through,
            )
            desired_by_day = {item["effective_date"]: item for item in desired}
            current = {
                row[0]: row[1:]
                for row in conn.execute(
                    """SELECT effective_date,source,event_factor,cumulative_factor,
                    valid_from,valid_through,factor_basis FROM corporate_actions
                    WHERE symbol=? AND record_kind='factor'""",
                    (symbol,),
                )
            }
            route_source = _ANCHOR_SOURCE if stored_anchors else _EVENT_SOURCE
            current_routes = {values[0] for values in current.values()}
            if current_routes and current_routes != {route_source}:
                if not factor_range or not current:
                    raise ValueError(
                        "Factor source route change requires a complete replacement range"
                    )
                current_days = set(current)
                if any(not factor_range[1] <= day <= factor_range[2] for day in current_days):
                    raise ValueError(
                        "Factor replacement range must cover all existing selected rows"
                    )
            for factor_day, factor in desired_by_day.items():
                values = (
                    route_source,
                    factor["event_factor"],
                    factor["cumulative_factor"],
                    factor["valid_from"],
                    factor["valid_through"],
                    factor["factor_basis"],
                )
                existing = current.get(factor_day)
                if existing is None:
                    conn.execute(
                        """INSERT INTO corporate_actions
                        (symbol,effective_date,record_kind,source,source_key,event_factor,
                         cumulative_factor,valid_from,valid_through,factor_basis,updated_at)
                        VALUES (?,?,'factor',?,'selected',?,?,?,?,?,?)""",
                        (symbol, factor_day, *values, time.time_ns() // 1000),
                    )
                    changed_factors += 1
                elif tuple(existing) != values:
                    if existing[0] != route_source and not (
                        factor_range and factor_range[1] <= factor_day <= factor_range[2]
                    ):
                        raise ValueError(
                            "Factor source route changed outside an explicit replacement range"
                        )
                    if existing[0] != route_source:
                        conn.execute(
                            """DELETE FROM corporate_actions WHERE symbol=? AND effective_date=?
                            AND record_kind='factor'""",
                            (symbol, factor_day),
                        )
                        conn.execute(
                            """INSERT INTO corporate_actions
                            (symbol,effective_date,record_kind,source,source_key,event_factor,
                             cumulative_factor,valid_from,valid_through,factor_basis,updated_at)
                            VALUES (?,?,'factor',?,'selected',?,?,?,?,?,?)""",
                            (symbol, factor_day, *values, time.time_ns() // 1000),
                        )
                    else:
                        conn.execute(
                            """UPDATE corporate_actions SET event_factor=?,cumulative_factor=?,
                            valid_from=?,valid_through=?,factor_basis=?,updated_at=?
                            WHERE symbol=? AND effective_date=?
                              AND record_kind='factor' AND source=?""",
                            (*values[1:], time.time_ns() // 1000, symbol, factor_day, route_source),
                        )
                    changed_factors += 1
            stale_ranges = []
            if factor_range:
                stale_ranges.append(factor_range[1:])
            if anchor_range and stored_anchors:
                stale_ranges.append(anchor_range[1:])
            if action_range and not stored_anchors:
                stale_ranges.append(action_range[1:])
            for start, end in stale_ranges:
                stale = conn.execute(
                    """SELECT effective_date FROM corporate_actions WHERE symbol=?
                    AND record_kind='factor' AND effective_date BETWEEN ? AND ?""",
                    (symbol, start, end),
                ).fetchall()
                for (stale_day,) in stale:
                    if stale_day not in desired_by_day:
                        before = conn.total_changes
                        conn.execute(
                            """DELETE FROM corporate_actions WHERE symbol=? AND effective_date=?
                            AND record_kind='factor'""",
                            (symbol, stale_day),
                        )
                        changed_factors += conn.total_changes - before

        for day in selected_dates:
            row = conn.execute(
                """SELECT f.source_pre_close,f.source_pre_close_source,
                    f.source_is_st,f.source_is_st_source,b.name,b.name_as_of
                FROM daily_features f LEFT JOIN daily_bars b USING(symbol,trade_date)
                WHERE f.symbol=? AND f.trade_date=?""",
                (symbol, day),
            ).fetchone()
            if row is None:
                continue
            previous = _previous_valid_close(conn, symbol, day)
            action_inputs = []
            if previous:
                for effective, category, event_source, payload in conn.execute(
                    """SELECT effective_date,category,source,payload_json
                    FROM corporate_actions
                    WHERE symbol=? AND record_kind='event'
                      AND effective_date>? AND effective_date<=?
                    ORDER BY effective_date,source,source_key""",
                    (symbol, previous[0], day),
                ):
                    item = json.loads(payload)
                    item.update(effective_date=effective, category=category, source=event_source)
                    action_inputs.append(item)
            stored_pre = (
                [(row[0], row[1])]
                if row[0] is not None and row[1] and not row[1].startswith("raw_fallback:")
                else []
            )
            direct_pre_close = [*dated_pre_close.get(day, ()), *stored_pre]
            stored_st = (
                [(row[2], row[3])]
                if row[2] is not None and row[3] and not row[3].startswith("raw_fallback:")
                else []
            )
            direct_st = [*dated_st.get(day, ()), *stored_st]
            preclose = select_reference_pre_close(
                dated=direct_pre_close,
                previous_close=previous[1] if previous else None,
                actions=action_inputs,
                actions_covered=day in covered_action_days,
                raw_candidate=(row[0], row[1]),
            )
            st_value, st_source, name_day = select_is_st(
                dated=direct_st, name=row[4], name_as_of=row[5], trade_date=day
            )
            retained_pre_close = next(
                (
                    (float(value), source)
                    for value, source in direct_pre_close
                    if value is not None and source and not source.startswith("raw_fallback:")
                ),
                (row[0], row[1]),
            )
            retained_st = next(
                (
                    (bool(value), source)
                    for value, source in direct_st
                    if value is not None and source and not source.startswith("raw_fallback:")
                ),
                (row[2], row[3]),
            )
            values = (
                retained_pre_close[0],
                retained_pre_close[1],
                retained_st[0],
                retained_st[1],
                preclose[0],
                preclose[1],
                st_value,
                st_source,
                name_day,
            )
            before = conn.total_changes
            conn.execute(
                """UPDATE daily_features SET source_pre_close=?,source_pre_close_source=?,
                source_is_st=?,source_is_st_source=?,pre_close=?,pre_close_source=?,is_st=?,
                is_st_source=?,is_st_name_date=?,updated_at=? WHERE symbol=? AND trade_date=?
                AND (source_pre_close IS NOT ? OR source_pre_close_source IS NOT ?
                     OR source_is_st IS NOT ? OR source_is_st_source IS NOT ?
                     OR pre_close IS NOT ? OR pre_close_source IS NOT ? OR is_st IS NOT ?
                     OR is_st_source IS NOT ? OR is_st_name_date IS NOT ?)""",
                (*values, time.time_ns() // 1000, symbol, day, *values),
            )
            changed_features += conn.total_changes - before
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    return {
        "actions": changed_actions,
        "anchors": changed_anchors,
        "factors": changed_factors,
        "features": changed_features,
    }


def _coverage_events(events: Iterable[Mapping[str, Any]], source: str, *, categories=(1, 2, 5)):
    """Normalize a complete finite event set without guessing duplicate identities."""
    rows = [dict(item) for item in events]
    slots: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for item in rows:
        if item.get("source", source) != source:
            raise ValueError("Coverage events must match the explicit source")
        item["source"] = source
        day = _day(item.get("effective_date", item.get("date")))
        item["effective_date"] = day
        category = int(item["category"])
        if category not in categories:
            raise ValueError(f"Unsupported corporate action category {category}")
        slots[(day, category)].append(item)
    for (day, category), group in slots.items():
        for item in group:
            key = item.get("business_key") or item.get("event_id") or item.get("source_key")
            if not key:
                if len(group) > 1 and item.get("event_slot") is None:
                    raise ValueError("Multiple coverage events need stable keys or event slots")
                key = f"{source}:{category}:{day}:{item.get('event_slot', '0')}"
            item["source_key"] = str(key)
    normalized = [item for group in normalize_actions(rows) for item in group["events"]]
    result = {}
    for item in normalized:
        key = (item["effective_date"], item["source_key"])
        if key in result and _coverage_event_value(result[key]) != _coverage_event_value(item):
            raise ValueError("Conflicting coverage event key")
        result[key] = item
    return result


def _coverage_event_value(item: Mapping[str, Any]) -> str:
    """Compare economic values, accepting equivalent legacy/per-share encodings."""
    canonical = _canonical_event(item)
    for key in ("date", "source_origins", "fenhong", "songzhuangu", "peigu", "peigujia"):
        canonical.pop(key, None)
    return json.dumps(canonical, sort_keys=True, allow_nan=False, default=str)


def _coverage_previous_close(conn: sqlite3.Connection, symbol: str, before: str):
    # calc_status can still describe the old bar while an updater is inserting
    # new OHLC. Actual turnover or confirmed trading status establishes trading.
    return conn.execute(
        """SELECT b.trade_date,b.close FROM daily_bars b
        LEFT JOIN daily_features f USING(symbol,trade_date)
        WHERE b.symbol=? AND b.trade_date<?
          AND b.open>0 AND b.high>0 AND b.low>0 AND b.close>0
          AND abs(b.open)<1e308 AND abs(b.high)<1e308
          AND abs(b.low)<1e308 AND abs(b.close)<1e308
          AND b.low<=min(b.open,b.close) AND b.high>=max(b.open,b.close)
          AND upper(coalesce(f.trading_status,'')) NOT IN
              ('SUSPENDED','停牌','NO_TRADE','INVALID','0')
          AND (b.volume>0 OR b.amount>0 OR upper(f.trading_status) IN
              ('TRADING','TRADED','NORMAL','正常交易','1'))
        ORDER BY b.trade_date DESC LIMIT 1""",
        (symbol, before),
    ).fetchone()


def advance_factor_coverage(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    verified_start: str,
    verified_end: str,
    events: Iterable[Mapping[str, Any]],
    source: str = "tdx:xdxr",
) -> dict[str, Any]:
    """Advance an existing selected scale using a verified complete event interval.

    Requires an outer transaction; joins its writer snapshot and never commits
    it. Historical overlap is accepted only against this function's persisted
    coverage receipt and identical stored events. Unverifiable history requires
    explicit maintenance. affected_from is the former coverage end plus one.
    """
    if not conn.in_transaction:
        raise ValueError("advance_factor_coverage requires an outer write transaction")
    start, end = _day(verified_start), _day(verified_end)
    if not symbol or end < start:
        raise ValueError("Invalid verified coverage interval")
    if not _known_source(source) or source.startswith(("raw_fallback:", "derived:")):
        raise ValueError("Coverage needs an explicit reliable event source")
    incoming = _coverage_events(events, source)
    if any(not start <= day <= end for day, _ in incoming):
        raise ValueError("Coverage event outside verified interval")
    savepoint = "fw03_advance_factor_coverage"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        # Acquire the writer before taking any source/cache snapshot. No rows
        # are touched, and releasing this savepoint keeps the caller's BEGIN.
        conn.execute("UPDATE corporate_actions SET updated_at=updated_at WHERE 0")
        cache = [
            dict(
                zip(
                    (
                        "effective_date",
                        "source",
                        "source_key",
                        "cumulative_factor",
                        "event_factor",
                        "valid_from",
                        "valid_through",
                        "factor_basis",
                        "payload_json",
                        "updated_at",
                    ),
                    row,
                )
            )
            for row in conn.execute(
                """SELECT effective_date,source,source_key,cumulative_factor,event_factor,
                valid_from,valid_through,factor_basis,payload_json,updated_at
                FROM corporate_actions WHERE symbol=? AND record_kind='factor'
                ORDER BY effective_date""",
                (symbol,),
            )
        ]
        if not cache:
            raise ValueError("Missing selected factor anchor")
        previous = None
        for item in cache:
            first, last = _day(item["valid_from"]), _day(item["valid_through"])
            cumulative = _positive(item["cumulative_factor"], "selected cumulative factor")
            if cumulative is None or first != item["effective_date"] or first > last:
                raise ValueError("Invalid selected factor cache")
            if not _known_source(item["source"]) or not item["factor_basis"].startswith(
                ("source_cumulative_factor:source_anchor:", "event_chain:", "anchor_continuation:")
            ):
                raise ValueError("Unverified selected factor anchor basis")
            if previous:
                if (
                    first
                    != (
                        date.fromisoformat(previous["valid_through"]) + timedelta(days=1)
                    ).isoformat()
                ):
                    raise ValueError("Selected factor coverage gap or overlap")
                if item["event_factor"] is not None and not math.isclose(
                    _positive(item["event_factor"], "selected event factor"),
                    cumulative / previous["cumulative_factor"],
                    rel_tol=1e-12,
                ):
                    raise ValueError("Inconsistent selected factor scale")
            previous = item
        tail = cache[-1]
        old_end = tail["valid_through"]
        next_day = (date.fromisoformat(old_end) + timedelta(days=1)).isoformat()
        if start < cache[0]["valid_from"] or start > next_day:
            raise ValueError("Verified interval outside existing coverage or coverage gap")
        tail_payload = json.loads(tail["payload_json"] or "{}")
        if not isinstance(tail_payload, dict):
            raise ValueError("Unverifiable factor coverage payload")
        receipt = tail_payload.get("fw03_advance")
        if receipt and (
            receipt.get("version") != 1
            or receipt.get("source") != source
            or receipt.get("verified_end") != old_end
            or not isinstance(receipt.get("events"), list)
        ):
            raise ValueError("Unverifiable factor coverage receipt or source change")
        ledger = {(day, key): value for day, key, value in receipt["events"]} if receipt else {}
        if receipt:
            if len(ledger) != len(receipt["events"]) or any(
                not receipt["verified_start"] <= day <= old_end for day, _ in ledger
            ):
                raise ValueError("Unverifiable factor coverage event receipt")
            root = receipt["anchor"]
            cached_root = next(
                (row for row in cache if row["effective_date"] == root["effective_date"]), None
            )
            if cached_root is None or any(
                cached_root[name] != root[name]
                for name in ("source", "cumulative_factor", "factor_basis")
            ):
                raise ValueError("Verified root anchor changed; explicit maintenance required")
        if start <= old_end and (not receipt or start < _day(receipt["verified_start"])):
            raise ValueError(
                "Historical overlap lacks verified event coverage; explicit maintenance required"
            )
        stored_rows = []
        for day, event_source, key, category, payload in conn.execute(
            """SELECT effective_date,source,source_key,category,payload_json
            FROM corporate_actions WHERE symbol=? AND record_kind='event'
              AND effective_date BETWEEN ? AND ? ORDER BY effective_date,source,source_key""",
            (symbol, start, end),
        ):
            if event_source != source:
                raise ValueError("Cannot verify overlapping events from another source")
            item = json.loads(payload)
            item.update(effective_date=day, source=event_source, source_key=key, category=category)
            # Migration retained SDK payloads verbatim; these amounts are already
            # per share. Generic legacy event inputs otherwise remain per ten.
            if (
                event_source == "tdx:xdxr"
                and category == 1
                and re.fullmatch(r"category=1:slot=[1-9]\d*", key)
            ):
                for canonical, raw in (
                    ("cash_dividend_per_share", "fenhong"),
                    ("bonus_shares_per_share", "songzhuangu"),
                    ("rights_shares_per_share", "peigu"),
                    ("rights_price", "peigujia"),
                ):
                    item.setdefault(canonical, item.get(raw))
            stored_rows.append(item)
        stored = _coverage_events(stored_rows, source)
        historical = {
            key: value for key, value in ledger.items() if start <= key[0] <= min(end, old_end)
        }
        for collection in (incoming, stored):
            overlap = {
                key: _coverage_event_value(item)
                for key, item in collection.items()
                if key[0] <= old_end
            }
            if overlap != historical:
                raise ValueError("Historical event set changed; explicit maintenance required")
        for key, item in stored.items():
            if key not in incoming or _coverage_event_value(item) != _coverage_event_value(
                incoming[key]
            ):
                raise ValueError(
                    "Historical event revision or omission; explicit maintenance required"
                )
        for key in incoming:
            if key[0] <= old_end and key not in stored:
                raise ValueError("Retrospective event insertion; explicit maintenance required")
        result = {
            "changed_factor_dates": [],
            "changed_rows": 0,
            "changed_event_rows": 0,
            "affected_from": None,
            "affected_through": None,
            "valid_through": old_end,
        }
        if end > old_end:
            if conn.execute(
                """SELECT 1 FROM corporate_actions WHERE symbol=? AND record_kind='factor_anchor'
                AND effective_date>? AND effective_date<=? LIMIT 1""",
                (symbol, old_end, end),
            ).fetchone():
                raise ValueError(
                    "New source anchor inside continuation interval requires explicit maintenance"
                )
            stamp = max(
                time.time_ns() // 1000,
                conn.execute(
                    "SELECT coalesce(max(updated_at),0)+1 FROM corporate_actions WHERE symbol=?",
                    (symbol,),
                ).fetchone()[0],
            )

            def timestamp():
                nonlocal stamp
                stamp += 1
                return stamp

            root = (
                receipt["anchor"]
                if receipt
                else {
                    "effective_date": tail["effective_date"],
                    "cumulative_factor": tail["cumulative_factor"],
                    "source": tail["source"],
                    "factor_basis": tail["factor_basis"],
                }
            )
            proof = {
                "version": 1,
                "source": source,
                "verified_start": receipt["verified_start"] if receipt else start,
                "verified_end": end,
                "anchor": root,
                "events": [
                    [day, key, value]
                    for (day, key), value in sorted(
                        {
                            **ledger,
                            **{
                                key: _coverage_event_value(item)
                                for key, item in incoming.items()
                                if key[0] > old_end
                            },
                        }.items()
                    )
                ],
            }

            def proof_through(last):
                return dict(
                    proof,
                    verified_end=last,
                    events=[row for row in proof["events"] if row[0] <= last],
                )

            new_segments = []
            running = tail["cumulative_factor"]
            grouped = normalize_actions(
                item for (day, _), item in incoming.items() if day > old_end
            )
            for group in grouped:
                if not any(item["category"] == 1 for item in group["events"]):
                    continue
                day = group["effective_date"]
                prior = _coverage_previous_close(conn, symbol, day)
                if not prior:
                    raise ValueError(f"Missing prior effective traded close for {day}")
                prior_day, raw_close = prior
                prior_factor = None
                if prior_day <= old_end:
                    prior_factor = next(
                        (
                            row["cumulative_factor"]
                            for row in cache
                            if row["valid_from"] <= prior_day <= row["valid_through"]
                        ),
                        None,
                    )
                else:
                    prior_factor = tail["cumulative_factor"]
                    for row in new_segments:
                        if row["effective_date"] <= prior_day:
                            prior_factor = row["cumulative_factor"]
                if prior_factor is None:
                    raise ValueError("Prior traded close is outside verified factor coverage")
                scaled_close = raw_close * prior_factor / running
                ratio = _event_ratio(scaled_close, group["events"])
                if ratio == 1.0:
                    continue
                running = _positive(running * ratio, "continued cumulative factor")
                new_segments.append(
                    {"effective_date": day, "event_factor": ratio, "cumulative_factor": running}
                )
            tail_end = (
                (
                    date.fromisoformat(new_segments[0]["effective_date"]) - timedelta(days=1)
                ).isoformat()
                if new_segments
                else end
            )
            if tail_end != old_end:
                tail_payload["fw03_advance"] = proof_through(tail_end)
                conn.execute(
                    """UPDATE corporate_actions SET valid_through=?,payload_json=?,updated_at=?
                    WHERE symbol=? AND effective_date=? AND record_kind='factor'""",
                    (
                        tail_end,
                        json.dumps(tail_payload, sort_keys=True, allow_nan=False),
                        timestamp(),
                        symbol,
                        tail["effective_date"],
                    ),
                )
                result["changed_factor_dates"].append(tail["effective_date"])
            for index, row in enumerate(new_segments):
                last = (
                    (
                        date.fromisoformat(new_segments[index + 1]["effective_date"])
                        - timedelta(days=1)
                    ).isoformat()
                    if index + 1 < len(new_segments)
                    else end
                )
                payload = json.dumps(
                    {"fw03_advance": proof_through(last)},
                    sort_keys=True,
                    allow_nan=False,
                )
                basis = (
                    f"anchor_continuation:{root['source']}:{root['effective_date']}:events:{source}"
                )
                conn.execute(
                    """INSERT INTO corporate_actions
                    (symbol,effective_date,record_kind,source,source_key,event_factor,cumulative_factor,
                     valid_from,valid_through,factor_basis,payload_json,updated_at)
                    VALUES (?,?,'factor',?,'selected',?,?,?,?,?,?,?)""",
                    (
                        symbol,
                        row["effective_date"],
                        f"derived:anchor_continuation:{source}",
                        row["event_factor"],
                        row["cumulative_factor"],
                        row["effective_date"],
                        last,
                        basis,
                        payload,
                        timestamp(),
                    ),
                )
                result["changed_factor_dates"].append(row["effective_date"])
            for key, item in incoming.items():
                if key not in stored:
                    _insert_action(conn, symbol, item, source, updated_at=timestamp())
                    result["changed_event_rows"] += 1
            result.update(
                changed_rows=len(result["changed_factor_dates"]) + result["changed_event_rows"],
                affected_from=next_day,
                affected_through=end,
                valid_through=end,
            )
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        return result
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
