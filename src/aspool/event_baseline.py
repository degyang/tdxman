"""Authoritative, lossless corporate-action baseline supplied as a full dump."""

import argparse
import hashlib
import json
import os
import sqlite3
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

FILE = "event_baseline.sqlite"


class BaselineReferences:
    """Load the small event inventory once; lookups use symbol/date bounds."""

    def __init__(self, root):
        self.events = defaultdict(dict)
        self.days = {}
        self.available = (Path(root) / FILE).exists()
        self.requires_dependents = False
        if not self.available:
            return
        with sqlite3.connect((Path(root).resolve() / FILE).as_uri() + "?mode=ro", uri=True) as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='metadata'").fetchone():
                metadata = json.loads(conn.execute("SELECT payload FROM metadata").fetchone()[0])
                self.requires_dependents = metadata.get("enriched_status") in (
                    "requires_rebuild",
                    "dependent_features_require_rebuild",
                )
            for symbol, day, payload in conn.execute(
                "SELECT symbol,effective_date,payload FROM events"
            ):
                row = json.loads(payload)
                value = tuple(
                    row[k]
                    for k in (
                        "dividend_per_share",
                        "per_share_bonus",
                        "allotment_ratio",
                        "allotment_price",
                        "currency",
                    )
                )
                self.events[symbol].setdefault(day, set()).add(value)
        self.days = {symbol: sorted(values) for symbol, values in self.events.items()}

    def reference(self, symbol, previous, day):
        from .baseline_factors import reference_price

        if not self.available or not previous or not previous.get("trade_date"):
            return None
        close = previous.get("close")
        if close is None or close <= 0:
            return None
        days = self.days.get(symbol, [])
        selected = days[bisect_right(days, previous["trade_date"]) : bisect_right(days, day)]
        try:
            for event_day in selected:
                parts = self.events[symbol][event_day]
                if any(p[4] != "CNY" for p in parts):
                    return None
                if any(sum(p[i] != 0 for p in parts) > 1 for i in range(3)):
                    return None
                cash, bonus, rights = (sum(p[i] for p in parts) for i in range(3))
                price = sum(p[2] * p[3] for p in parts) / rights if rights else 0
                close = reference_price(close, cash, bonus, rights, price)
        except ValueError:
            return None
        return close, "derived:baseline_events" if selected else "derived:baseline_no_action"


def install(root, source):
    root, source = Path(root), Path(source)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    target = root / FILE
    if target.exists():
        with sqlite3.connect(target.as_uri() + "?mode=ro", uri=True) as conn:
            metadata = json.loads(conn.execute("SELECT payload FROM metadata").fetchone()[0])
        if metadata["sha256"] == digest:
            return metadata
        raise ValueError("A different baseline is installed; explicit revision is required")
    with duckdb.connect() as reader:
        rows = reader.execute("SELECT * FROM read_parquet(?)", [str(source)]).fetchall()
        fields = [r[0] for r in reader.description]
    required = [
        "thscode",
        "ticker",
        "ex_date_ms",
        "dividend_per_share",
        "per_share_bonus",
        "allotment_ratio",
        "allotment_price",
        "currency",
    ]
    if fields != required:
        raise ValueError(f"Unexpected event schema: {fields}")
    dates = [
        datetime.fromtimestamp(r[2] / 1000, ZoneInfo("Asia/Shanghai")).date().isoformat()
        for r in rows
    ]
    metadata = dict(
        source="ths:full_market_dump",
        source_file=source.name,
        sha256=digest,
        authority="baseline",
        layer="base",
        rows=len(rows),
        symbols=len({r[0] for r in rows}),
        start=min(dates),
        end=max(dates),
        timezone="Asia/Shanghai",
        price_units="per_share",
        enriched_status="requires_rebuild",
    )
    temporary = target.with_suffix(".sqlite.importing")
    if temporary.exists():
        raise ValueError(f"An unfinished import exists: {temporary}")
    try:
        with sqlite3.connect(temporary) as conn:
            conn.execute(
                "CREATE TABLE events(row_id INTEGER PRIMARY KEY,symbol TEXT NOT NULL,"
                "effective_date TEXT NOT NULL,payload TEXT NOT NULL)"
            )
            conn.execute("CREATE INDEX events_by_symbol_date ON events(symbol,effective_date)")
            conn.execute("CREATE INDEX events_by_date ON events(effective_date,symbol)")
            conn.execute("CREATE TABLE metadata(payload TEXT NOT NULL)")
            conn.executemany(
                "INSERT INTO events VALUES (?,?,?,?)",
                [
                    (i, r[0], day, json.dumps(dict(zip(fields, r)), allow_nan=False))
                    for i, (r, day) in enumerate(zip(rows, dates), 1)
                ],
            )
            conn.execute("INSERT INTO metadata VALUES (?)", (json.dumps(metadata),))
            conn.commit()
            if conn.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ValueError("Baseline verification failed")
            assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == len(rows)
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return metadata


def read_events(root, *, symbol, start, end):
    """Read original baseline rows; retain duplicates and future-event boundaries."""
    path = Path(root).resolve() / FILE
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
        return [
            dict(row_id=i, symbol=symbol, effective_date=day, **json.loads(payload))
            for i, day, payload in conn.execute(
                "SELECT row_id,effective_date,payload FROM events "
                "WHERE symbol=? AND effective_date BETWEEN ? AND ? ORDER BY effective_date,row_id",
                (symbol, start, end),
            )
        ]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(install(args.root.resolve(), args.source.resolve()), ensure_ascii=False))
