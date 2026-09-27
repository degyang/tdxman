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
    for raw in events:
        event = dict(raw)
        effective = _day(event.get("effective_date", event.get("date")))
        category = event.get("category")
        if category is None:
            raise ValueError("Corporate action category is required")
        event["effective_date"] = effective
        event["category"] = int(category)
        event = _canonical_event(event)
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
        if number is not None and source and not source.startswith("raw_fallback:"):
            reliable.append((number, source))
    if len({value for value, _ in reliable}) > 1:
        raise ValueError("Conflicting reliable dated reference prices")
    if reliable:
        return reliable[0]
    prior = _positive(previous_close, "previous close")
    if prior is not None and actions_covered:
        reference = prior
        for batch in normalize_actions(actions):
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
    """Use dated ST, then a name proved for that exact day; otherwise unknown."""
    day = _day(trade_date)
    known = [
        (bool(value), source)
        for value, source in dated
        if value is not None and source and not source.startswith("raw_fallback:")
    ]
    if len({value for value, _ in known}) > 1:
        raise ValueError("Conflicting reliable dated ST values")
    if known:
        return known[0][0], known[0][1], None
    if name is not None and name_as_of is not None and _day(name_as_of) == day:
        value = classify_st_name(name)
        if value is not None:
            return value, "dated_name", day
    return None, None, None


def build_selected_factors(
    *,
    anchors: Iterable[Mapping[str, Any]] = (),
    events: Iterable[Mapping[str, Any]] = (),
    prior_closes: Mapping[str, float] | None = None,
    through: str,
) -> list[dict[str, Any]]:
    """Build sparse intervals from exactly one route; source anchors are not cumprod'd."""
    end = _day(through)
    anchor_rows = list(anchors)
    if anchor_rows:
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
        for item in events:
            category = int(item["category"])
            if category in (2, 5):
                continue
            grouped_events[_day(item.get("effective_date", item.get("date")))].append(dict(item))
        selected = []
        running = 1.0
        for day, same_day in sorted(grouped_events.items()):
            if day > end:
                continue
            close = _positive((prior_closes or {}).get(day), f"prior close for {day}")
            if close is None:
                raise ValueError(f"Missing prior close for event factor on {day}")
            running *= _event_ratio(close, same_day)
            if not math.isfinite(running) or running <= 0:
                raise ValueError("Invalid event cumulative factor")
            selected.append((day, running, "event_chain"))
        route = "event_chain"
    result = []
    for index, (day, cumulative, basis) in enumerate(selected):
        next_day = selected[index + 1][0] if index + 1 < len(selected) else None
        through_day = (
            min(date.fromisoformat(end), date.fromisoformat(next_day) - timedelta(days=1))
            if next_day
            else date.fromisoformat(end)
        )
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
    conn: sqlite3.Connection, symbol: str, item: Mapping[str, Any], source: str
) -> None:
    day = _day(item.get("effective_date", item.get("date")))
    category = int(item["category"])
    payload = json.dumps(
        dict(item), ensure_ascii=False, sort_keys=True, allow_nan=False, default=str
    )
    supplied_key = item.get("source_key") or item.get("event_id")
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
        (symbol, day, source, key, category, payload, time.time_ns() // 1000),
    )


