from __future__ import annotations

import asyncio
from datetime import date, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

import pyarrow.parquet as pq

from .change_protocol import maintenance
from .daily_storage import build_field_quality_report, is_missing_value, merge_daily
from .fetch import client_factory, fetch_async, fetch_sync
from .free_stockdb import _market, _normalize
from .index_lists import atomic_json
from .pool import writer
from .store import (
    bars_path,
    catalog,
    last_marker,
    record_coverage,
    record_coverages,
)

ETF_HISTORY_START = date(2010, 1, 1)


def _fundamentals(root: Path) -> dict[str, dict[str, object]]:
    """Read manual fundamentals snapshots for daily-bar enrichment, if present."""
    from .fundamental_catalog import available, read

    if available(root):
        return {r["symbol"]:r for r in read(root).to_dict("records")}
    path = root / "lake/fundamentals/snapshots.parquet"
    if not path.exists():
        return {}
    table = pq.read_table(path)
    from .change_protocol import note_range

    note_range("snapshot_read", rows=len(table), path=path, bytes_proxy=path.stat().st_size)
    return {row["symbol"]: row for row in table.to_pylist()}


def _enrich_daily(rows: list[dict[str, object]], snapshot: dict[str, object] | None):
    """Add free-stockdb-compatible valuation fields to online daily bars."""
    if snapshot is None:
        return rows
    total_share = snapshot.get("total_share")
    float_share = snapshot.get("float_share")
    ttm_eps = snapshot.get("ttm_eps")
    net_assets = snapshot.get("net_assets")
    enriched: list[dict[str, object]] = []
    for row in rows:
        observed = snapshot.get("refreshed_at")
        observed_day = observed.date() if isinstance(observed, datetime) else None
        # A latest snapshot is evidence only for its observation date.
        if observed_day is None or row.get("trade_date") != observed_day:
            enriched.append(row)
            continue
        close = row.get("close")
        volume = row.get("volume")
        values: dict[str, object] = {
            "total_share": total_share,
            "float_share": float_share,
        }
        if close is not None and total_share and float_share:
            values["total_mv"] = float(close) * float(total_share)
            values["float_mv"] = float(close) * float(float_share)
        if close is not None and ttm_eps and float(ttm_eps) > 0:
            values["pe_ttm"] = float(close) / float(ttm_eps)
        if close is not None and net_assets and float(net_assets) > 0:
            values["pb"] = float(close) / float(net_assets)
        if (
            row.get("turnover") is None
            and volume is not None
            and float_share
            and float(float_share) > 0
        ):
            values["turnover"] = float(volume) / float(float_share) * 100
        enriched.append({**row, **values})
    return enriched


def _load_tdxman() -> tuple[Any, Any, Any, Any]:
    from tdxman.mac.client import AsyncMacClient, MacClient
    from tdxman.mac.enums import Adjust, Period
    from tdxman.models.enums import Market

    return MacClient, AsyncMacClient, (Adjust, Period), Market


def _tdx_market(symbol: str, market_type: Any) -> Any:
    return getattr(market_type, _market(symbol))


def _daily_rows(frame: Any, symbol: str, start: date | None = None) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for row in frame.to_dict(orient="records"):
        value = row.get("datetime") or row.get("date")
        parsed = value.date() if hasattr(value, "date") else date.fromisoformat(str(value)[:10])
        if not start or parsed >= start:
            rows.append(
                {**row, "trade_date": parsed, "symbol": symbol, "volume": float(row["vol"])}
            )
    return rows


def _minute_rows(frame: Any, symbol: str, start: datetime | None = None) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for row in frame.to_dict(orient="records"):
        value = row.get("datetime") or row.get("date")
        stamp = value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
        if not isinstance(stamp, datetime):
            stamp = datetime.fromisoformat(str(stamp))
        if not start or stamp >= start:
            rows.append({**row, "timestamp": stamp.replace(tzinfo=None), "symbol": symbol})
    return rows


