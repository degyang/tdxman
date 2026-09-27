"""Reconstruct dated daily indicators before publishing price-limit events."""

from __future__ import annotations

import json
import logging
import math
from bisect import bisect_left, bisect_right
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from itertools import product
from pathlib import Path
from time import perf_counter
from uuid import uuid4
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

from .daily_storage import _same_values
from .fetch import client_factory, fetch_sync
from .index_lists import atomic_json
from .pool import pool_lock, writer
from .security_facts import calendar_days, daily_facts
from .store import daily_paths, read_only_catalog

LOG = logging.getLogger(__name__)
VERSION = "daily-enrichment-v2"
METRICS = (
    "pre_close",
    "vol_ratio",
    "turnover_rate",
    "amplitude",
    "pct_chg",
    "float_share",
    "total_share",
    "float_mv",
    "total_mv",
)


def valid(value, *, zero=False):
    try:
        return (
            value is not None
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and (float(value) >= 0 if zero else float(value) > 0)
        )
    except (ValueError, TypeError):
        return False


def cents(value):
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def usable_source(source, code=None):
    """An empty protocol record cannot establish absence of corporate actions."""
    if not source:
        return False
    finance = source.get("finance", {})
    response_code = finance.get("code")
    return bool(response_code and (code is None or response_code == code))


def _fetch(client, job):
    from tdxman.models.enums import Market

    code, market = job
    events = client.get_xdxr_info(Market[market], code)
    finance = client.get_finance_info(Market[market], code)
    if finance.empty or str(finance.iloc[0].get("code", "")) != code:
        raise ValueError("Empty finance response; corporate-action coverage unconfirmed")
    return dict(
        events=json.loads(events.to_json(orient="records", date_format="iso")),
        finance=json.loads(finance.to_json(orient="records"))[0],
        fetched_date=datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
    )


def _event_map(source):
    result = {}
    for event in source.get("events", []):
        day = date.fromisoformat(str(event["date"])[:10])
        result.setdefault(day, []).append(event)
    return result


def _share_timeline(events):
    """Use dated capital records; invalidate anchors at unresolved capital actions."""
    days, values = [], []
    floating = total = None
    for day, group in sorted(events.items()):
        if any(
            int(e["category"]) in (11, 12)
            or (int(e["category"]) == 1 and (valid(e.get("songzhuangu")) or valid(e.get("peigu"))))
            for e in group
        ):
            floating = total = None
        for event in group:
            if 2 <= int(event["category"]) <= 10:
                floating = (
                    float(event["panhou_liutong"]) * 10000
                    if valid(event.get("panhou_liutong"))
                    else None
                )
                total = (
                    float(event["hou_zongguben"]) * 10000
                    if valid(event.get("hou_zongguben"))
                    else None
                )
        days.append(day)
        values.append((floating, total))
    return days, values


def _reference(previous, events):
    value = Decimal(str(previous))
    actions = [e for e in events if int(e["category"]) == 1]
    if len(actions) > 1 or any(int(e["category"]) in (11, 12) for e in events):
        return None
    for event in actions:
        names = ("fenhong", "songzhuangu", "peigu", "peigujia")
        if not all(valid(event.get(k), zero=True) for k in names):
            return None
        cash, bonus, rights, price = [Decimal(str(event[k])) for k in names]
        # Protocol values are float32. Do not choose a cent at a rounding
        # boundary when binary decoding/JSON precision can change that cent.
        bounds = []
        for parameter in (cash, bonus, rights, price):
            error = abs(parameter) / Decimal(2**23) + Decimal("0.000000001")
            bounds.append((max(Decimal(0), parameter - error), parameter + error))
        possible = {
            cents((value - c + r * p) / (1 + b + r))
            for c, b, r, p in product(*bounds)
        }
        if len(possible) != 1:
            return None
        value = (value - cash + rights * price) / (1 + bonus + rights)
    return cents(value) if value > 0 else None


