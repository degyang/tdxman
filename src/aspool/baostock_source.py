"""Fill historical gaps from BaoStock without replacing valid primary values."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pyarrow.parquet as pq

from tdxman.baostock import BaostockClient
from tdxman.models.enums import Market

from .change_protocol import maintenance
from .config import read_config
from .daily_storage import daily_field_quality, is_missing_value, merge_daily
from .index_lists import atomic_json
from .pool import pool_lock, writer
from .security_facts import (
    BASIC_TABLE,
    CALENDAR_TABLE,
    DAILY_TABLE,
    daily_facts,
    initialize_facts,
    lifecycle_map,
)
from .store import catalog, daily_paths, initialize, read_only_catalog, record_coverages

_OHLCV = ("open", "high", "low", "close", "volume", "amount")


def enabled():
    value = read_config().get("aspool.baostock.enabled", "true").lower()
    if value not in {"true", "false"}:
        raise ValueError("aspool.baostock.enabled 应为 true 或 false")
    return value == "true"


def _valid_bar(row):
    return (
        row is not None
        and all(
            not is_missing_value(row.get(k))
            and math.isfinite(float(row[k]))
            and (float(row[k]) > 0 if k in _OHLCV[:4] else float(row[k]) >= 0)
            for k in _OHLCV
        )
        and row["low"]
        <= min(row["open"], row["close"])
        <= max(row["open"], row["close"])
        <= row["high"]
    )


def _rows(root, market, code, start, end, *, planning=False):
    result = {}
    for path in daily_paths(root, market, code):
        parquet = pq.ParquetFile(path)
        columns = (
            [
                key
                for key in ("trade_date", *_OHLCV, "pre_close", "is_st",
                            "turnover_rate", "turnover")
                if key in parquet.schema_arrow.names
            ]
            if planning
            else None
        )
        table = parquet.read(columns=columns)
        from .change_protocol import note_range

        note_range("read", rows=len(table), path=path, bytes_proxy=path.stat().st_size,
                   start=table["trade_date"][0].as_py() if len(table) else None,
                   end=table["trade_date"][-1].as_py() if len(table) else None)
        for row in table.to_pylist():
            if start <= row["trade_date"] <= end:
                if row["trade_date"] in result:
                    raise ValueError(f"{code}.{market}: 重复日线日期")
                result[row["trade_date"]] = row
    return result


def _expected(days, basic):
    listing = basic.get("listing_date") if basic else None
    delisting = basic.get("delisting_date") if basic else None
    return [
        day
        for day in days
        if (listing is None or day >= listing) and (delisting is None or day < delisting)
    ]


def _needs_fill(row, fact):
    if fact and fact["trading_status"] == "SUSPENDED":
        # A status fact closes an absent suspended bar, but existing rows can
        # still need the dated optional fields.
        return row is not None and not daily_field_quality(row)["joint_valid"]
    return (not _valid_bar(row) or not daily_field_quality(row)["joint_valid"]
            or (is_missing_value(row.get("turnover_rate"))
                and is_missing_value(row.get("turnover"))))


@writer
def _initialize(root):
    initialize(root)
    initialize_facts(root)


@writer
def _publish_calendar(root, frame):
    from .change_protocol import catalog_rows

    if "operation" in frame and not frame.operation.isin(["insert", "update"]).all():
        raise ValueError("Explicit calendar deletions/retractions are unsupported")
    return catalog_rows(
        root, CALENDAR_TABLE, ["trade_date"],
        (dict(trade_date=r["date"], is_open=r["is_open"], source=r["source"])
         for r in frame.to_dict("records")),
        source="baostock", reason="calendar_fact", stale_start=date.min,
    )


@writer
def _invalidate(root, days):
    from .limit_events import _mark_stale, _published_dates_from, initialize_limits

    initialize_limits(root)
    if days:
        _mark_stale(
            root,
            sorted(set(days) | set(_published_dates_from(root, min(days)))),
            "BaoStock 历史补齐待重算",
        )


@writer
def _publish(root, market, code, basic, frame, start, end):
    if "operation" in frame and not frame.operation.isin(["insert", "update"]).all():
        raise ValueError("Explicit BaoStock deletions/retractions are unsupported")
    symbol = f"{code}.{market}"
    prior = _rows(root, market, code, start, end)
    old_facts = {r["trade_date"]: r for r in daily_facts(root, symbol, start, end)}
    updates, facts, conflicts, rejected = [], [], [], []
    fetched = datetime.now(timezone.utc).replace(tzinfo=None)
    for incoming in frame.to_dict("records"):
        day = incoming["date"]
        old = prior.get(day)
        suspended = incoming["trading_status"] == "SUSPENDED"
        if not suspended and not _valid_bar(incoming):
            rejected.append({"symbol": symbol, "date": str(day), "reason": "invalid_source_ohlcv"})
            continue
        if suspended and old and not is_missing_value(old.get("volume")) and old["volume"] > 0:
            conflicts.append(
                {
                    "symbol": symbol,
                    "date": str(day),
                    "field": "trading_status",
                    "primary": "positive_volume",
                    "baostock": "SUSPENDED",
                }
            )
            continue
        row = (
            dict(old)
            if old
            else {"symbol": code, "code": code, "market": market, "name": None, "trade_date": day}
        )
        if not suspended and not _valid_bar(old):
            row.update({key: incoming[key] for key in _OHLCV})
            row["ohlcv_source"] = "baostock"
        elif not suspended and old:
            prices = [key for key in _OHLCV[:4] if abs(old[key] - incoming[key]) > 0.001]
            if prices:
                conflicts.append(
                    {
                        "symbol": symbol,
                        "date": str(day),
                        "fields": prices,
                        "reason": "valid_primary_ohlc_differs",
                    }
                )
                continue
        previous = old_facts.get(day, {})
        fact = {
            "symbol": symbol,
            "trade_date": day,
            "source": "baostock",
            "fetched_at": fetched,
            "trading_status": incoming["trading_status"],
        }
        for key in ("pre_close", "is_st"):
            value = incoming[key]
            quality = daily_field_quality(incoming)[key]
            fact[key] = value if quality == "valid" else previous.get(key)
            if quality != "valid":
                if daily_field_quality(row)[key] != "valid":
                    rejected.append(
                        {
                            "symbol": symbol,
                            "date": str(day),
                            "field": key,
                            "reason": "missing_or_invalid_source_field",
                        }
                    )
                continue
            if daily_field_quality(row)[key] != "valid":
                row[key] = value
                row[key + "_source"] = "baostock"
                row[key + "_date"] = day
            elif row[key] != value if key == "is_st" else abs(row[key] - value) > 0.001:
                conflicts.append(
                    {
                        "symbol": symbol,
                        "date": str(day),
                        "field": key,
                        "primary": row[key],
                        "baostock": value,
                    }
                )
                fact[key] = None
                fact.setdefault("_retract_fields", []).append(key)
        row["trading_status"] = incoming["trading_status"]
        row["trading_status_source"] = "baostock"
        if is_missing_value(row.get("turnover_rate")) and not is_missing_value(row.get("turnover")):
            row["turnover_rate"] = row["turnover"]
        for key in ("turnover_rate", "pe_ttm", "pb"):
            if not suspended and is_missing_value(row.get(key)):
                row[key] = incoming[key]
                row[key + "_source"] = "baostock"
        if not suspended and daily_field_quality(row)["pre_close"] == "valid":
            if is_missing_value(row.get("pct_chg")):
                row["pct_chg"] = (row["close"] / row["pre_close"] - 1) * 100
            if is_missing_value(row.get("amplitude")):
                row["amplitude"] = (row["high"] - row["low"]) / row["pre_close"] * 100
        # Suspensions have no traded OHLCV. Keep only the status when there is
        # no primary bar; never manufacture flat-price or zero-volume bars.
        if not suspended or old is not None:
            if all(not is_missing_value(row.get(k)) and float(row[k]) >= 0 for k in _OHLCV):
                updates.append(row)
        if any(previous.get(k) != fact[k] for k in fact
               if k not in {"fetched_at", "_retract_fields"}):
            facts.append(fact)
    coverage, changed_dates = [], []
    changed = (
        merge_daily(
            root, market, code, updates, "baostock", coverage=coverage, changed_dates=changed_dates
        )
        if updates
        else 0
    )
    record_coverages(root, coverage)
    from .change_protocol import catalog_rows

    catalog_rows(root, BASIC_TABLE, ["symbol"],
                 [dict(symbol=symbol, listing_date=basic["listing_date"],
                       delisting_date=basic["delisting_date"], name=basic["name"],
                       source="baostock", fetched_at=fetched)],
                 source="baostock", reason="lifecycle_fact", ignore=("fetched_at",),
                 stale_start=date.min)
    fact_count = catalog_rows(root, DAILY_TABLE, ["symbol", "trade_date"], facts,
                              source="baostock", reason="dated_security_fact",
                              ignore=("fetched_at",),
                              stale_start=min((r["trade_date"] for r in facts), default=None))
    from .change_protocol import observe

    if "fetched_at" not in basic:
        with catalog(root) as conn:
            observe(conn, f"lifecycle:{symbol}")
    return changed, fact_count, conflicts, rejected


@maintenance
def supplement_daily(
    root: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    lookback: int | None = None,
    limit: int | None = None,
    recompute_limits: bool = True,
):
    """Plan only incomplete securities; refresh dated facts in a serial session.

    A bounded lookback is deliberate: older unknown streak boundaries remain
    unknown until an explicit earlier --start is supplied.
    """
    root = Path(root)
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    if lookback is None:
        lookback = int(read_config().get("aspool.baostock.lookback", "30"))
    if lookback < 1:
        raise ValueError("lookback 必须大于 0")
    end = end or now.date()
    if end > now.date():
        raise ValueError("不能补齐未来交易日")
    if end == now.date() and now.hour < 16:
        end -= timedelta(days=1)
    first = start or end - timedelta(days=max(90, lookback * 3))
    if first > end:
        raise ValueError("start 不能晚于已收盘的 end")
    _initialize(root)
    run_id = "baostock-" + uuid4().hex
    path = root / "reports/maintenance" / f"{run_id}.json"
    report = {
        "command": "supplement",
        "source": "baostock",
        "scope": "SH,SZ",
        "run_id": run_id,
        "started_at": now.isoformat(),
        "status": "running",
        "requested": 0,
        "success": 0,
        "changed_rows": 0,
        "fact_rows": 0,
        "skipped_complete": 0,
        "unsupported": [],
        "missing": [],
        "failed": [],
        "conflicts": [],
        "rejected": [],
    }
    from .change_protocol import attach_run

    attach_run(report)
    atomic_json(path, report)
    days, jobs = [], []
    try:
        with BaostockClient() as client:
            calendar = client.get_trade_calendar(first, end)
            days = calendar.loc[calendar.is_open, "date"].tolist()
            if start is None:
                days = days[-lookback:]
                first = days[0] if days else end
            report.update(start=str(first), end=str(end))
            _publish_calendar(root, calendar)
            with pool_lock(root):
                metadata = lifecycle_map(root)
                from .store import existing_tables

                with read_only_catalog(root) as conn:
                    observed = (dict(conn.execute(
                        "SELECT object_key, observed_at FROM fetch_observations").fetchall())
                        if "fetch_observations" in existing_tables(root) else {})
                with read_only_catalog(root) as conn:
                    has_universe = conn.execute(
                        "SELECT count(*) FROM information_schema.tables WHERE table_name='universe'"
                    ).fetchone()[0]
                    query = "SELECT c.symbol,c.market FROM coverage c"
                    if has_universe:
                        query += (
                            " LEFT JOIN universe u ON u.symbol=c.symbol "
                            "WHERE coalesce(u.asset_type,'stock')='stock'"
                        )
                    securities = conn.execute(query + " ORDER BY c.symbol").fetchall()
                if not securities:
                    raise ValueError("BaoStock 补齐要求先导入股票日线数据池")
                for code, market in securities:
                    symbol = f"{code}.{market}"
                    try:
                        BaostockClient.security_code(Market[market], code)
                    except ValueError:
                        report["unsupported"].append(symbol)
                        continue
                    basic = metadata.get(symbol)
                    rows = _rows(root, market, code, first, end, planning=True)
                    facts = {r["trade_date"]: r for r in daily_facts(root, symbol, first, end)}
                    last_observed = observed.get(f"lifecycle:{symbol}",
                                                  basic.get("fetched_at") if basic else None)
                    stale_basic = (
                        basic is None or is_missing_value(last_observed)
                        or (
                            now.astimezone(timezone.utc).replace(tzinfo=None)
                            - last_observed
                        ).days
                        >= 7
                    )
                    if stale_basic or any(
                        _needs_fill(rows.get(d), facts.get(d)) for d in _expected(days, basic)
                    ):
                        jobs.append((code, market, basic, stale_basic))
                    else:
                        report["skipped_complete"] += 1
            report["planned"] = len(jobs)
            if limit:
                jobs = jobs[:limit]
            report["requested"] = len(jobs)
            report["not_selected"] = report["planned"] - len(jobs)
            atomic_json(path, report)
            consecutive_failures = 0
            for index, (code, market, basic, stale_basic) in enumerate(jobs):
                symbol = f"{code}.{market}"
                try:
                    if stale_basic:
                        basic = client.get_stock_basic(Market[market], code).to_dict("records")[0]
                    expected = _expected(days, basic)
                    frame = client.get_daily(Market[market], code, start=first, end=end, count=None)
                    frame = frame[frame.date.isin(expected)]
                    missing = sorted(set(expected) - set(frame.date))
                    if missing:
                        report["missing"].append(
                            {"symbol": symbol, "dates": list(map(str, missing))}
                        )
                    changed, facts, conflicts, rejected = _publish(
                        root, market, code, basic, frame, first, end
                    )
                    report["success"] += 1
                    report["changed_rows"] += changed
                    report["fact_rows"] += facts
                    report["conflicts"].extend(conflicts)
                    report["rejected"].extend(rejected)
                    consecutive_failures = 0
                except Exception as exc:
                    committed = getattr(exc, "committed_change", None)
                    if committed:
                        report["changed_rows"] += committed["changed_rows"]
                    fact_commit = getattr(exc, "committed_catalog_change", None)
                    if fact_commit and fact_commit["object_key"] == DAILY_TABLE:
                        report["fact_rows"] += fact_commit["changed_rows"]
                    report["failed"].append({"symbol": symbol, "error": str(exc)})
                    consecutive_failures += 1
                    if consecutive_failures >= 3:
                        report["not_attempted"] = len(jobs) - index - 1
                        break
                if (index + 1) % 100 == 0:
                    atomic_json(path, report)
        from .enrichment import _recompute_dates

        recompute = (_recompute_dates(root, first, end, [])
                     if days and not report["failed"] else [])
        if recompute and recompute_limits:
            from .limit_events import compute_limit_events

            try:
                report["limit_events"] = {
                    "status": "ok",
                    "batches": compute_limit_events(root, recompute),
                    "dates": [str(d) for d in recompute],
                }
                from .pool import DataPool

                pool = DataPool(root)
                events = pool.read_limit_events(start=min(days), end=max(days))
                coverage = pool.read_limit_coverage(start=min(days), end=max(days))
                report["limit_events"]["price_limit_coverage"] = [
                    {
                        "date": str(row["trade_date"])[:10],
                        **{
                            key: int(row[key])
                            for key in (
                                "processed_count",
                                "known_count",
                                "unknown_count",
                                "no_limit_count",
                                "invalid_count",
                            )
                        },
                        "rule_version": row["rule_version"],
                        "stale": bool(row["stale"]),
                    }
                    for row in coverage.to_dict("records")
                ]
                up = events[events.close_limit_up.eq(True)]
                report["limit_events"]["consecutive_coverage"] = [
                    {
                        "date": str(day)[:10],
                        "limit_up_count": len(group),
                        "consecutive_known": int(group.consecutive_up.notna().sum()),
                        "consecutive_unknown": int(group.consecutive_up.isna().sum()),
                    }
                    for day, group in up.groupby("trade_date")
                ]
            except Exception as exc:
                report["limit_events"] = {"status": "failed", "error": str(exc)}
        incomplete = any(report[k] for k in ("missing", "failed", "conflicts", "rejected"))
        incomplete |= report.get("limit_events", {}).get("status") == "failed"
        report["status"] = "partial" if incomplete else "ok"
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = str(exc)
    finally:
        report["finished_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
        atomic_json(path, report)
        atomic_json(
            root / "reports/maintenance/baostock-latest.json", {**report, "report": str(path)}
        )
    return report, path