def _merge_rows(
    prior: list[dict[str, object]], incoming: list[dict[str, object]], key: str
) -> list[dict[str, object]]:
    from .daily_storage import _same_values

    dependencies = {
        "pct_chg": {"close", "pre_close"},
        "amplitude": {"high", "low", "pre_close"},
        "vol_ratio": {"volume"},
        "turnover": {"volume", "float_share"},
        "turnover_rate": {"volume", "float_share"},
        "total_mv": {"close", "total_share"},
        "float_mv": {"close", "float_share"},
    }
    merged: dict[object, dict[str, object]] = {}
    volume_changes, close_changes = set(), set()
    for position, row in enumerate(prior + incoming):
        previous = merged.get(row[key], {})
        fresh = {field: value for field, value in row.items() if not is_missing_value(value)}
        changed = {field for field, value in fresh.items()
                   if not _same_values({field: previous.get(field)}, {field: value})}
        result = {
            **previous,
            **fresh,
        }
        if previous:
            for field, inputs in dependencies.items():
                if (inputs & changed and field not in changed
                        and (field in previous or field + "_source" in previous)):
                    result[field] = None
                    result[field + "_source"] = "unknown:inputs_changed"
            if "volume" in changed:
                volume_changes.add(row[key])
            if "close" in changed:
                close_changes.add(row[key])
        elif position >= len(prior):
            # Inserting an older missing session also changes later baselines.
            if "volume" in fresh:
                volume_changes.add(row[key])
            if "close" in fresh:
                close_changes.add(row[key])
        merged[row[key]] = result
    result = [merged[value] for value in sorted(merged)]
    if key == "trade_date":
        for index, row in enumerate(result):
            if row[key] in volume_changes:
                for later in result[index + 1:index + 6]:
                    later["vol_ratio"] = None
                    later["vol_ratio_source"] = "unknown:baseline_changed"
            if row[key] in close_changes and index + 1 < len(result):
                later = result[index + 1]
                if later.get("pre_close_source") == "derived:tdx_xdxr":
                    for field in ("pre_close", "pct_chg", "amplitude"):
                        later[field] = None
                        later[field + "_source"] = "unknown:previous_close_changed"
    return result