def derive_rows(rows, facts, source, sessions, start, end):
    """Return only changed optional values; preserve every original OHLCV value."""
    from .limit_events import _reference_price

    source = source if usable_source(source) else None
    events = _event_map(source) if source else {}
    share_days, shares = _share_timeline(events)
    positions = {day: i for i, day in enumerate(sessions)}
    by_day = {r["trade_date"]: r for r in rows}
    counts, missing, conflicts, changes = Counter(), Counter(), [], []
    previous = None
    for original in rows:
        day = original["trade_date"]
        if not start <= day <= end:
            if valid(original.get("close")):
                previous = original
            continue
        row = dict(original)
        fact = facts.get(day, {})
        pos = positions.get(day)
        predecessor = sessions[pos - 1] if pos is not None and pos else None
        row.update(
            previous_trade_date=previous["trade_date"] if previous else None,
            previous_close=previous.get("close") if previous else None,
        )
        if valid(fact.get("pre_close")):
            row["pre_close"] = fact["pre_close"]
            row["pre_close_source"] = fact["source"]
        # A recomputed return is an output, not independent price evidence.
        evidence = dict(row)
        if row.get("pct_chg_source") == "derived:close_pre_close":
            evidence["pct_chg"] = None
        reference, audit = _reference_price(evidence, predecessor)
        invalid_prices = audit and audit["basis"] == "ohlc_not_on_price_grid"
        if audit:
            row["pre_close"] = reference
            row["pre_close_source"] = "derived:" + audit["basis"]
        # Corporate actions and the previous unadjusted close determine the
        # reference. A stale/derived percentage must not veto those inputs.
        if (not invalid_prices and not valid(fact.get("pre_close")) and source and previous
                and previous["trade_date"] == predecessor):
            candidate = _reference(previous["close"], events.get(day, []))
            previous_on_grid = abs(previous["close"] - cents(previous["close"])) <= max(
                .00001, abs(previous["close"]) * 1.5e-7)
            if candidate is not None and previous_on_grid:
                reference = candidate
                row["pre_close_source"] = "derived:tdx_xdxr"
        row["pre_close"] = cents(reference) if valid(reference) else None
        reference = row["pre_close"]
        if invalid_prices:
            conflicts.append({"date": str(day), "field": "ohlc",
                              "reason": "ohlc_not_on_price_grid"})
        if not valid(reference):
            # Values derived from a rejected reference must not remain usable.
            row["pct_chg"] = None
            row["amplitude"] = None
        if valid(reference) and valid(row.get("close")):
            row["pct_chg"] = (float(row["close"]) / float(reference) - 1) * 100
            row["pct_chg_source"] = "derived:close_pre_close"
            if valid(row.get("high")) and valid(row.get("low")):
                row["amplitude"] = (float(row["high"]) - float(row["low"])) / reference * 100
                row["amplitude_source"] = "derived:high_low_pre_close"
        # The five-session close ratio excludes today's volume. Missing sessions
        # remain unknown; explicit full-day suspension contributes zero volume.
        if pos is not None and pos >= 5 and valid(row.get("volume"), zero=True):
            baseline = []
            for prior_day in sessions[pos - 5 : pos]:
                f = facts.get(prior_day, {})
                v = by_day.get(prior_day, {}).get("volume")
                if f.get("trading_status") == "SUSPENDED":
                    v = 0.0
                if not valid(v, zero=True):
                    break
                baseline.append(float(v))
            if len(baseline) == 5 and sum(baseline) > 0:
                row["vol_ratio"] = float(row["volume"]) * 5 / sum(baseline)
                row["vol_ratio_source"] = "derived:five_market_sessions_close"
            else:
                row["vol_ratio"] = None
                row["vol_ratio_source"] = "unknown:incomplete_five_session_baseline"
        index = bisect_right(share_days, day) - 1
        floating, total = shares[index] if index >= 0 else (None, None)
        share_source = "tdx:xdxr_capital"
        if source and day.isoformat() == source["fetched_date"]:
            finance = source["finance"]
            floating, total = finance.get("liutong_guben"), finance.get("zong_guben")
            share_source = "tdx:finance_snapshot"
        for field, value in (("float_share", floating), ("total_share", total)):
            mv = "float_mv" if field == "float_share" else "total_mv"
            if valid(value):
                row[field] = value
                row[field + "_source"] = share_source
                if valid(row.get("close")):
                    row[mv] = float(value) * float(row["close"])
                    row[mv + "_source"] = "derived:" + share_source
            else:
                row[field] = row[mv] = None
                row[field + "_source"] = "unknown:dated_capital_unavailable"
                row[mv + "_source"] = "unknown:dated_capital_unavailable"
        if valid(floating) and valid(row.get("volume"), zero=True):
            # Preserve source-provided historical turnover; replace values from
            # this derivation when its capital anchor or volume changes.
            rate_source = str(row.get("turnover_rate_source", ""))
            if rate_source != "baostock":
                row["turnover_rate"] = float(row["volume"]) / float(floating) * 100
                row["turnover_rate_source"] = "derived:" + share_source
        elif row.get("turnover_rate_source") != "baostock":
            row["turnover_rate"] = row["turnover"] = None
            row["turnover_rate_source"] = "unknown:dated_capital_unavailable"
        for field in METRICS:
            value = row.get(field, row.get("turnover") if field == "turnover_rate" else None)
            if value is None or not math.isfinite(float(value)):
                missing[field] += 1
            if row.get(field) != original.get(field) and not _same_values(
                {field: row.get(field)}, {field: original.get(field)}
            ):
                counts[field] += 1
        row.pop("previous_trade_date", None)
        row.pop("previous_close", None)
        if not _same_values(original, row):
            changes.append(row)
        if valid(original.get("close")):
            previous = original
    return changes, counts, missing, conflicts


