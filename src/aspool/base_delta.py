"""Bounded logical replication of base inputs with durable receiver recovery."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb

from .pool import pool_lock
from .sqlite_publication import assert_published, durable_remove, durable_write, state_directory

DATASETS = {
    "event-coverage": ("@coverage", "event_coverage", "as_of"),
    "factor-source-evidence": ("@files", "source_evidence", "as_of"),
    "stock-bars": ("stocks.sqlite", "daily_bars", "trade_date"),
    "index-bars": ("indices.sqlite", "daily_bars", "trade_date"),
    "etf-bars": ("etfs.sqlite", "daily_bars", "trade_date"),
    "stock-source-facts": ("features.sqlite", "stock_daily_features", "trade_date"),
    "actions": ("stocks.sqlite", "corporate_actions", "effective_date"),
    "factor-anchors": ("adjustments.sqlite", "stock_factor_anchors", "effective_date"),
    "etf-source-factors": ("adjustments.sqlite", "etf_adjustment_factors", "effective_date"),
    "financial-reports": ("fundamentals.sqlite", "stock_financial_reports", "period_end"),
    "shareholder-counts": ("fundamentals.sqlite", "stock_shareholder_counts", "as_of_date"),
    "securities": ("catalog.duckdb", "securities", None),
    "index-memberships": ("catalog.duckdb", "index_memberships", None),
    "calendar": ("catalog.duckdb", "security_calendar", "trade_date"),
    "board-snapshot-sets": ("features.sqlite", "board_snapshot_sets", None),
    "board-snapshots": ("features.sqlite", "board_snapshots", None),
    "board-source-state": ("features.sqlite", "board_sync_state", None),
}
# Revision/algorithm fields in imported legacy anchors describe local bookkeeping.
OBSERVATION_METADATA = {"updated_at", "fetched_at", "input_hash", "algorithm_version"}
MAX_ROWS = 100_000
MAX_BYTES = 256 * 1024 * 1024
STOCK_DATASETS = {"stock-bars", "stock-source-facts", "actions", "factor-anchors"}
_ACTIVE = ContextVar("replica_recovery_root", default=None)


def evidence_directory(root):
    root = Path(root).resolve()
    return root.parent / ".local" / "base-evidence" / root.name


def store_source_evidence(root, evidence):
    digest = hashlib.sha256(
        json.dumps(evidence, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()
    directory = evidence_directory(root)
    durable_write(directory / (digest + ".json"), evidence)
    with sqlite3.connect(directory / "index.sqlite") as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS evidence_index (sha256 TEXT PRIMARY KEY,"
            "symbol TEXT NOT NULL,as_of TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS evidence_by_symbol_date ON evidence_index(symbol,as_of)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO evidence_index VALUES (?,?,?)",
            (digest, evidence["symbol"], evidence["as_of"]),
        )
    return digest


def replica_path(root):
    return state_directory(root) / "pending-replica.json"


def assert_replica_complete(root):
    if replica_path(root).exists() and _ACTIVE.get() != str(Path(root).resolve()):
        from .api_contract import DataPoolError

        raise DataPoolError("RECOVERY_REQUIRED", "Base replica incomplete; run replica recover")


def _checksum(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _json_value(value):
    return value.isoformat() if isinstance(value, (datetime, date)) else value


@contextmanager
def _connection(root, filename, *, write=False):
    path = Path(root) / filename
    if not path.is_file():
        raise ValueError(f"Missing dataset file: {path}")
    if filename.endswith(".duckdb"):
        conn = duckdb.connect(str(path), read_only=not write)
    else:
        conn = sqlite3.connect(path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True)
        if write:
            conn.execute("PRAGMA synchronous=FULL")
    try:
        yield conn
    finally:
        conn.close()


def _shape(conn, table, duck=False):
    if duck:
        info = conn.execute(f'DESCRIBE "{table}"').fetchall()
        return [r[0] for r in info], [r[0] for r in info if r[3] == "PRI"]
    info = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    return [r[1] for r in info], [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5]]


def _fields(dataset, columns, keys):
    fields = [c for c in columns if c not in OBSERVATION_METADATA]
    if dataset == "stock-source-facts":
        from .sqlite_daily_update import FACT_FIELDS

        fields = [c for c in columns if c in FACT_FIELDS or c in keys or c == "is_st_name_date"]
    return fields


def _read(root, dataset, start, end, symbols, max_rows):
    if dataset == "event-coverage":
        path = evidence_directory(root) / "coverage.sqlite"
        fields = ["symbol", "verified_start", "as_of", "source", "payload_json"]
        keys = fields[:4]
        if not path.exists():
            return fields, keys, {}
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
            marks = ",".join("?" for _ in symbols)
            rows = conn.execute(
                "SELECT symbol,verified_start,as_of,source,payload_json FROM event_coverage "
                f"WHERE as_of BETWEEN ? AND ? AND symbol IN ({marks}) LIMIT ?",
                (start, end, *symbols, max_rows + 1),
            ).fetchall()
        if len(rows) > max_rows:
            raise ValueError("Coverage receipt budget exceeded")
        return fields, keys, {tuple(row[:4]): dict(zip(fields, row)) for row in rows}
    if dataset == "factor-source-evidence":
        records = {}
        directory = evidence_directory(root)
        if directory.exists():
            index = directory / "index.sqlite"
            if index.exists():
                with sqlite3.connect(index.as_uri() + "?mode=ro", uri=True) as conn:
                    marks = ",".join("?" for _ in symbols)
                    names = conn.execute(
                        "SELECT sha256 FROM evidence_index WHERE as_of BETWEEN ? AND ? "
                        f"AND symbol IN ({marks}) LIMIT ?",
                        (start, end, *symbols, max_rows + 1),
                    ).fetchall()
                if len(names) > max_rows:
                    raise ValueError("Source evidence index budget exceeded")
                paths = [directory / (r[0] + ".json") for r in names]
            else:
                paths = list(directory.glob("*.json"))
                if len(paths) > 1000:
                    raise ValueError("Source evidence requires an indexed inventory")
            read_bytes = 0
            for path in paths:
                read_bytes += path.stat().st_size
                if read_bytes > MAX_BYTES:
                    raise ValueError("Source evidence byte budget exceeded")
                if len(records) >= max_rows or path.stat().st_size > 8 * 1024 * 1024:
                    raise ValueError("Source evidence budget exceeded")
                evidence = json.loads(path.read_text())
                if evidence["symbol"] not in symbols or not start <= evidence["as_of"] <= end:
                    continue
                digest = hashlib.sha256(
                    json.dumps(evidence, sort_keys=True, allow_nan=False).encode()
                ).hexdigest()
                if path.stem != digest:
                    raise ValueError("Source evidence identity mismatch")
                records[(digest,)] = {
                    "sha256": digest,
                    "symbol": evidence["symbol"],
                    "as_of": evidence["as_of"],
                    "payload": evidence,
                }
        return ["sha256", "symbol", "as_of", "payload"], ["sha256"], records
    filename, table, day = DATASETS[dataset]
    with _connection(root, filename) as conn:
        columns, keys = _shape(conn, table, filename.endswith(".duckdb"))
        if not keys:
            raise ValueError(f"Unsupported schema: {dataset}")
        fields = _fields(dataset, columns, keys)
        clauses, params = [], []
        if day:
            clauses.append(f'"{day}" BETWEEN ? AND ?')
            params.extend((start, end))
        if "symbol" in columns:
            clauses.append("symbol IN (" + ",".join("?" for _ in symbols) + ")")
            params.extend(symbols)
        # Snapshot IDs form an immutable source identity; export all available source sets.
        projection = ",".join(f'"{c}"' for c in fields)
        sql = f'SELECT {projection} FROM "{table}"'
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        rows = conn.execute(sql + " LIMIT ?", (*params, max_rows + 1)).fetchall()
        if len(rows) > max_rows:
            raise ValueError(f"Row budget exceeded: {dataset}")
        records = {}
        for values in rows:
            row = dict(zip(fields, map(_json_value, values), strict=True))
            if dataset == "etf-source-factors" and row.get("source") != "legacy:free-stockdb":
                raise ValueError("Unsupported ETF source-factor identity")
            # Locally computed volume metrics are not source observations.
            if dataset in {"stock-bars", "etf-bars"}:
                for metric in ("vol_ratio", "turnover_rate"):
                    if (dataset == "etf-bars" and not row.get(metric + "_source")) or str(
                        row.get(metric + "_source") or ""
                    ).startswith("derived:"):
                        row[metric] = None
                        row[metric + "_source"] = None
            records[tuple(row[k] for k in keys)] = row
        return fields, keys, records


def export_delta(source, target, *, datasets, start, end, symbols, output, max_rows=MAX_ROWS):
    source, target, output = map(lambda p: Path(p).resolve(), (source, target, output))
    if source == target or any(output.is_relative_to(r) for r in (source, target)):
        raise ValueError("Use distinct roots and output outside running roots")
    date.fromisoformat(start)
    date.fromisoformat(end)
    if start > end or not symbols or len(symbols) > 128 or not datasets:
        raise ValueError("Provide an ordered window, datasets and 1..128 symbols")
    if not 1 <= max_rows <= MAX_ROWS or any(d not in DATASETS for d in datasets):
        raise ValueError("Unsupported dataset or row budget")
    result = {
        "format": "aspool-base-delta-v1",
        "source": str(source),
        "target": str(target),
        "window": {"start": start, "end": end, "symbols": sorted(set(symbols))},
        "datasets": [],
        "status": "exported_not_applied",
    }
    total = 0
    with ExitStack() as stack:
        for root in sorted((source, target)):
            stack.enter_context(pool_lock(root))
            assert_published(root)
        for dataset in sorted(set(datasets)):
            fields, keys, before = _read(target, dataset, start, end, symbols, max_rows)
            other_fields, other_keys, after = _read(source, dataset, start, end, symbols, max_rows)
            if fields != other_fields or keys != other_keys:
                raise ValueError(f"Schema mismatch: {dataset}")
            total += len(before) + len(after)
            if total > max_rows:
                raise ValueError("Aggregate row budget exceeded")
            result["datasets"].append(
                {
                    "name": dataset,
                    "keys": keys,
                    "fields": fields,
                    "changes": [
                        {"key": list(k), "before": before.get(k), "after": r}
                        for k, r in sorted(after.items())
                        if before.get(k) != r
                    ],
                    "source_absent_keys": [list(k) for k in sorted(before.keys() - after.keys())],
                }
            )
    result["sha256"] = _checksum(result)
    if len(json.dumps(result).encode()) > MAX_BYTES:
        raise ValueError("Package byte budget exceeded")
    durable_write(output, result)
    return result


def _load(root, package):
    path = Path(package)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Package byte budget exceeded")
    value = json.loads(path.read_text())
    checksum = value.pop("sha256", None)
    if checksum != _checksum(value):
        raise ValueError("Package checksum mismatch")
    if value.get("format") != "aspool-base-delta-v1" or value.get("target") != str(root):
        raise ValueError("Delta target or format mismatch")
    window = value["window"]
    date.fromisoformat(window["start"])
    date.fromisoformat(window["end"])
    if window["start"] > window["end"] or not 1 <= len(window["symbols"]) <= 128:
        raise ValueError("Invalid package scope")
    datasets = value["datasets"]
    if len({d["name"] for d in datasets}) != len(datasets):
        raise ValueError("Duplicate dataset")
    if sum(len(d["changes"]) for d in datasets) > MAX_ROWS:
        raise ValueError("Package row budget exceeded")
    value["sha256"] = checksum
    return value


def _checked(root, dataset, window):
    name = dataset["name"]
    if name not in DATASETS:
        raise ValueError("Unknown package dataset")
    fields, keys, current = _read(
        root, name, window["start"], window["end"], window["symbols"], MAX_ROWS
    )
    if fields != dataset["fields"] or keys != dataset["keys"]:
        raise ValueError("Receiver schema changed")
    seen, changes = set(), []
    day = DATASETS[name][2]
    for change in dataset["changes"]:
        key, after = tuple(change["key"]), change["after"]
        if key in seen or set(after) != set(fields) or tuple(after[k] for k in keys) != key:
            raise ValueError("Invalid or duplicate delta row")
        seen.add(key)
        if "symbol" in after and after["symbol"] not in window["symbols"]:
            raise ValueError("Row outside symbol scope")
        if day and not window["start"] <= after[day] <= window["end"]:
            raise ValueError("Row outside date scope")
        if name in {"actions", "factor-anchors"}:
            expected_kind = "event" if name == "actions" else "factor_anchor"
            if after.get("record_kind") != expected_kind:
                raise ValueError("Source record kind mismatch")
        if name == "factor-source-evidence":
            digest = hashlib.sha256(
                json.dumps(after["payload"], sort_keys=True, allow_nan=False).encode()
            ).hexdigest()
            if (
                after["sha256"] != digest
                or after["symbol"] != after["payload"]["symbol"]
                or after["as_of"] != after["payload"]["as_of"]
            ):
                raise ValueError("Invalid source evidence identity")
        actual = current.get(key)
        if (
            name in {"board-snapshots", "board-snapshot-sets"}
            and actual is not None
            and actual != after
        ):
            raise ValueError("Immutable board snapshot identity changed")
        if actual == after:
            continue
        if actual != change["before"]:
            raise ValueError(f"Receiver conflict: {name} {key}")
        changes.append(change)
    return changes


def _put(conn, table, keys, after, *, duck=False):
    columns, _ = _shape(conn, table, duck)
    row = dict(after)
    if "updated_at" in columns:
        row["updated_at"] = (
            datetime.now(timezone.utc).replace(tzinfo=None) if duck else time.time_ns() // 1000
        )
    if "fetched_at" in columns:
        row["fetched_at"] = datetime.now(timezone.utc).isoformat()
    where = " AND ".join(f'"{k}"=?' for k in keys)
    exists = conn.execute(
        f'SELECT 1 FROM "{table}" WHERE {where}', [row[k] for k in keys]
    ).fetchone()
    if exists:
        cols = [c for c in row if c not in keys]
        conn.execute(
            f'UPDATE "{table}" SET ' + ",".join(f'"{c}"=?' for c in cols) + f" WHERE {where}",
            [row[c] for c in cols] + [row[k] for k in keys],
        )
    else:
        if table == "daily_features":
            row["calc_status"] = "NO_TRADE"
        cols = list(row)
        conn.execute(
            f'INSERT INTO "{table}" ('
            + ",".join(f'"{c}"' for c in cols)
            + ") VALUES ("
            + ",".join("?" for _ in cols)
            + ")",
            [row[c] for c in cols],
        )


def _fault(phase):
    """Named test boundary for actual process-exit recovery."""


def _standalone(root, dataset, window):
    name = dataset["name"]
    changes = _checked(root, dataset, window)
    if not changes:
        return 0
    if name == "event-coverage":
        for change in changes:
            row = change["after"]
            store_event_coverage(
                root,
                symbol=row["symbol"],
                verified_start=row["verified_start"],
                verified_end=row["as_of"],
                source=row["source"],
                events=json.loads(row["payload_json"]),
            )
        return len(changes)
    if name == "factor-source-evidence":
        for change in changes:
            after = change["after"]
            digest = hashlib.sha256(
                json.dumps(after["payload"], sort_keys=True, allow_nan=False).encode()
            ).hexdigest()
            if after["sha256"] != digest:
                raise ValueError("Invalid source evidence digest")
            store_source_evidence(root, after["payload"])
        return len(changes)
    if name == "etf-bars":
        from collections import defaultdict

        from .sqlite_etf_store import connection, save_rows

        groups = defaultdict(list)
        for change in changes:
            row = change["after"]
            groups[(row["symbol"], row["source"])].append(row)
        for (symbol, source), rows in groups.items():
            code, market = symbol.split(".")
            with connection(root, read_only=False) as conn:
                conn.row_factory = sqlite3.Row
                save_rows(
                    conn,
                    dict(code=code, market=market, name=rows[0].get("name") or ""),
                    rows,
                    source=source,
                )
        return len(changes)
    filename, table, _ = DATASETS[name]
    with _connection(root, filename, write=True) as conn:
        conn.execute("BEGIN TRANSACTION")
        try:
            for change in changes:
                _put(
                    conn, table, dataset["keys"], change["after"], duck=filename.endswith(".duckdb")
                )
            if name in {"financial-reports", "shareholder-counts"}:
                state_name = table
                maximum = conn.execute(
                    f'SELECT max("{DATASETS[name][2]}") FROM "{table}"'
                ).fetchone()[0]
                old = conn.execute(
                    "SELECT revision FROM dataset_state WHERE dataset=?", (state_name,)
                ).fetchone()
                conn.execute(
                    "INSERT OR REPLACE INTO dataset_state VALUES (?,?,?,?)",
                    (state_name, (old[0] if old else 0) + 1, maximum, time.time_ns() // 1000),
                )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    return len(changes)


def _verified_baseline(root, conn, symbol, through):
    import pandas as pd

    from .sqlite_factor_bootstrap import validate_evidence

    _, _, records = _read(root, "factor-source-evidence", through, through, [symbol], MAX_ROWS)
    if not records:
        return None
    if len(records) != 1:
        raise ValueError("Ambiguous factor source evidence")
    proof = next(iter(records.values()))["payload"]
    frames = {}
    for kind, values in proof["prices"].items():
        frame = pd.DataFrame(values, columns=["open", "high", "low", "close"])
        frame["datetime"] = proof["dates"]
        frames[kind] = frame
    verified = validate_evidence(
        symbol, proof["listing_date"], proof["as_of"], frames, proof["events"]
    )
    for day, prices in zip(verified["days"], verified["raw"], strict=True):
        row = conn.execute(
            "SELECT open,high,low,close FROM daily_bars WHERE symbol=? AND trade_date=?",
            (symbol, day),
        ).fetchone()
        if row is None or tuple(row) != tuple(prices):
            raise ValueError("Source factor evidence differs from receiver raw prices")
    from .sqlite_reference_factors import _coverage_event_value, _insert_action

    for event in verified["events"]:
        old = conn.execute(
            "SELECT payload_json FROM corporate_actions WHERE symbol=? "
            "AND record_kind='event' AND effective_date=? AND source=? AND source_key=?",
            (symbol, event["effective_date"], "tdx:xdxr", event["source_key"]),
        ).fetchone()
        if old is not None and _coverage_event_value(json.loads(old[0])) != _coverage_event_value(
            event
        ):
            raise ValueError("Receiver event conflicts with source factor evidence")
        _insert_action(conn, symbol, event, "tdx:xdxr")
    price_events = verified["price_events"]
    end = (
        (date.fromisoformat(price_events[0]["effective_date"]) - timedelta(days=1)).isoformat()
        if price_events
        else through
    )
    return proof["listing_date"], end, "tdx:listing_episode"


def _stock_apply(root, datasets, window, *, force=False):
    from .sqlite_canonical import canonical_operation
    from .sqlite_daily_derived import DERIVED_COLUMNS, classify_trading, recompute_symbol_features
    from .sqlite_market_summary import recompute_daily_summary
    from .sqlite_reference_factors import update_reference_factors
    from .sqlite_stock_store import stock_connection
    from .sqlite_volume_metrics import recompute_volume_metrics

    changes = [(d, c) for d in datasets for c in _checked(root, d, window)]
    if not changes and not force:
        return {"changed_rows": 0, "summary_rows": 0}
    affected, symbols = set(), {}
    if force:
        symbols = {symbol: window["start"] for symbol in window["symbols"]}
    reference_symbols = set()
    with _connection(root, "catalog.duckdb") as catalog:
        marks = ",".join("?" for _ in window["symbols"])
        listing_column = (
            "listing_date" if "listing_date" in _shape(catalog, "securities", True)[0] else "NULL"
        )
        listing_dates = dict(
            catalog.execute(
                f"SELECT symbol,{listing_column} FROM securities WHERE symbol IN ({marks})",
                window["symbols"],
            ).fetchall()
        )
        calendar = (
            [
                str(r[0])
                for r in catalog.execute(
                    "SELECT trade_date FROM security_calendar WHERE is_open ORDER BY "
                    "trade_date LIMIT 10001"
                ).fetchall()
            ]
            if "security_calendar" in {r[0] for r in catalog.execute("SHOW TABLES").fetchall()}
            else []
        )
    if len(calendar) > 10000:
        raise ValueError("Calendar budget exceeded")
    with stock_connection(root, read_only=False) as conn, canonical_operation(conn, "daily_update"):
        conn.execute("BEGIN IMMEDIATE")
        try:
            for dataset, change in changes:
                name = dataset["name"]
                row = change["after"]
                symbol = row["symbol"]
                day = row.get("trade_date", row.get("effective_date"))
                symbols[symbol] = min(symbols.get(symbol, day), day)
                if name in {"actions", "factor-anchors", "stock-source-facts"} or (
                    name == "stock-bars"
                    and (change.get("before") or {}).get("close") != row.get("close")
                ):
                    reference_symbols.add(symbol)
                table = {
                    "stock-bars": "daily_bars",
                    "stock-source-facts": "daily_features",
                    "actions": "corporate_actions",
                    "factor-anchors": "corporate_actions",
                }[name]
                # Canonical routing requires record kind in each action write.
                if table == "corporate_actions":
                    kind = "event" if name == "actions" else "factor_anchor"
                    cols = list(row)
                    values = list(row.values())
                    cols.append("updated_at")
                    values.append(time.time_ns() // 1000)
                    projection = ",".join(cols)
                    marks = ",".join(f"'{kind}'" if c == "record_kind" else "?" for c in cols)
                    values = [v for c, v in zip(cols, values) if c != "record_kind"]
                    keys = dataset["keys"]
                    setters = ",".join(f"{c}=excluded.{c}" for c in cols if c not in keys)
                    conn.execute(
                        f"INSERT INTO corporate_actions({projection}) VALUES ({marks}) "
                        f"ON CONFLICT(" + ",".join(keys) + f") DO UPDATE SET {setters}",
                        values,
                    )
                else:
                    # Schema introspection of the logical table is supported by the adapter.
                    _put(conn, table, dataset["keys"], row)
            for symbol, start in sorted(symbols.items()):
                if not conn.execute(
                    "SELECT 1 FROM corporate_actions WHERE symbol=? "
                    "AND record_kind='factor' LIMIT 1",
                    (symbol,),
                ).fetchone():
                    _, _, proofs = _read(
                        root,
                        "factor-source-evidence",
                        window["start"],
                        window["end"],
                        [symbol],
                        MAX_ROWS,
                    )
                    if proofs:
                        start = min(start, *(r["payload"]["listing_date"] for r in proofs.values()))
                dates = [
                    r[0]
                    for r in conn.execute(
                        "SELECT trade_date FROM daily_bars WHERE symbol=? AND trade_date>=? "
                        "UNION SELECT trade_date FROM daily_features WHERE symbol=? "
                        "AND trade_date>=? "
                        "ORDER BY trade_date LIMIT 10001",
                        (symbol, start, symbol, start),
                    )
                ]
                if len(dates) > 10000:
                    raise ValueError("Dependency suffix exceeds 10000 sessions")
                if not dates:
                    continue
                previous_features = {}
                for day in dates:
                    cursor = conn.execute(
                        "SELECT * FROM daily_features WHERE symbol=? AND trade_date=?",
                        (symbol, day),
                    )
                    old = cursor.fetchone()
                    if old is not None:
                        previous_features[day] = dict(zip([c[0] for c in cursor.description], old))
                for day in dates:
                    # Create missing dated facts without inventing source observations.
                    conn.execute(
                        "INSERT INTO "
                        "daily_features(symbol,trade_date,calc_status,"
                        "updated_at) VALUES (?,?,'NO_TRADE',?) "
                        "ON CONFLICT(symbol,trade_date) DO NOTHING",
                        (symbol, day, time.time_ns() // 1000),
                    )
                    conn.execute(
                        "UPDATE daily_features SET calc_status='NO_TRADE',"
                        + ",".join(f"{c}=NULL" for c in DERIVED_COLUMNS if c != "calc_status")
                        + " WHERE symbol=? AND trade_date=?",
                        (symbol, day),
                    )
                for day in dates:
                    cursor = conn.execute(
                        "SELECT b.*,f.trading_status FROM daily_bars b JOIN daily_features f "
                        "USING(symbol,trade_date) WHERE b.symbol=? AND b.trade_date=?",
                        (symbol, day),
                    )
                    values = cursor.fetchone()
                    if values is None:
                        continue
                    row = dict(zip([c[0] for c in cursor.description], values))
                    row["bar_date"] = day
                    if classify_trading(row) == "TRADED":
                        conn.execute(
                            "UPDATE daily_features SET calc_status='TRADED' "
                            "WHERE symbol=? AND trade_date=?",
                            (symbol, day),
                        )
                options = {}
                has_sources = conn.execute(
                    "SELECT 1 FROM corporate_actions WHERE symbol=? AND "
                    "record_kind IN ('event','factor_anchor') LIMIT 1",
                    (symbol,),
                ).fetchone()
                _, _, coverage = _read(
                    root, "event-coverage", dates[-1], dates[-1], [symbol], MAX_ROWS
                )
                for receipt in sorted(coverage.values(), key=lambda r: r["verified_start"]):
                    from .sqlite_event_update import merge_recent_events

                    merge_recent_events(
                        conn,
                        symbol=symbol,
                        verified_start=receipt["verified_start"],
                        verified_end=receipt["as_of"],
                        source=receipt["source"],
                        events=json.loads(receipt["payload_json"]),
                    )
                existing_factor = conn.execute(
                    "SELECT 1 FROM corporate_actions WHERE symbol=? "
                    "AND record_kind='factor' LIMIT 1",
                    (symbol,),
                ).fetchone()
                baseline = (
                    _verified_baseline(root, conn, symbol, dates[-1])
                    if not existing_factor
                    else None
                )
                if baseline:
                    options["verified_no_event_range"] = baseline
                if baseline or (symbol in reference_symbols and has_sources and not coverage):
                    options.update(factor_through=dates[-1], anchors=[])
                    # Passing an empty finite list requests rebuild from stored source inputs.
                update_reference_factors(conn, symbol=symbol, dates=dates, **options)
                for offset in range(0, len(dates), 60):
                    volume_days, _ = recompute_volume_metrics(
                        conn, symbol, dates[offset : offset + 60], propagate_days=()
                    )
                    affected.update(volume_days)
                listing = listing_dates.get(symbol)
                listed_days = (
                    {
                        day: sum(str(listing) <= session <= day for session in calendar)
                        for day in dates
                    }
                    if listing and calendar
                    else {}
                )
                result = recompute_symbol_features(
                    conn,
                    symbol=symbol,
                    start=dates[0],
                    end=dates[-1],
                    listed_days=listed_days,
                    max_rows=100000,
                    max_affected_dates=10000,
                )
                for day, old in previous_features.items():
                    cursor = conn.execute(
                        "SELECT * FROM daily_features WHERE symbol=? AND trade_date=?",
                        (symbol, day),
                    )
                    current = dict(zip([c[0] for c in cursor.description], cursor.fetchone()))
                    if all(current[k] == v for k, v in old.items() if k != "updated_at"):
                        conn.execute(
                            "UPDATE daily_features SET updated_at=? WHERE "
                            "symbol=? AND trade_date=?",
                            (old["updated_at"], symbol, day),
                        )
                affected.update(dates)
                affected.update(result.get("affected_dates", []))
            summary_rows = 0
            for day in sorted(affected):
                summary_rows += recompute_daily_summary(conn, trade_date=day, inputs_changed=True)[
                    "changed_rows"
                ]
                from .sqlite_board_daily import recompute_board_daily

                recompute_board_daily(conn, day=day, changed_symbols=set(symbols))
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return {
        "changed_rows": len(changes),
        "summary_rows": summary_rows,
        "affected_dates": sorted(affected),
    }


def _apply_delta(root, package=None, *, recover=False):
    """Preflight all inputs; persist intent; resume committed phases idempotently."""
    root = Path(root).resolve()
    with pool_lock(root, write=True):
        path = replica_path(root)
        if recover:
            if not path.exists():
                return {"applied": False, "changed_rows": 0, "reason": "no pending replica"}
            intent = json.loads(path.read_text())
            if intent.pop("sha256", None) != _checksum(intent):
                raise ValueError("Invalid replica recovery intent")
            value = intent["package"]
        else:
            assert_published(root)
            value = _load(root, package)
            intent = {"root": str(root), "package": value, "results": {}}
        if intent["root"] != str(root):
            raise ValueError("Replica recovery root mismatch")
        token = _ACTIVE.set(str(root))
        try:
            from .sqlite_publication import pending_path, recover_publication

            if pending_path(root).exists():
                recover_publication(root)
            window, datasets = value["window"], value["datasets"]
            for dataset in datasets:
                _checked(root, dataset, window)

            def save():
                sealed = dict(intent)
                sealed["sha256"] = _checksum(sealed)
                durable_write(path, sealed)

            if not path.exists():
                save()
            _fault("after_intent")
            # Copy immutable snapshots before source state selects them.
            order = {"board-snapshot-sets": 0, "board-snapshots": 1, "board-source-state": 2}
            for dataset in sorted(
                (d for d in datasets if d["name"] not in STOCK_DATASETS),
                key=lambda d: (order.get(d["name"], 3), d["name"]),
            ):
                name = dataset["name"]
                count = _standalone(root, dataset, window)
                intent["results"][name] = max(count, intent["results"].get(name, 0))
                save()
                _fault("after_" + name)
            stock = [d for d in datasets if d["name"] in STOCK_DATASETS]
            force = (root / "stocks.sqlite").exists() and any(
                intent["results"].get(name, 0)
                for name in (
                    "securities",
                    "calendar",
                    "board-source-state",
                    "factor-source-evidence",
                    "event-coverage",
                )
            )
            if stock or force:
                previous = intent["results"].get("stock", {})
                stats = _stock_apply(root, stock, window, force=force and not previous)
                intent["results"]["stock"] = (
                    stats if stats["changed_rows"] or not previous else previous
                )
                save()
                _fault("after_stock")
            # Verify all base after-images. Calculated metrics are projected out by _read.
            for dataset in datasets:
                if _checked(root, dataset, window):
                    raise ValueError("Replica verification still has unapplied changes")
            _fault("before_complete")
            durable_remove(path)
            return {"applied": True, "results": intent["results"]}
        finally:
            _ACTIVE.reset(token)


def apply_delta(root, package=None, *, recover=False):
    root = Path(root).resolve()
    token = _ACTIVE.set(str(root)) if recover else None
    try:
        return _apply_delta(root, package, recover=recover)
    finally:
        if token is not None:
            _ACTIVE.reset(token)


def apply_stock_delta(root, package):
    value = _load(Path(root).resolve(), package)
    if any(d["name"] not in {"stock-bars", "stock-source-facts"} for d in value["datasets"]):
        raise ValueError("apply-stock accepts stock-bars and stock-source-facts only")
    result = apply_delta(root, package)
    stats = result.get("results", {}).get("stock", {})
    return dict(
        result, changed_rows=stats.get("changed_rows", 0), summary_rows=stats.get("summary_rows", 0)
    )


def store_event_coverage(root, *, symbol, verified_start, verified_end, source, events):
    directory = evidence_directory(root)
    directory.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(list(events), sort_keys=True, allow_nan=False)
    with sqlite3.connect(directory / "coverage.sqlite") as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS event_coverage("
            "symbol TEXT,verified_start TEXT,as_of TEXT,source TEXT,payload_json TEXT NOT NULL,"
            "PRIMARY KEY(symbol,verified_start,as_of,source)) WITHOUT ROWID"
        )
        key = (symbol, verified_start, verified_end, source)
        old = conn.execute(
            "SELECT payload_json FROM event_coverage WHERE symbol=? AND verified_start=? "
            "AND as_of=? AND source=?",
            key,
        ).fetchone()
        if old is None or old[0] != payload:
            conn.execute(
                "INSERT OR REPLACE INTO event_coverage VALUES (?,?,?,?,?)", (*key, payload)
            )