def update_reference_factors(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    dates: Iterable[str],
    actions: Iterable[Mapping[str, Any]] = (),
    anchors: Iterable[Mapping[str, Any]] = (),
    prior_closes: Mapping[str, float] | None = None,
    factor_through: str,
    dated_pre_close: Mapping[str, Sequence[tuple[float | None, str | None]]] | None = None,
    dated_st: Mapping[str, Sequence[tuple[bool | None, str | None]]] | None = None,
    actions_covered_dates: Iterable[str] = (),
) -> dict[str, int]:
    """Atomically update selected actions, selected factors, pre-close and ST fields.

    Existing source fields and unrelated derived columns are preserved. An
    identical second call performs no feature writes and keeps updated_at.
    """
    selected_dates = sorted({_day(value) for value in dates})
    if not symbol or not selected_dates:
        return {"actions": 0, "factors": 0, "features": 0}
    action_rows, anchor_rows = [dict(x) for x in actions], [dict(x) for x in anchors]
    action_rows = [
        normalized for group in normalize_actions(action_rows) for normalized in group["events"]
    ]
    action_slots: dict[tuple[str, int], list[Mapping[str, Any]]] = defaultdict(list)
    for action in action_rows:
        key = (_day(action.get("effective_date", action.get("date"))), int(action["category"]))
        action_slots[key].append(action)
    for key, same_day in action_slots.items():
        if len(same_day) > 1 and any(
            not (row.get("source_key") or row.get("event_id") or row.get("event_slot") is not None)
            for row in same_day
        ):
            raise ValueError(
                f"Multiple same-category events on {key[0]} need stable source keys or event slots"
            )
    factor_closes = dict(prior_closes or {})
    if not anchor_rows:
        for action in action_rows:
            event_day = _day(action.get("effective_date", action.get("date")))
            if event_day not in factor_closes:
                prior = conn.execute(
                    """SELECT close FROM daily_bars
                    WHERE symbol=? AND trade_date<? AND close>0
                    ORDER BY trade_date DESC LIMIT 1""",
                    (symbol, event_day),
                ).fetchone()
                if prior:
                    factor_closes[event_day] = prior[0]
    factors = build_selected_factors(
        anchors=anchor_rows, events=action_rows, prior_closes=factor_closes, through=factor_through
    )
    dated_pre_close, dated_st = dated_pre_close or {}, dated_st or {}
    covered_action_days = {_day(value) for value in actions_covered_dates}
    changed_actions = changed_features = changed_factors = 0
    savepoint = "fw03_reference_factors"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        for action in action_rows:
            before = conn.total_changes
            _insert_action(conn, symbol, action, "tdx:xdxr")
            changed_actions += conn.total_changes - before
        stored_factors = conn.execute(
            """SELECT effective_date,source,event_factor,cumulative_factor,
            valid_from,valid_through,factor_basis FROM corporate_actions
            WHERE symbol=? AND record_kind='factor' ORDER BY effective_date""",
            (symbol,),
        ).fetchall()
        factor_source = _ANCHOR_SOURCE if anchor_rows else _EVENT_SOURCE
        desired_factors = [
            (
                f["effective_date"],
                factor_source,
                f["event_factor"],
                f["cumulative_factor"],
                f["valid_from"],
                f["valid_through"],
                f["factor_basis"],
            )
            for f in factors
        ]
        if stored_factors != desired_factors:
            changed_factors = len(set(stored_factors).symmetric_difference(desired_factors))
            conn.execute(
                "DELETE FROM corporate_actions WHERE symbol=? AND record_kind='factor'", (symbol,)
            )
            for factor in factors:
                conn.execute(
                    """INSERT INTO corporate_actions
                    (symbol,effective_date,record_kind,source,source_key,event_factor,cumulative_factor,
                     valid_from,valid_through,factor_basis,updated_at)
                    VALUES (?,?,'factor',?,'selected',?,?,?,?,?,?)""",
                    (
                        symbol,
                        factor["effective_date"],
                        factor_source,
                        factor["event_factor"],
                        factor["cumulative_factor"],
                        factor["valid_from"],
                        factor["valid_through"],
                        factor["factor_basis"],
                        time.time_ns() // 1000,
                    ),
                )
        for anchor in anchor_rows:
            day = _day(anchor["effective_date"])
            source = str(anchor.get("source") or "source")
            value = _positive(anchor.get("source_cumulative_factor"), "source factor")
            key = str(anchor.get("source_key") or f"{source}:{day}")
            payload = json.dumps(
                dict(anchor), sort_keys=True, ensure_ascii=False, allow_nan=False, default=str
            )
            conn.execute(
                """INSERT INTO corporate_actions
                (symbol,effective_date,record_kind,source,source_key,payload_json,
                 source_cumulative_factor,updated_at) VALUES (?,?,'factor_anchor',?,?,?,?,?)
                ON CONFLICT(symbol,effective_date,record_kind,source,source_key) DO UPDATE SET
                payload_json=excluded.payload_json,source_cumulative_factor=excluded.source_cumulative_factor,
                updated_at=excluded.updated_at WHERE payload_json IS NOT excluded.payload_json
                OR source_cumulative_factor IS NOT excluded.source_cumulative_factor""",
                (symbol, day, source, key, payload, value, time.time_ns() // 1000),
            )
        for day in selected_dates:
            row = conn.execute(
                """SELECT f.source_pre_close,f.source_pre_close_source,
                    f.source_is_st,f.source_is_st_source,b.close,b.name,b.name_as_of
                FROM daily_features f LEFT JOIN daily_bars b USING(symbol,trade_date)
                WHERE f.symbol=? AND f.trade_date=?""",
                (symbol, day),
            ).fetchone()
            if row is None:
                continue
            previous = conn.execute(
                """SELECT trade_date,close FROM daily_bars
                WHERE symbol=? AND trade_date<? AND close>0 ORDER BY trade_date DESC LIMIT 1""",
                (symbol, day),
            ).fetchone()
            action_inputs = []
            if previous:
                for effective, category, payload in conn.execute(
                    """SELECT effective_date,category,payload_json
                    FROM corporate_actions WHERE symbol=? AND record_kind='event'
                      AND effective_date>? AND effective_date<=? ORDER BY effective_date""",
                    (symbol, previous[0], day),
                ):
                    parsed = json.loads(payload)
                    parsed.update(effective_date=effective, category=category)
                    action_inputs.append(parsed)
            direct_pre_close = dated_pre_close.get(day, ())
            direct_st = dated_st.get(day, ((row[2], row[3]),))
            preclose = select_reference_pre_close(
                dated=direct_pre_close,
                previous_close=previous[1] if previous else None,
                actions=action_inputs,
                actions_covered=day in covered_action_days,
                raw_candidate=(row[0], row[1]),
            )
            st_value, st_source, name_day = select_is_st(
                dated=direct_st,
                name=row[5],
                name_as_of=row[6],
                trade_date=day,
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
        "factors": changed_factors,
        "features": changed_features,
    }