@writer
def _publish(root, jobs, sessions, start, end):
    from .change_observation import add_cost, daily_changes, empty_cost, save_changes
    from .limit_events import _mark_stale, _published_dates_from, initialize_limits
    from .security_facts import initialize_facts
    from .store import catalog

    results = []
    initialize_facts(root)
    initialize_limits(root)
    for code, market, source in jobs:
        started = perf_counter()
        metrics, applied_changes = empty_cost(), []
        lifecycle_changed = False
        lifecycle_start = None
        result_entry = {}
        source = source if usable_source(source, code) else None
        try:
            if usable_source(source, code):
                raw_ipo = str(source["finance"].get("ipo_date", ""))
                if len(raw_ipo) == 8:
                    ipo = datetime.strptime(raw_ipo, "%Y%m%d").date()
                    with catalog(root) as conn:
                        prior_lifecycle = conn.execute(
                            "SELECT listing_date FROM security_lifecycle WHERE symbol=?",
                            [f"{code}.{market}"],
                        ).fetchone()
                        if prior_lifecycle is None or prior_lifecycle[0] is None:
                            # Establishing a listing date also changes the treatment of
                            # previously published sessions before that date.
                            stale = _published_dates_from(root, date.min)
                            _mark_stale(root, stale, "新增上市日期事实，等待重算")
                            add_cost(metrics, stale_date_marks=len(stale))
                            conn.execute(
                                """INSERT INTO security_lifecycle
                                (symbol,listing_date,source,fetched_at)
                                VALUES (?,?,?,current_timestamp)
                                ON CONFLICT(symbol) DO UPDATE SET listing_date=excluded.listing_date
                                """,
                                [f"{code}.{market}", ipo, "tdx:finance"],
                            )
                            lifecycle_changed = True
                            lifecycle_start = min(stale).isoformat() if stale else ipo.isoformat()
            paths = daily_paths(root, market, code)
            tables = []
            rows = []
            for p in paths:
                table = pq.ParquetFile(p).read()
                add_cost(metrics, files_read=1, rows_read=len(table),
                         file_bytes_read_proxy=p.stat().st_size)
                dates = table["trade_date"].to_pylist()
                offset = max(0, bisect_left(dates, start - timedelta(days=30)) - 5)
                tail = table.slice(offset)
                tables.append((p, table, offset, tail))
                rows.extend(tail.to_pylist())
                add_cost(metrics, rows_materialized=len(tail))
            if [r["trade_date"] for r in rows] != sorted({r["trade_date"] for r in rows}):
                raise ValueError("Duplicate or unordered daily dates")
            # Read pre-window status facts for rolling volume baselines.
            facts = {
                r["trade_date"]: r
                for r in daily_facts(root, f"{code}.{market}", start - timedelta(days=30), end)
            }
            updates, counts, missing, conflicts = derive_rows(
                rows, facts, source, sessions, start, end
            )
            replacements = {r["trade_date"]: r for r in updates}
            before = {r["trade_date"]: r for r in rows}
            for path, table, offset, tail in tables:
                if not any(d in replacements for d in tail["trade_date"].to_pylist()):
                    continue
                output = [replacements.get(day, before[day])
                          for day in tail["trade_date"].to_pylist()]
                columns = dict.fromkeys([*table.column_names, *(k for r in output for k in r)])
                result = pa.Table.from_pylist([{k: r.get(k) for k in columns} for r in output])
                for field in ("trade_date", "open", "high", "low", "close", "volume", "amount"):
                    if not tail[field].equals(result[field].cast(tail[field].type)):
                        raise ValueError(f"Unexpected raw field change: {field}")
                if offset:
                    result = pa.concat_tables([table.slice(0, offset), result],
                                              promote_options="permissive")
                temp = path.with_suffix(f".{uuid4().hex}.part")
                tail_days = set(tail["trade_date"].to_pylist())
                file_updates = [r for r in updates if r["trade_date"] in tail_days]
                delta = daily_changes(before, file_updates, f"{code}.{market}",
                                      VERSION, "optional_field_enrichment")
                try:
                    pq.write_table(result, temp, compression="zstd")
                    stale = _published_dates_from(root, min(r["trade_date"] for r in file_updates))
                    _mark_stale(root, stale, "日线指标及参考价变化，等待重算")
                    add_cost(metrics, stale_date_marks=len(stale))
                    written_bytes = temp.stat().st_size
                    temp.replace(path)
                finally:
                    temp.unlink(missing_ok=True)
                applied_changes.extend(delta)
                add_cost(metrics, files_rewritten=1, file_bytes_rewritten=written_bytes,
                         rows_rewritten=len(result), changed_rows=len(delta))
            result_entry.update(fields=dict(counts), missing=dict(missing), conflicts=conflicts)
        except Exception as exc:
            result_entry["error"] = str(exc)
        # Observation failures must not hide committed writes or abort later jobs.
        # Attempt the evidence write once, outside the business-write exception path.
        result_entry["change_report"] = None
        try:
            result_entry["change_report"] = save_changes(
                root, applied_changes, status="partial" if "error" in result_entry else "applied"
            )
        except Exception as exc:
            result_entry["observation_error"] = str(exc)
            result_entry.setdefault("error", f"Change observation failed: {exc}")
        metrics["elapsed_seconds"] = perf_counter() - started
        results.append(dict(
            result_entry, symbol=f"{code}.{market}", changed_rows=len(applied_changes),
            change_start=min((r["trade_date"] for r in applied_changes), default=None),
            change_end=max((r["trade_date"] for r in applied_changes), default=None),
            cost=metrics, lifecycle_changed=lifecycle_changed, lifecycle_start=lifecycle_start,
        ))
    return results


