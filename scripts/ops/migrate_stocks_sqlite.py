"""Import historical source facts into an isolated, explicit SQLite development root.

This ops command does not enable the new backend or advertise derived readiness.
The immutable development snapshot remains the recovery source for lower-priority
candidates, retired aliases and legacy calculated references.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import re
import time
from collections import Counter, defaultdict
from datetime import date, datetime
from importlib.resources import files
from pathlib import Path

import duckdb
import pyarrow.parquet as pq
from snapshot_development_data import digest

from aspool.free_stockdb import _market
from aspool.sqlite_stock_store import stock_connection
from aspool.universe import _is_a_share

BAR_COLUMNS = (
    "symbol trade_date open high low close volume amount turnover_rate vol_ratio pct_chg "
    "amplitude total_share float_share float_shares total_mv float_mv pe_ttm pb name "
    "name_as_of name_source ohlcv_source turnover_rate_source vol_ratio_source "
    "pct_chg_source amplitude_source total_share_source float_share_source "
    "total_mv_source float_mv_source"
).split()
FEATURE_COLUMNS = (
    "symbol trade_date source_pre_close source_pre_close_source source_is_st "
    "source_is_st_source is_st_name_date trading_status trading_status_source "
    "pre_close pre_close_source is_st is_st_source calc_status limit_status "
    "limit_reason streak_known"
).split()
ACTION_COLUMNS = (
    "symbol effective_date record_kind source source_key category payload_json "
    "source_cumulative_factor"
).split()
NON_NUMERIC = {"symbol", "trade_date", "name", "name_as_of", "name_source"}
KEY_ALIASES = {"asset_type", "code", "date", "datetime", "market", "vol", "turnover"}
FACT_FIELDS = {"pre_close", "pre_close_source", "is_st", "is_st_source",
               "is_st_name_date", "trading_status", "trading_status_source"}
ALLOWED = set(BAR_COLUMNS) | KEY_ALIASES | FACT_FIELDS


def iso(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            from zoneinfo import ZoneInfo
            value = value.astimezone(ZoneInfo("Asia/Shanghai"))
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    value = str(value)
    if len(value) == 8 and value.isdigit():
        value = f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return date.fromisoformat(value[:10]).isoformat()


def finite(value, field):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"Non-numeric {field}: {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"Non-finite {field}: {value!r}")
    return float(value)


def source_value(value, source, *, raw=False):
    # Locally computed and snapshot-name values are not immutable dated inputs.
    if value is None or (source and (source.startswith(("derived:", "limit_derived:"))
                                    or source == "tdxman:quote_name")):
        return None, None
    return value, ("raw_fallback:" if raw else "") + (source or "legacy_unspecified")


def normalize_bar(row, symbol, audit):
    code, market = symbol.split(".")
    day = iso(row["trade_date"])
    for key, allowed in [("symbol", {code, symbol, f"{market}.{code}"}),
                         ("code", {code}), ("market", {market})]:
        if row.get(key) is not None and str(row[key]) not in allowed:
            raise ValueError(f"Path/{key} mismatch for {symbol}: {row[key]}")
    for key in ("date", "datetime"):
        if row.get(key) is not None and iso(row[key]) != day:
            raise ValueError(f"Date alias mismatch: {symbol} {day} {key}")
    if row.get("asset_type") not in (None, "stock"):
        raise ValueError(f"Non-stock row in stock source: {symbol}")
    values = dict(row, symbol=symbol, trade_date=day)
    for canonical, alias in [("volume", "vol"), ("turnover_rate", "turnover")]:
        a, b = row.get(canonical), row.get(alias)
        if a is not None and b is not None and a != b:
            audit("alias_precedence", symbol, day, canonical, a, b)
        values[canonical] = a if a is not None else b
    nameday = iso(row.get("is_st_name_date"))
    values["name_as_of"] = nameday if row.get("is_st_source") == "tdxman:quote_name" else None
    values["name_source"] = "tdxman:quote_name" if values["name_as_of"] else None
    result = []
    for column in BAR_COLUMNS:
        value = values.get(column)
        if column not in NON_NUMERIC and not column.endswith("_source"):
            value = finite(value, column)
        elif value is not None and not isinstance(value, str):
            raise ValueError(f"Non-text {column}")
        result.append(value)
    return tuple(result)


def normalize_features(row, fact, symbol, day, audit):
    values = dict(symbol=symbol, trade_date=day)
    raw_pre, raw_source = source_value(row.get("pre_close"), row.get("pre_close_source"), raw=True)
    fact_pre, fact_source = source_value(fact.get("pre_close"), fact.get("source"))
    if raw_pre is not None and fact_pre is not None and raw_pre != fact_pre:
        audit("dated_precedence", symbol, day, "pre_close", fact_pre, raw_pre)
    pre = fact_pre if fact_pre is not None else raw_pre
    pre_source = fact_source if fact_pre is not None else raw_source
    raw_st, raw_st_source = source_value(row.get("is_st"), row.get("is_st_source"), raw=True)
    fact_st, fact_st_source = source_value(fact.get("is_st"), fact.get("source"))
    if raw_st is not None and fact_st is not None and raw_st != fact_st:
        audit("st_source_conflict", symbol, day, "is_st", fact_st, raw_st)
    st = fact_st if fact_st is not None else raw_st
    st_source = fact_st_source if fact_st is not None else raw_st_source
    if st is not None and st not in (True, False):
        raise ValueError(f"Invalid is_st: {st}")
    status = fact.get("trading_status") or row.get("trading_status")
    status_source = fact.get("source") if fact.get("trading_status") else row.get(
        "trading_status_source")
    prices = [row.get(k) for k in ("open", "high", "low", "close")]
    valid = bool(row) and all(v is not None and math.isfinite(v) and v > 0 for v in prices)
    if valid:
        op, hi, lo, cl = prices
        valid = lo <= min(op, cl) <= max(op, cl) <= hi
    no_trade = not row or status in ("SUSPENDED", "停牌")
    if no_trade and any((row.get(k) or 0) > 0 for k in ("volume", "vol", "amount")):
        audit("suspension_with_turnover", symbol, day, "trading_status", status, row.get("amount"))
        no_trade, valid = False, False
    calc = "NO_TRADE" if no_trade else ("TRADED" if valid else "INVALID")
    values.update(
        source_pre_close=finite(pre, "pre_close"), source_pre_close_source=pre_source,
        source_is_st=int(st) if st is not None else None, source_is_st_source=st_source,
        is_st_name_date=iso(row.get("is_st_name_date")), trading_status=status,
        trading_status_source=status_source, calc_status=calc,
        limit_status="INVALID" if calc == "INVALID" else None,
        limit_reason="invalid_ohlc" if calc == "INVALID" else None,
        streak_known=0 if calc == "INVALID" else None,
    )
    # Effective values are deliberately left NULL until FW-03 validates provenance
    # and rebuilds references, factors, limits and moving averages together.
    return tuple(values.get(column) for column in FEATURE_COLUMNS)


def insert_verified(conn, table, columns, rows, stamp):
    if not rows:
        return
    names = ",".join([*columns, "updated_at"])
    placeholders = ",".join("?" for _ in range(len(columns) + 1))
    conn.executemany(f"INSERT INTO {table} ({names}) VALUES ({placeholders})",
                     (row + (stamp,) for row in rows))


def check_rows(conn, table, columns, symbol, expected):
    ordering = {"daily_bars": "trade_date", "daily_features": "trade_date",
                "corporate_actions": "effective_date,record_kind,source,source_key"}[table]
    actual = conn.execute(f"SELECT {','.join(columns)} FROM {table} "
                          f"WHERE symbol=? ORDER BY {ordering}", [symbol]).fetchall()
    indices = [columns.index(column) for column in ordering.split(",")]
    expected = sorted(expected, key=lambda r: tuple(r[i] for i in indices))
    if actual != expected:
        raise ValueError(f"Field verification failed: {table} {symbol}")


def migrate(source, target, symbols=None, resume=False):
    source, target = source.resolve(), target.resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError("Source and target must be separate trees")
    manifest_path = source / "development-snapshot.json"
    manifest = json.loads(manifest_path.read_text())
    fingerprint = digest(manifest_path)
    inventory = {entry["path"]: entry for entry in manifest["files"]}
    checked = set()

    def checked_path(path):
        relative = path.relative_to(source).as_posix()
        if relative not in checked:
            entry = inventory[relative]
            if path.stat().st_size != entry["bytes"] or digest(path) != entry["sha256"]:
                raise ValueError(f"Frozen input changed: {relative}")
            checked.add(relative)
        return path

    checked_path(source / "catalog.duckdb")
    reports = target / "_reports"
    reports.mkdir(parents=True, exist_ok=True)
    progress = reports / "facts-progress.jsonl"
    state_path = reports / "facts-input.json"
    state = {"source_manifest_sha256": fingerprint, "symbols": symbols,
             "migration_sha256": digest(Path(__file__)),
             "schema_sha256": digest(Path(__file__).parents[2] / "src/aspool/stocks_schema.sql")}
    done = set()
    totals = Counter()
    if resume:
        if json.loads(state_path.read_text()) != state:
            raise ValueError("Migration inputs changed; use a new target")
        for line in progress.read_text().splitlines() if progress.exists() else []:
            row = json.loads(line)
            done.add(row["symbol"])
            totals.update(row["counts"])
    else:
        if (target / "stocks.sqlite").exists() or state_path.exists():
            raise FileExistsError("Use --resume for the same inputs or a new target")
        state_path.write_text(json.dumps(state, indent=2) + "\n")
    catalog = duckdb.connect(str(source / "catalog.duckdb"), read_only=True)
    catalog.execute("SET threads=1")
    catalog.execute("SET memory_limit='256MB'")
    classes = {f"{code}.{market}": kind for code, market, kind in catalog.execute(
        "SELECT symbol,market,asset_type FROM universe").fetchall()}
    for (symbol,) in catalog.execute("SELECT symbol FROM security_lifecycle").fetchall():
        classes.setdefault(symbol, "stock")
    paths_by_symbol = defaultdict(list)
    classification_fallback = []
    for relative in sorted(inventory):
        parts = Path(relative).parts
        if parts[:3] != ("lake", "bars", "daily") or parts[-1] != "bars.parquet":
            continue
        if len(parts) not in (6, 7) or not parts[3].startswith("market=") or not parts[
            4
        ].startswith("symbol="):
            raise ValueError(f"Unsupported daily layout: {relative}")
        market, code = parts[3][7:], parts[4][7:]
        symbol = f"{code}.{market}"
        if symbol not in classes:
            if market in {"SH", "SZ", "BJ"} and len(code) == 6 and _is_a_share(market, code):
                classes[symbol] = "stock"
                classification_fallback.append(dict(
                    symbol=symbol, basis="existing_universe_a_share_rule; absent_from_catalog"))
            else:
                raise ValueError(f"Unclassified security: {symbol}")
        if classes[symbol] == "stock":
            paths_by_symbol[symbol].append(source / relative)
    for symbol, paths in paths_by_symbol.items():
        kinds = {path.parent.name.startswith("year=") for path in paths}
        if len(kinds) != 1 or (False in kinds and len(paths) != 1):
            raise ValueError(f"Mixed daily layouts: {symbol}")
    facts_by_symbol = defaultdict(dict)
    cursor = catalog.execute("SELECT * FROM security_daily_facts")
    names = [column[0] for column in cursor.description]
    for row in cursor.fetchall():
        fact = dict(zip(names, row))
        if classes.get(fact["symbol"]) != "stock":
            raise ValueError(f"Unclassified stock fact: {fact['symbol']}")
        facts_by_symbol[fact["symbol"]][iso(fact["trade_date"])] = fact
    factor_path = source / "lake/adjustments/factors.parquet"
    factors_by_symbol = defaultdict(list)
    excluded = Counter()
    factor_market_aliases = Counter()
    for row in pq.ParquetFile(checked_path(factor_path)).read().to_pylist():
        symbol = f"{row['symbol']}.{row['market']}"
        if classes.get(symbol) == "etf":
            excluded["etf_factor_anchors"] += 1
            continue
        original_symbol = symbol
        if symbol not in classes:
            mapped = f"{row['symbol']}.{_market(row['symbol'])}"
            if classes.get(mapped) == "stock":
                # Legacy import classified 920-prefixed BJ securities as SH.
                # Require the corrected identity in the dated/public catalog.
                factor_market_aliases[f"{symbol}->{mapped}"] += 1
                symbol = mapped
            elif (row["market"] == "SZ" and row["symbol"].startswith(("15", "16", "18"))) or (
                row["market"] == "SH" and row["symbol"].startswith("5")
            ):
                excluded["non_stock_factor_anchors"] += 1
                continue
        if classes.get(symbol) != "stock":
            raise ValueError(f"Unclassified factor: {symbol}")
        factor = finite(row["cumulative_factor"], "cumulative_factor")
        if factor is None or factor <= 0:
            raise ValueError(f"Invalid source factor: {symbol}")
        factors_by_symbol[symbol].append((symbol, iso(row["trade_date"]), "factor_anchor",
                                         "legacy:adjustments/factors.parquet",
                                         f"{original_symbol}:cumulative", None,
                                         json.dumps(dict(row, trade_date=iso(row["trade_date"])),
                                                    sort_keys=True, allow_nan=False), factor))
    events_by_symbol = {}
    for relative in inventory:
        if not (relative.startswith("lake/fundamentals/dated_inputs/")
                and relative.endswith(".json")):
            continue
        path = source / relative
        if classes.get(path.stem) == "etf":
            excluded["etf_event_files"] += 1
            continue
        if classes.get(path.stem) != "stock":
            raise ValueError(f"Unclassified event security: {path.stem}")
        events_by_symbol[path.stem] = path
    candidates = sorted(set(paths_by_symbol) | set(facts_by_symbol) |
                        set(factors_by_symbol) | set(events_by_symbol))
    if symbols is not None:
        unknown = set(symbols) - set(candidates)
        if unknown:
            raise ValueError(f"Unknown stock selections: {sorted(unknown)}")
        candidates = [symbol for symbol in candidates if symbol in symbols]
    print(json.dumps({"planned_symbols": len(candidates), "already_committed": len(done)}),
          flush=True)
    result_path = reports / "facts-result.json"
    if done == set(candidates) and result_path.exists():
        for symbol in candidates:
            for path in paths_by_symbol.get(symbol, []):
                checked_path(path)
            if symbol in events_by_symbol:
                checked_path(events_by_symbol[symbol])
        report = json.loads(result_path.read_text())
        if digest(target / "stocks.sqlite") != report["database_sha256"]:
            raise ValueError("Completed target changed; refusing to reuse its verification")
        catalog.close()
        print(json.dumps({"status": "already_verified", "rows": report["rows"]}), flush=True)
        return report
    issue_counts = Counter()
    issue_path = reports / f"facts-differences-{time.time_ns()}.jsonl.gz"
    start = time.monotonic()
    stamp = time.time_ns() // 1000
    with gzip.open(issue_path, "wt") as differences, stock_connection(
        target, create=not resume, read_only=False
    ) as conn, progress.open("a") as completed:
        # Offline initialization only: build non-unique secondary indexes once,
        # rather than rewriting every historical date page for each new symbol.
        index_statements = re.findall(
            r"CREATE INDEX [^;]+;", files("aspool").joinpath("stocks_schema.sql").read_text()
        )
        for statement in index_statements:
            name = statement.split()[2]
            conn.execute(f"DROP INDEX IF EXISTS {name}")
        def audit(kind, symbol, day, field, selected, other):
            issue_counts[kind] += 1
            differences.write(json.dumps(dict(kind=kind, symbol=symbol, trade_date=day,
                                             field=field, selected=selected, other=other),
                                         ensure_ascii=False, allow_nan=False) + "\n")

        for index, symbol in enumerate(candidates, 1):
            if symbol in done:
                for path in paths_by_symbol.get(symbol, []):
                    checked_path(path)
                if symbol in events_by_symbol:
                    checked_path(events_by_symbol[symbol])
                continue
            bars, features, raw = [], [], {}
            for path in paths_by_symbol.get(symbol, []):
                parquet = pq.ParquetFile(checked_path(path))
                extra = set(parquet.schema_arrow.names) - ALLOWED
                if extra:
                    raise ValueError(f"Unmapped columns: {extra}")
                for batch in parquet.iter_batches(batch_size=4096):
                    for row in batch.to_pylist():
                        normalized = normalize_bar(row, symbol, audit)
                        day = normalized[1]
                        if path.parent.name.startswith("year=") and day[:4] != path.parent.name[5:]:
                            raise ValueError(f"Date outside partition: {symbol} {day}")
                        if day in raw:
                            raise ValueError(f"Duplicate bar key: {symbol} {day}")
                        raw[day] = row
                        bars.append(normalized)
            facts = facts_by_symbol.get(symbol, {})
            for day in sorted(raw.keys() | facts.keys()):
                features.append(normalize_features(raw.get(day, {}), facts.get(day, {}),
                                                   symbol, day, audit))
            actions = list(factors_by_symbol.get(symbol, []))
            if symbol in events_by_symbol:
                path = checked_path(events_by_symbol[symbol])
                envelope = json.loads(path.read_text())
                slots = Counter()
                for event in envelope["events"]:
                    day, category = iso(event["date"]), event["category"]
                    slots[(day, category)] += 1
                    actions.append((symbol, day, "event", "tdx:xdxr",
                                    f"category={category}:slot={slots[(day, category)]}", category,
                                    json.dumps(event, sort_keys=True, separators=(",", ":"),
                                               ensure_ascii=False, allow_nan=False), None))
            with conn:
                # Reprocess only the unrecorded symbol after an interrupted commit.
                for table in ("daily_bars", "daily_features", "corporate_actions"):
                    conn.execute(f"DELETE FROM {table} WHERE symbol=?", [symbol])
                for table, columns, rows in [("daily_bars", BAR_COLUMNS, bars),
                                              ("daily_features", FEATURE_COLUMNS, features),
                                              ("corporate_actions", ACTION_COLUMNS, actions)]:
                    insert_verified(conn, table, columns, rows, stamp)
                    check_rows(conn, table, columns, symbol, rows)
            counts = dict(daily_bars=len(bars), daily_features=len(features),
                          corporate_actions=len(actions))
            completed.write(json.dumps(dict(symbol=symbol, counts=counts)) + "\n")
            completed.flush()
            totals.update(counts)
            if index % 100 == 0 or index == len(candidates):
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                print(json.dumps(dict(symbols_done=index, symbols_total=len(candidates),
                                      rows=dict(totals), seconds=round(time.monotonic()-start, 1))),
                      flush=True)
        print(json.dumps({"stage": "building_secondary_indexes"}), flush=True)
        for statement in index_statements:
            conn.execute(statement)
        conn.commit()
        integrity = conn.execute("PRAGMA integrity_check").fetchall()
        if integrity != [("ok",)]:
            raise RuntimeError(f"SQLite integrity failure: {integrity[:10]}")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        actual = {table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                  for table in ("daily_bars", "daily_features", "corporate_actions")}
        if actual != dict(totals):
            raise ValueError("Final counts differ from committed progress")
    catalog.close()
    report = dict(
        stage="source_facts_only", full_history=symbols is None,
        rows=dict(totals), source_manifest_sha256=fingerprint,
        classification_fallback=classification_fallback,
        excluded_non_stock=dict(excluded),
        factor_market_aliases=dict(factor_market_aliases),
        database_bytes=(target / "stocks.sqlite").stat().st_size,
        database_sha256=digest(target / "stocks.sqlite"),
        field_verification="all inserted source columns compared by symbol before commit",
        integrity_check="ok", differences_this_run=dict(issue_counts),
        derived_ready=False, production_ready=False,
        pending=["effective reference/ST provenance resolution including legacy references",
                 "factor selection, limits, streaks, MA20 and market summaries",
                 "public API integration and Fundwise acceptance"],
        seconds=round(time.monotonic()-start, 1),
    )
    result_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "factor_market_aliases"}), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--target-root", type=Path, required=True)
    parser.add_argument("--symbols", nargs="+")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    migrate(args.source_root, args.target_root, args.symbols, args.resume)


if __name__ == "__main__":
    main()