def _fill_close_vol_ratio(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Fill only missing daily volume ratios with the close-of-day five-day definition."""
    for index, row in enumerate(rows):
        if not is_missing_value(row.get("vol_ratio")) or index < 5 or is_missing_value(
            row.get("volume")
        ):
            continue
        previous = [item.get("volume") for item in rows[index - 5 : index]]
        if all(value is not None for value in previous):
            average = sum(float(value) for value in previous) / 5
            if average > 0:
                row["vol_ratio"] = float(row["volume"]) / average
    return rows


def _symbols(root: Path, period: str, limit: int | None, asset_type: str = "stock") -> list[str]:
    table = "coverage" if period == "daily" else "coverage_minutes"
    with catalog(root) as conn:
        if asset_type == "etf":
            query = f"""select c.symbol from {table} c
                join universe u on u.symbol = c.symbol
                where u.asset_type = 'etf' and u.active order by c.symbol"""
        else:
            query = f"""select c.symbol from {table} c
                left join universe u on u.symbol = c.symbol
                where coalesce(u.asset_type, 'stock') = 'stock' order by c.symbol"""
        values = [row[0] for row in conn.execute(query).fetchall()]
    return values[:limit] if limit else values


def _bar_page(frame, symbol, since, floor: date | None = None):
    rows = _daily_rows(frame, symbol)
    if not rows:
        return [], None, 0
    oldest = min(row["trade_date"] for row in rows)
    return (
        [
            row
            for row in rows
            if (since is None or row["trade_date"] >= since)
            and (floor is None or row["trade_date"] >= floor)
        ],
        oldest,
        len(rows),
    )


def _job_spec(job):
    symbol, since = job[:2]
    asset_type = job[2] if len(job) > 2 else "stock"
    return symbol, since, asset_type


def _stock_records(client, job):
    _, _, enums, Market = _load_tdxman()
    Adjust, Period = enums
    symbol, since, asset_type = _job_spec(job)
    floor = ETF_HISTORY_START if asset_type == "etf" and since is None else None
    offset, oldest, rows = 0, None, []
    while offset < 64000:
        frame = client.get_stock_kline(
            _tdx_market(symbol, Market),
            symbol,
            Period.DAILY,
            start=offset,
            count=30 if since is not None else 800,
            adjust=Adjust.NONE,
        )
        page, minimum, size = _bar_page(frame, symbol, since, floor)
        if minimum is None:
            return rows
        if oldest is not None and minimum >= oldest:
            raise ValueError("股票分页未向更早日期推进")
        rows.extend(page)
        if since is not None and minimum <= since:
            return rows
        if since is None and (size < 800 or (floor is not None and minimum <= floor)):
            return rows
        offset += size
        oldest = minimum
    raise ValueError("股票分页超过上限")


async def _stock_records_async(client, job):
    _, _, enums, Market = _load_tdxman()
    Adjust, Period = enums
    symbol, since, asset_type = _job_spec(job)
    floor = ETF_HISTORY_START if asset_type == "etf" and since is None else None
    offset, oldest, rows = 0, None, []
    while offset < 64000:
        frame = await client.get_stock_kline(
            _tdx_market(symbol, Market),
            symbol,
            Period.DAILY,
            start=offset,
            count=30 if since is not None else 800,
            adjust=Adjust.NONE,
        )
        page, minimum, size = _bar_page(frame, symbol, since, floor)
        if minimum is None:
            return rows
        if oldest is not None and minimum >= oldest:
            raise ValueError("股票分页未向更早日期推进")
        rows.extend(page)
        if since is not None and minimum <= since:
            return rows
        if since is None and (size < 800 or (floor is not None and minimum <= floor)):
            return rows
        offset += size
        oldest = minimum
    raise ValueError("股票分页超过上限")


@maintenance
async def _sync_daily_run(
    root, limit, asynchronous, workers, asset_type="stock", derive_limits=True,
):
    started = perf_counter()
    universe = {"listed": 0, "added": [], "inactive": [], "reactivated": []}
    # A limited run is intentionally isolated for diagnostics and tests.
    if limit is None or asset_type == "etf":
        from .universe import (
            pending_universe_symbols,
            refresh_etf_universe,
            refresh_stock_universe,
            universe_is_stale,
        )

        if universe_is_stale(root, asset_type=asset_type):
            universe = (
                refresh_etf_universe(root) if asset_type == "etf" else refresh_stock_universe(root)
            )
        else:
            universe["added"] = pending_universe_symbols(root, asset_type)
        if limit:
            universe["added"] = universe["added"][:limit]
    symbols = _symbols(root, "daily", limit, asset_type)
    report = {
        "command": f"{asset_type}-sync",
        "asset_type": asset_type,
        "run_id": uuid4().hex,
        "requested": len(symbols),
        "started_at": datetime.now().isoformat(),
        "async": asynchronous,
        "workers": workers,
        "success": [],
        "missing": [],
        "failed": [],
        "write_seconds": 0.0,
        "universe": {
            key: value if key != "added" else len(value) for key, value in universe.items()
        },
    }
    from .change_protocol import attach_run

    attach_run(report)
    jobs = []
    for symbol in symbols:
        try:
            from .daily_access import DailyStorage

            days = DailyStorage(root).tail_dates(_market(symbol), symbol)
            if not days:
                raise ValueError("已有标的缺少历史日线")
            jobs.append((symbol, days[max(0, len(days) - 5)], asset_type))
        except Exception as exc:
            report["failed"].append({"symbol": symbol, "error": str(exc)})
    remaining = max(0, limit - len(jobs)) if limit else None
    additions = universe["added"] if remaining is None else universe["added"][:remaining]
    for entry in additions:
        jobs.append((entry["symbol"], None, asset_type))
    report["requested"] = len(jobs) + len(report["failed"])

    pending = []

    def consume(entry):
        job, rows, error, _ = entry
        symbol = job[0]
        try:
            if error:
                raise error
            if not rows:
                report["missing"].append(symbol)
                return
            pending.append((symbol, rows))
        except Exception as exc:
            report["failed"].append({"symbol": symbol, "error": str(exc)})

    if jobs:
        MacClient, AsyncMacClient, _, _ = _load_tdxman()
        if asynchronous:
            factory, retry = client_factory(AsyncMacClient, workers)
            async for entry in fetch_async(jobs, factory, _stock_records_async, workers, retry):
                consume(entry)
        else:
            factory, retry = client_factory(MacClient, workers)
            for entry in fetch_sync(jobs, factory, _stock_records, workers, retry):
                consume(entry)
    tick = perf_counter()
    results, failures, quality_rows = _publish_stock_rows(root, pending, asset_type)
    report["write_seconds"] = perf_counter() - tick
    report["success"].extend(results)
    report["failed"].extend(failures)
    report["field_quality"] = build_field_quality_report(
        quality_rows, asset_type, f"tdxman:{asset_type}"
    )
    report["total_seconds"] = perf_counter() - started
    report["finished_at"] = datetime.now().isoformat()
    report["status"] = (
        "partial" if report["failed"] or (asset_type == "etf" and report["missing"]) else "ok"
    )
    # 派生事件：原始日线已提交，派生失败不回滚原始数据，只记录状态供重试。
    touched_dates = sorted(
        {
            date.fromisoformat(change)
            for entry in report["success"]
            for change in entry.get("changed_dates", [])
        }
    )
    if asset_type == "stock" and touched_dates and derive_limits and not report["failed"]:
        touched = [d.isoformat() for d in touched_dates]
        try:
            from .limit_events import compute_limit_events

            report["limit_events"] = {
                "dates": touched,
                "batches": compute_limit_events(root, touched_dates, asset_type="stock"),
                "status": "ok",
            }
            from .limit_events import summarize_published_limit_quality

            report["limit_events"]["rule_quality"] = summarize_published_limit_quality(
                root, touched_dates
            )
        except Exception as exc:  # noqa: BLE001 - 派生失败不得影响原始批次
            report["limit_events"] = {
                "dates": touched,
                "status": "failed",
                "error": str(exc),
            }
    report_path = Path(root) / "reports/maintenance" / f"{report['run_id']}.json"
    atomic_json(report_path, report)
    atomic_json(
        Path(root) / "reports/maintenance/latest.json", {**report, "report": str(report_path)}
    )
    if report["failed"]:
        raise ValueError(f"部分股票未完成：{report_path}")
    return len(report["success"]), sum(r["changed_rows"] for r in report["success"])


@writer
def _publish_stock_rows(root, pending, asset_type="stock"):
    from .change_observation import ChangeSpool, empty_cost, save_changes

    coverage = []
    quality_rows = []
    results, failures = [], []
    fundamentals = _fundamentals(root)
    with catalog(root) as conn:
        try:
            names = {
                row[0]: row[1]
                for row in conn.execute(
                    "SELECT symbol, name FROM universe WHERE asset_type = ?", [asset_type]
                ).fetchall()
            }
        except Exception:
            names = {}
    for symbol, rows in pending:
        changed_dates: list = []
        changes, metrics = ChangeSpool(root), empty_cost()
        entry = {"symbol": symbol, "fetched": len(rows)}
        try:
            incoming = _normalize(
                _enrich_daily(rows, fundamentals.get(symbol) if asset_type == "stock" else None),
                symbol,
            )
            if asset_type == "etf":
                incoming = [
                    {**row, "name": names.get(symbol), "asset_type": "etf"} for row in incoming
                ]
            merge_daily(
                root,
                _market(symbol),
                symbol,
                incoming,
                f"tdxman:{asset_type}",
                coverage=coverage,
                changed_dates=changed_dates,
                quality_rows=quality_rows,
                changes=changes,
                metrics=metrics,
            )
        except Exception as exc:
            entry["error"] = str(exc)
        entry.update(
            changed_rows=metrics["changed_rows"],
            changed_dates=[d.isoformat() for d in changed_dates],
            change_report=None, cost=metrics,
        )
        try:
            entry["change_report"] = save_changes(
                root, changes, status="partial" if "error" in entry else "applied"
            )
        except Exception as exc:
            entry["observation_error"] = str(exc)
            entry.setdefault("error", f"Change observation failed: {exc}")
        changes.close()
        (failures if "error" in entry else results).append(entry)
    record_coverages(root, coverage)
    return results, failures, quality_rows


def update_daily(root, limit=None, workers=1, asset_type="stock", derive_limits=True):
    if asset_type == "stock" and (Path(root) / "stocks.sqlite").exists():
        raise ValueError("SQLite 股票日线请使用 aspool update 或带日期窗口的 aspool sync")
    if asset_type == "etf" and (Path(root) / "etfs.sqlite").exists():
        return _sqlite_etfs(root, limit=limit, workers=workers)
    return asyncio.run(_sync_daily_run(root, limit, False, workers, asset_type, derive_limits))


async def update_daily_async(root, limit=None, workers=1, asset_type="stock", derive_limits=True):
    if asset_type == "stock" and (Path(root) / "stocks.sqlite").exists():
        raise ValueError("SQLite 股票日线请使用 aspool update 或带日期窗口的 aspool sync")
    if asset_type == "etf" and (Path(root) / "etfs.sqlite").exists():
        return await asyncio.to_thread(_sqlite_etfs, root, limit=limit, workers=workers,
                                       asynchronous=True)
    return await _sync_daily_run(root, limit, True, workers, asset_type, derive_limits)


def _sqlite_etfs(root, **kwargs):
    from .sqlite_etf_sync import sync_etfs

    report, path = sync_etfs(root, **kwargs)
    if report["status"] != "ok":
        raise ValueError(f"ETF sync incomplete: {path}")
    return len(report["success"]), sum(r["added"]+r["changed"] for r in report["success"])


def update_minutes(
    root: Path, limit: int | None = None, asset_type: str = "stock"
) -> tuple[int, int]:
    MacClient, _, enums, Market = _load_tdxman()
    Adjust, Period = enums
    count = rows_written = 0
    with MacClient.from_best_host() as client:
        for symbol in _symbols(root, "minutes", limit, asset_type):
            start = last_marker(root, symbol, "minutes")
            frame = client.get_stock_kline(
                _tdx_market(symbol, Market),
                symbol,
                Period.MIN_1,
                start=0,
                count=800,
                adjust=Adjust.NONE,
            )
            incoming = _minute_rows(frame, symbol, start if isinstance(start, datetime) else None)
            if not incoming:
                continue
            path = bars_path(root, "minutes", _market(symbol), symbol)
            prior = pq.read_table(path).to_pylist() if path.exists() else []
            rows = _merge_rows(prior, incoming, "timestamp")
            _write_period(path, rows)
            record_coverage(
                root,
                symbol,
                _market(symbol),
                rows[0]["timestamp"],
                rows[-1]["timestamp"],
                len(rows),
                "tdxman",
                "minutes",
            )
            count += 1
            rows_written += len(incoming)
    return count, rows_written


def update_daily_offline(root: Path, limit: int | None = None, asset_type: str = "stock"):
    if asset_type == "stock" and (Path(root) / "stocks.sqlite").exists():
        raise ValueError("SQLite 股票离线导入使用显式 ops 脚本；禁止旧文件更新")
    if asset_type == "etf" and (Path(root) / "etfs.sqlite").exists():
        return _sqlite_etfs(root, limit=limit, mode="offline")
    return _update_daily_offline_legacy(root, limit, asset_type)


@writer
def _update_daily_offline_legacy(
    root: Path, limit: int | None = None, asset_type: str = "stock"
) -> tuple[int, int]:
    """Merge local vipdoc daily bars into the pool, preserving static snapshots."""
    from tdxman.models.enums import Market
    from tdxman.offline import find_daily_bar_file, read_daily_bars

    count = written = 0
    fundamentals = _fundamentals(root)
    symbols = _symbols(root, "daily", limit, asset_type)
    pending = []
    if asset_type == "etf":
        from .universe import pending_universe_symbols

        pending = pending_universe_symbols(root, "etf")
    jobs = [(symbol, True) for symbol in symbols]
    jobs.extend((entry["symbol"], False) for entry in pending)
    if limit:
        jobs = jobs[:limit]
    for symbol, existing in jobs:
        market = _tdx_market(symbol, Market)
        if market == Market.BJ:
            continue
        try:
            source = read_daily_bars(find_daily_bar_file(market, symbol))
            if existing:
                source = source[-30:]
        except FileNotFoundError:
            continue
        raw = [
            {
                "trade_date": date(bar.year, bar.month, bar.day),
                "symbol": symbol,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.vol * 100,
                "amount": bar.amount,
            }
            for bar in source
        ]
        incoming = _normalize(
            _enrich_daily(raw, fundamentals.get(symbol) if asset_type == "stock" else None), symbol
        )
        if asset_type == "etf":
            incoming = [
                {**row, "asset_type": "etf"}
                for row in incoming
                if row["trade_date"] >= ETF_HISTORY_START
            ]
        if not incoming:
            continue
        changed = merge_daily(root, _market(symbol), symbol, incoming,
                              f"tdxman:{asset_type}:offline")
        count += 1
        written += changed
    return count, written


async def update_minutes_async(
    root: Path, limit: int | None = None, asset_type: str = "stock"
) -> tuple[int, int]:
    _, AsyncMacClient, enums, Market = _load_tdxman()
    Adjust, Period = enums
    count = rows_written = 0
    async with AsyncMacClient.from_best_host() as client:
        for symbol in _symbols(root, "minutes", limit, asset_type):
            start = last_marker(root, symbol, "minutes")
            frame = await client.get_stock_kline(
                _tdx_market(symbol, Market), symbol, Period.MIN_1, 0, 800, 1, Adjust.NONE
            )
            incoming = _minute_rows(frame, symbol, start if isinstance(start, datetime) else None)
            if not incoming:
                continue
            path = bars_path(root, "minutes", _market(symbol), symbol)
            prior = pq.read_table(path).to_pylist() if path.exists() else []
            rows = _merge_rows(prior, incoming, "timestamp")
            _write_period(path, rows)
            record_coverage(
                root,
                symbol,
                _market(symbol),
                rows[0]["timestamp"],
                rows[-1]["timestamp"],
                len(rows),
                "tdxman",
                "minutes",
            )
            count += 1
            rows_written += len(incoming)
    return count, rows_written


def _write_period(path: Path, rows: list[dict[str, object]]) -> None:
    from uuid import uuid4

    import pyarrow as pa

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{uuid4().hex}.part")
    pq.write_table(pa.Table.from_pylist(rows), temporary, compression="zstd")
    temporary.replace(path)


def update_online(
    root: Path,
    period: str,
    async_mode: bool,
    limit: int | None = None,
    workers: int = 1,
    asset_type: str = "stock",
    derive_limits: bool = True,
) -> tuple[int, int]:
    if period == "daily":
        return (
            asyncio.run(update_daily_async(root, limit, workers, asset_type, derive_limits))
            if async_mode
            else update_daily(root, limit, workers, asset_type, derive_limits)
        )
    return (
        asyncio.run(update_minutes_async(root, limit, asset_type))
        if async_mode
        else update_minutes(root, limit, asset_type)
    )