def _recompute_dates(root, start, end, results):
    """Keep retries for existing stale/missing publications, skip true no-ops."""
    with pool_lock(root), read_only_catalog(root) as conn:
        pending = {row[0] for row in conn.execute(
            "SELECT trade_date FROM daily_limit_staleness WHERE trade_date BETWEEN ? AND ?",
            [start, end],
        ).fetchall()}
        pending.update(row[0] for row in conn.execute(
            "SELECT c.trade_date FROM security_calendar c WHERE c.is_open "
            "AND c.trade_date BETWEEN ? AND ? AND NOT EXISTS "
            "(SELECT 1 FROM daily_limit_publication p WHERE p.trade_date=c.trade_date)",
            [start, end],
        ).fetchall())
    pending.update(date.fromisoformat(result[key]) for result in results
                   for key in ("change_start", "change_end", "lifecycle_start") if result.get(key))
    return sorted(pending)


def audit_conflicts(root, results):
    """Use BaoStock as a read-only algorithm comparator, never a conflict override."""
    from tdxman.baostock import BaostockClient
    from tdxman.models.enums import Market

    selected = [entry for entry in results if entry.get("conflicts")]
    report = {"mode": "read_only_comparison", "stocks": len(selected),
              "compared": [], "unsupported": [], "failed": []}
    for entry in selected:
        code, market = entry["symbol"].split(".")
        if market == "BJ":
            report["unsupported"].append(entry["symbol"])
            continue
        days = {date.fromisoformat(c["date"]) for c in entry["conflicts"]}
        try:
            with BaostockClient(timeout=8) as client:
                remote = client.get_daily(Market[market], code, start=min(days),
                                          end=max(days), count=None)
            with pool_lock(root):
                from .store import read_daily_table

                stored = {r["trade_date"]: r for r in
                          read_daily_table(root, market, code).to_pylist()
                          if r["trade_date"] in days}
            for row in remote.to_dict("records"):
                day = row["date"]
                if day not in days:
                    continue
                local = stored.get(day, {})
                fields = ("open", "high", "low", "close", "pre_close")
                report["compared"].append({
                    "symbol": entry["symbol"], "date": str(day),
                    "local": {key: local.get(key) for key in fields},
                    "baostock": {key: row.get(key) for key in fields},
                    "ohlc_matches": all(valid(local.get(k)) and valid(row.get(k))
                                        and abs(local[k] - row[k]) < .001 for k in fields[:4]),
                })
        except Exception as exc:
            report["failed"].append({"symbol": entry["symbol"], "error": str(exc)})
    return report


