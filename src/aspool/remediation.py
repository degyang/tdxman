"""Once-planned, sequential limit republication with external durable checkpoints.

Output work is bounded by max_days. Source reads deliberately retain the v8
conservative full-history reference algorithm; this is not a DG-05 optimization.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from datetime import date
from importlib.metadata import version
from pathlib import Path

from . import limit_events as limits
from .daily_access import DailyStorage
from .pool import pool_lock
from .security_facts import calendar_days
from .store import catalog_session, read_only_catalog


def read_scope(root):
    """Same scope semantics as v8, with strictly read-only catalog access."""
    from .security_facts import lifecycle_map

    names, listings = {}, {}
    with read_only_catalog(root) as conn:
        try:
            columns = {row[0] for row in conn.execute("describe universe").fetchall()}
            selection = "symbol,name" + (",listing_date" if "listing_date" in columns else "")
            rows = conn.execute(
                f"SELECT {selection} FROM universe WHERE coalesce(asset_type,'stock')='stock'"
            ).fetchall()
            for row in rows:
                names[str(row[0])] = row[1]
                if len(row) > 2 and row[2] is not None:
                    listings[str(row[0])] = row[2]
        except Exception:
            names, listings = {}, {}
    lifecycle = lifecycle_map(root)
    result = []
    for market, code in DailyStorage(root).identities():
        asset = limits._read_asset_types(root, market, code)
        if (asset or "stock") != "stock":
            continue
        basic = lifecycle.get(f"{code}.{market}", {})
        result.append(
            limits.ScopeEntry(
                f"{code}.{market}",
                market,
                code,
                names.get(code),
                asset,
                basic.get("listing_date") or listings.get(code),
                basic.get("delisting_date"),
            )
        )
    return result


def signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def implementation():
    from tdxman.codec import price_rules

    return dict(
        files={
            str(path.relative_to(Path(__file__).parents[1])): file_hash(path)
            for path in (
                Path(__file__),
                Path(limits.__file__),
                Path(__file__).with_name("security_facts.py"),
                Path(__file__).with_name("daily_access.py"),
                Path(price_rules.__file__),
            )
        },
        dependencies={name: version(name) for name in ("duckdb", "pyarrow", "pandas")},
    )


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=2, default=str)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def checkpoint(conn, path, state):
    previous = conn.execute(
        "SELECT state_hash FROM remediation_runs WHERE plan_id=?", [state["plan_id"]]
    ).fetchone()
    conn.execute(
        "INSERT OR REPLACE INTO remediation_runs VALUES (?, ?, ?, ?)",
        [
            state["plan_id"],
            json.dumps(state, sort_keys=True),
            signature(state),
            previous[0] if previous else None,
        ],
    )
    save(path, state)


def inputs(root):
    """Content-based source watermark, excluding outputs changed by this run."""
    digest = hashlib.sha256()
    for path in DailyStorage(root).files():
        digest.update(str(path.relative_to(root)).encode())
        digest.update(file_hash(path).encode())
    with read_only_catalog(root) as conn:
        tables = {row[0] for row in conn.execute("show tables").fetchall()}
        for table in (
            "universe",
            "security_lifecycle",
            "security_calendar",
            "security_daily_facts",
        ):
            digest.update(table.encode())
            if table not in tables:
                digest.update(b"absent")
                continue
            cursor = conn.execute(f"SELECT * FROM {table} ORDER BY ALL")
            while rows := cursor.fetchmany(10000):
                digest.update(json.dumps(rows, default=str).encode())
    return digest.hexdigest()


def output_version(root, day):
    digest = hashlib.sha256()
    with read_only_catalog(root) as conn:
        for table in (
            "daily_limit_publication",
            "daily_limit_batches",
            "daily_limit_scope",
            "daily_limit_events",
            "daily_limit_exceptions",
            "daily_limit_references",
            "daily_limit_summary",
            "daily_limit_gap_states",
            "daily_limit_streak_boundaries",
            "daily_limit_staleness",
        ):
            rows = conn.execute(f"SELECT * FROM {table} WHERE trade_date=? ORDER BY ALL", [day])
            digest.update(table.encode())
            while chunk := rows.fetchmany(10000):
                digest.update(json.dumps(chunk, default=str).encode())
    return digest.hexdigest()


def plan(root, start, end, recovery_manifest, destination):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    if root == destination or root in destination.parents:
        raise ValueError("Plan/state must be outside the pool")
    if destination.exists():
        raise ValueError("Immutable plan already exists")
    if start > end:
        raise ValueError("Reversed dates")
    with pool_lock(root):
        scope = read_scope(root)
        storage = DailyStorage(root)
        axis = set()
        for entry in scope:
            for batch in storage.batches(entry.market, entry.code, ["trade_date"]):
                axis.update(batch.column(0).to_pylist())
        calendar = calendar_days(root)
        if not axis:
            raise ValueError("Empty source axis")
        axis.update(
            day
            for day, opened in calendar.items()
            if opened and min(axis) <= day <= max(end, max(axis))
        )
        with read_only_catalog(root) as conn:
            last = conn.execute("SELECT max(trade_date) FROM daily_limit_publication").fetchone()[0]
        end = max(end, last) if last is not None else end
        days = sorted(day for day in axis if start <= day <= end)
        if not days:
            raise ValueError("No target sessions")
        manifest = Path(recovery_manifest).resolve()
        result = dict(
            format=1,
            root=str(root),
            rule=limits.RULE_VERSION,
            source=inputs(root),
            implementation=implementation(),
            recovery_manifest=str(manifest),
            recovery_sha256=file_hash(manifest),
            axis=sorted(axis),
            dates=days,
            scope=[asdict(entry) for entry in scope],
            algorithm="v8 conservative sequential reference; full source histories",
        )
        result["plan_id"] = signature(result)
        save(destination, result)
    return result


def execute(plan_path, state_path, *, max_days, approval=None):
    """Publish at most max_days from a fixed plan, then release the writer lock.

    A crash between publication and checkpoint causes exactly that date to be
    recalculated. Existing daily transactions remain authoritative; completed
    checkpoints are checked against their output fingerprint before resuming.
    """
    if isinstance(max_days, bool) or not isinstance(max_days, int) or max_days < 1:
        raise ValueError("max_days must be a positive integer")
    spec = json.loads(Path(plan_path).read_text())
    plan_id = spec.pop("plan_id")
    if signature(spec) != plan_id:
        raise ValueError("Plan integrity failure")
    spec["plan_id"] = plan_id
    root = Path(spec["root"])
    recovery = json.loads(Path(spec["recovery_manifest"]).read_text())
    if recovery.get("snapshot"):
        snapshot = Path(recovery["snapshot"]).resolve()
        if root == snapshot or snapshot in root.parents:
            raise ValueError("Immutable recovery snapshot cannot be a write target")
    state_path = Path(state_path).resolve()
    if root == state_path or root in state_path.parents or state_path == Path(plan_path).resolve():
        raise ValueError("State must be outside pool and distinct from plan")
    if root == (Path.home() / ".aspool").resolve():
        if approval is None:
            raise ValueError("Production requires coordinator approval document")
        gate = json.loads(Path(approval).read_text())
        if (
            gate.get("plan_id") != plan_id
            or gate.get("source") != spec["source"]
            or not gate.get("single_writer")
            or not gate.get("recovery_verified")
        ):
            raise ValueError("Production approval does not match plan/source/single writer")
    if spec["rule"] != limits.RULE_VERSION:
        raise ValueError("Rule version changed")
    if spec["implementation"] != implementation():
        raise ValueError("Implementation/dependency version changed")
    if file_hash(spec["recovery_manifest"]) != spec["recovery_sha256"]:
        raise ValueError("Recovery manifest changed")
    state = (
        json.loads(state_path.read_text())
        if state_path.exists()
        else dict(plan_id=plan_id, next=0, completed=[], pending=None, initialized=False)
    )
    if state["plan_id"] != plan_id:
        raise ValueError("State belongs to another plan")
    if (
        state["next"] != len(state["completed"])
        or [done["date"] for done in state["completed"]] != spec["dates"][: state["next"]]
        or not 0 <= state["next"] <= len(spec["dates"])
    ):
        raise ValueError("Invalid checkpoint sequence")
    with pool_lock(root, write=True), catalog_session(root) as conn:
        from .change_protocol import assert_readable

        assert_readable(root)
        if inputs(root) != spec["source"]:
            raise ValueError("Source changed; audit and approve a new plan")
        conn.execute("""CREATE TABLE IF NOT EXISTS remediation_runs (
            plan_id VARCHAR PRIMARY KEY, state_json VARCHAR NOT NULL,
            state_hash VARCHAR NOT NULL, previous_hash VARCHAR)""")
        authoritative = conn.execute(
            "SELECT state_json,state_hash,previous_hash FROM remediation_runs WHERE plan_id=?",
            [plan_id],
        ).fetchone()
        if authoritative:
            stored = json.loads(authoritative[0])
            if signature(stored) != authoritative[1]:
                raise ValueError("Catalog checkpoint integrity failure")
            if state_path.exists() and signature(state) not in authoritative[1:]:
                raise ValueError("External checkpoint differs from authoritative catalog")
            state = stored
            save(state_path, state)
        elif state_path.exists():
            raise ValueError("External checkpoint has no authoritative catalog ledger")
        else:
            checkpoint(conn, state_path, state)
        for done in state["completed"]:
            if output_version(root, date.fromisoformat(done["date"])) != done["version"]:
                raise ValueError("Completed publication changed externally")
        if state["next"] == len(spec["dates"]):
            return state
        dates = [date.fromisoformat(day) for day in spec["dates"]]
        axis = [date.fromisoformat(day) for day in spec["axis"]]
        scope = []
        for entry in spec["scope"]:
            entry = dict(entry)
            for field in ("listing_date", "delisting_date"):
                if entry[field] is not None:
                    entry[field] = date.fromisoformat(entry[field])
            scope.append(limits.ScopeEntry(**entry))
        if not state["initialized"]:
            limits.initialize_limits(root)
            limits._mark_stale(root, dates, "DG04 fixed plan " + plan_id)
            state["initialized"] = True
            checkpoint(conn, state_path, state)
        selected = dates[state["next"] : state["next"] + max_days]
        prior = limits._previous_session_states(root, selected[0], [e.symbol for e in scope])
        calendar = calendar_days(root)
        by_symbol = {e.symbol: e for e in scope}

        class Rows:
            def __init__(self):
                self.cache = {}

            def get(self, symbol):
                if symbol not in self.cache:
                    entry = by_symbol[symbol]
                    rows = limits._read_symbol_bars(root, entry.market, entry.code)
                    self.cache[symbol] = limits._SymbolBars(
                        rows, selected[0], selected[-1], entry.listing_date
                    )
                return self.cache[symbol]

        bars = Rows()
        for day in selected:
            state["pending"] = str(day)
            checkpoint(conn, state_path, state)
            result = limits._compute_date(day, scope, bars, axis, prior, calendar)
            limits._publish_batch(root, f"daily-limit-{day:%Y%m%d}-stock", result, "stock")
            state["completed"].append(dict(date=str(day), version=output_version(root, day)))
            state["next"] += 1
            state["pending"] = None
            checkpoint(conn, state_path, state)
    return state
