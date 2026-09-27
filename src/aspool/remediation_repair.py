"""Frozen source supplements, rehearsed in isolation and replayed in bounded steps."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from .change_protocol import assert_readable, catalog_rows, recover
from .daily_access import DailyStorage
from .daily_storage import is_missing_value, merge_daily
from .pool import pool_lock
from .remediation import checkpoint, file_hash, implementation, save, signature
from .store import catalog_session, read_only_catalog, record_coverages


def inventory(root):
    result = {"daily:" + str(p.relative_to(root)): file_hash(p) for p in DailyStorage(root).files()}
    with read_only_catalog(root) as conn:
        for table in ("universe", "security_lifecycle", "security_calendar"):
            result[table] = signature(
                conn.execute(f"SELECT * FROM {table} ORDER BY ALL").fetchall()
            )
        rows = conn.execute("SELECT * FROM security_daily_facts ORDER BY symbol,trade_date")
        symbol, group = None, []
        while chunk := rows.fetchmany(10000):
            for row in chunk:
                if row[0] != symbol:
                    if symbol is not None:
                        result["facts:" + symbol] = signature(group)
                    symbol, group = row[0], []
                group.append(row)
        if symbol is not None:
            result["facts:" + symbol] = signature(group)
    return result


def refresh_symbol(root, versions, symbol):
    code, market = symbol.split(".")
    for path in DailyStorage(root).paths(market, code):
        versions["daily:" + str(path.relative_to(root))] = file_hash(path)
    with read_only_catalog(root) as conn:
        facts = conn.execute(
            "SELECT * FROM security_daily_facts WHERE symbol=? ORDER BY ALL", [symbol]
        ).fetchall()
    if facts:
        versions["facts:" + symbol] = signature(facts)


def apply_symbol(root, symbol, records, *, after_daily=None):
    """Fill approved absent bars and missing optional values; preserve valid OHLCV."""
    code, market = symbol.split(".")
    updates, facts, coverage = [], [], []
    for record in records:
        incoming = record["row"]
        day = date.fromisoformat(incoming["date"])
        table = DailyStorage(root).read(market, code, day, day)
        old = table.to_pylist()[0] if table is not None and table.num_rows else None
        row = dict(old) if old else dict(symbol=code, code=code, market=market, trade_date=day)
        status = incoming["trading_status"]
        if status != "SUSPENDED":
            if old is None:
                for key in ("open", "high", "low", "close", "volume", "amount"):
                    row[key] = incoming[key]
            for key in ("pre_close", "is_st", "turnover_rate"):
                if is_missing_value(row.get(key)) and not is_missing_value(incoming.get(key)):
                    row[key] = incoming[key]
                    row[key + "_source"] = "baostock"
            if is_missing_value(row.get("pct_chg")) and row.get("pre_close"):
                row["pct_chg"] = (row["close"] / row["pre_close"] - 1) * 100
                row["pct_chg_source"] = "baostock-reference-calculation"
            if is_missing_value(row.get("amplitude")) and row.get("pre_close"):
                row["amplitude"] = (row["high"] - row["low"]) / row["pre_close"] * 100
                row["amplitude_source"] = "baostock-reference-calculation"
            row["trading_status"] = status
            row["trading_status_source"] = "baostock"
            updates.append(row)
        facts.append(
            dict(
                symbol=symbol,
                trade_date=day,
                pre_close=incoming.get("pre_close"),
                is_st=incoming.get("is_st"),
                trading_status=status,
                source="baostock",
                fetched_at=datetime.fromisoformat(incoming["fetched_at"]).replace(tzinfo=None),
            )
        )
    changed = (
        merge_daily(root, market, code, updates, "dg04-baostock-corroborated", coverage=coverage)
        if updates
        else 0
    )
    record_coverages(root, coverage)
    if after_daily is not None:
        after_daily()
    fact_count = catalog_rows(
        root,
        "security_daily_facts",
        ["symbol", "trade_date"],
        facts,
        source="baostock",
        reason="DG04 independent source supplement",
        ignore=("fetched_at",),
        stale_start=min(r["trade_date"] for r in facts),
    )
    return dict(symbol=symbol, changed_rows=changed, changed_facts=fact_count)


def prepare(lab_root, target_root, payload_path, manifest, destination):
    """Rehearse once and freeze each exact before/after content watermark."""
    lab_root, target_root = Path(lab_root).resolve(), Path(target_root).resolve()
    if lab_root == target_root or lab_root == (Path.home() / ".aspool").resolve():
        raise ValueError("Rehearsal requires an isolated lab")
    recovery = json.loads(Path(manifest).read_text())
    if recovery.get("snapshot"):
        snapshot = Path(recovery["snapshot"]).resolve()
        if lab_root == snapshot or snapshot in lab_root.parents:
            raise ValueError("Immutable snapshot cannot be a rehearsal target")
    destination = Path(destination)
    if destination.exists():
        raise ValueError("Immutable repair plan exists")
    records = json.loads(Path(payload_path).read_text())
    groups = {}
    for record in records:
        if file_hash(record["evidence"]) != record["evidence_sha256"]:
            raise ValueError("Source evidence changed")
        groups.setdefault(record["symbol"], []).append(record)
    with pool_lock(lab_root, write=True), catalog_session(lab_root):
        assert_readable(lab_root)
        versions = inventory(lab_root)
        initial = signature(versions)
        steps = []
        for symbol, group in sorted(groups.items()):
            before = signature(versions)
            intermediate = []

            def daily_checkpoint():
                refresh_symbol(lab_root, versions, symbol)
                intermediate.append(signature(versions))

            result = apply_symbol(lab_root, symbol, group, after_daily=daily_checkpoint)
            refresh_symbol(lab_root, versions, symbol)
            steps.append(
                dict(
                    symbol=symbol,
                    records=group,
                    before=before,
                    after_daily=intermediate[0],
                    after=signature(versions),
                    rehearsal=result,
                )
            )
        spec = dict(
            format=1,
            kind="repair",
            root=str(target_root),
            source=initial,
            implementation=implementation(),
            repair_code=file_hash(__file__),
            recovery_manifest=str(Path(manifest).resolve()),
            recovery_sha256=file_hash(manifest),
            steps=steps,
        )
        spec["plan_id"] = signature(spec)
        save(destination, spec)
    return spec


def execute_repair(plan_path, state_path, *, max_symbols, approval=None):
    if isinstance(max_symbols, bool) or not isinstance(max_symbols, int) or max_symbols < 1:
        raise ValueError("max_symbols must be positive")
    spec = json.loads(Path(plan_path).read_text())
    plan_id = spec.pop("plan_id")
    if signature(spec) != plan_id:
        raise ValueError("Repair plan integrity failure")
    spec["plan_id"] = plan_id
    if spec["implementation"] != implementation() or spec["repair_code"] != file_hash(__file__):
        raise ValueError("Implementation changed")
    root = Path(spec["root"]).resolve()
    manifest = json.loads(Path(spec["recovery_manifest"]).read_text())
    if file_hash(spec["recovery_manifest"]) != spec["recovery_sha256"]:
        raise ValueError("Recovery manifest changed")
    if manifest.get("snapshot"):
        snapshot = Path(manifest["snapshot"]).resolve()
        if root == snapshot or snapshot in root.parents:
            raise ValueError("Immutable snapshot is not a repair target")
    if root == (Path.home() / ".aspool").resolve():
        gate = json.loads(Path(approval).read_text()) if approval else {}
        if (
            gate.get("plan_id") != plan_id
            or gate.get("source") != spec["source"]
            or not gate.get("single_writer")
            or not gate.get("recovery_verified")
        ):
            raise ValueError("Production approval required")
    state_path = Path(state_path).resolve()
    if root == state_path or root in state_path.parents or state_path == Path(plan_path).resolve():
        raise ValueError("State must be outside pool and distinct from plan")
    for step in spec["steps"]:
        for record in step["records"]:
            if file_hash(record["evidence"]) != record["evidence_sha256"]:
                raise ValueError("Independent source evidence changed")
    with pool_lock(root, write=True), catalog_session(root) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS remediation_runs (
            plan_id VARCHAR PRIMARY KEY, state_json VARCHAR NOT NULL,
            state_hash VARCHAR NOT NULL, previous_hash VARCHAR)""")
        stored = conn.execute(
            "SELECT state_json,state_hash,previous_hash FROM remediation_runs WHERE plan_id=?",
            [plan_id],
        ).fetchone()
        if stored:
            state = json.loads(stored[0])
            if signature(state) != stored[1]:
                raise ValueError("Catalog state integrity failure")
            if (
                state_path.exists()
                and signature(json.loads(state_path.read_text())) not in stored[1:]
            ):
                raise ValueError("External state integrity failure")
        else:
            if state_path.exists():
                raise ValueError("State has no authoritative ledger")
            state = dict(plan_id=plan_id, next=0, pending=None, completed=[])
        if (
            state["next"] != len(state["completed"])
            or not 0 <= state["next"] <= len(spec["steps"])
            or [item["symbol"] for item in state["completed"]]
            != [step["symbol"] for step in spec["steps"][: state["next"]]]
        ):
            raise ValueError("Invalid repair checkpoint sequence")
        recover(root)
        versions = inventory(root)
        current = signature(versions)
        expected = (
            spec["source"] if state["next"] == 0 else spec["steps"][state["next"] - 1]["after"]
        )
        if state["pending"] is not None:
            step = spec["steps"][state["next"]]
            if current == step["after"]:
                state["completed"].append(dict(symbol=step["symbol"], recovered=True))
                state["next"] += 1
                state["pending"] = None
                checkpoint(conn, state_path, state)
            elif current not in (expected, step["after_daily"]):
                raise ValueError("Pending repair source differs from frozen recovery checkpoints")
        elif current != expected:
            raise ValueError("Source differs from rehearsed checkpoint")
        checkpoint(conn, state_path, state)
        for step in spec["steps"][state["next"] : state["next"] + max_symbols]:
            state["pending"] = step["symbol"]
            checkpoint(conn, state_path, state)
            result = apply_symbol(root, step["symbol"], step["records"])
            refresh_symbol(root, versions, step["symbol"])
            if signature(versions) != step["after"]:
                raise ValueError("Repair output differs from isolated rehearsal")
            state["completed"].append(result)
            state["next"] += 1
            state["pending"] = None
            checkpoint(conn, state_path, state)
    return state