def enrich_daily(root, *, start=None, end=None, lookback=30, limit=None, workers=4,
                 recompute=True, compare_baostock=True):
    """Enrich existing stock history, cache raw evidence and report residual gaps."""
    from tdxman.client import TdxClient

    root = Path(root)
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    end = end or today
    if end > today or (start and start > end):
        raise ValueError("Invalid enrichment date range")
    path = root / "reports/maintenance" / f"enrichment-{uuid4().hex}.json"
    cache = root / "lake/fundamentals/dated_inputs"
    cache.mkdir(parents=True, exist_ok=True)
    with pool_lock(root), read_only_catalog(root) as conn:
        universe = conn.execute(
            "select count(*) from information_schema.tables where table_name='universe'"
        ).fetchone()[0]
        query = "select c.symbol,c.market from coverage c"
        if universe:
            query += (" left join universe u on u.symbol=c.symbol "
                      "where coalesce(u.asset_type,'stock')='stock'")
        securities = conn.execute(query + " order by c.symbol").fetchall()
        if limit:
            securities = securities[:limit]
        sessions = sorted(
            day for day, opened in calendar_days(root).items() if opened and day <= end
        )
    if not sessions:
        raise ValueError("Missing trading calendar; run BaoStock supplementation first")
    start = start or sessions[max(0, len(sessions) - lookback)]
    if datetime.now(ZoneInfo("Asia/Shanghai")).hour < 16 and end == today:
        end = today - timedelta(days=1)
        sessions = [d for d in sessions if d <= end]
    if start > end:
        raise ValueError("Enrichment requires a closed trading session")
    report = dict(
        version=VERSION,
        start=str(start),
        end=str(end),
        status="running",
        requested=len(securities),
        processed=0,
        source_failures=[],
        results=[],
    )
    atomic_json(path, report)
    cached, fetch = [], []
    for code, market in securities:
        file = cache / f"{code}.{market}.json"
        source = json.loads(file.read_text()) if file.exists() else None
        if usable_source(source, code) and source.get("fetched_date") == today.isoformat():
            cached.append((code, market, source))
        else:
            fetch.append((code, market))
    factory, retry = client_factory(TdxClient, workers)
    pending = []

    def flush():
        if pending:
            report["results"].extend(_publish(root, pending, sessions, start, end))
            report["processed"] += len(pending)
            pending.clear()
            atomic_json(path, report)
            LOG.info("Enrichment %s/%s stocks", report["processed"], len(securities))

    try:
        for job in cached:
            pending.append(job)
            if len(pending) >= 40:
                flush()
        for (code, market), source, error, _ in fetch_sync(fetch, factory, _fetch, workers, retry):
            if error:
                report["source_failures"].append(dict(symbol=f"{code}.{market}", error=str(error)))
            else:
                atomic_json(cache / f"{code}.{market}.json", source)
            pending.append((code, market, source))
            if len(pending) >= 40:
                flush()
        flush()
        if compare_baostock:
            report["algorithm_check"] = audit_conflicts(root, report["results"])
            atomic_json(path, report)
        if recompute and not any("error" in r for r in report["results"]):
            from .limit_events import compute_limit_events

            dates = _recompute_dates(root, start, end, report["results"])
            report["limit_requested_dates"] = [day.isoformat() for day in dates]
            report["limit_batches"] = (len(compute_limit_events(root, dates, cache_years=6))
                                       if dates else 0)
        report["status"] = (
            "partial"
            if report["source_failures"] or any("error" in r for r in report["results"])
            else "ok"
        )
    except BaseException as exc:
        report.update(status="failed", error=str(exc))
        if not isinstance(exc, Exception):
            raise
    finally:
        report["fields"] = dict(
            sum((Counter(r.get("fields", {})) for r in report["results"]), Counter())
        )
        report["missing"] = dict(
            sum((Counter(r.get("missing", {})) for r in report["results"]), Counter())
        )
        report["changed_rows"] = sum(r.get("changed_rows", 0) for r in report["results"])
        atomic_json(path, report)
        atomic_json(
            root / "reports/maintenance/enrichment-latest.json", {**report, "report": str(path)}
        )
    return report, path
