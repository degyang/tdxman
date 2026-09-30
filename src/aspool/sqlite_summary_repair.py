"""Repair absent daily aggregates without re-fetching otherwise complete bars."""

import sqlite3
import time
from pathlib import Path

from .platform_v2 import mirror_platform_v2, require_current_mirror
from .pool import pool_lock
from .sqlite_canonical import canonical_writer
from .sqlite_market_summary import SCOPES, recompute_daily_summary
from .sqlite_stock_store import stock_connection


@canonical_writer("summary_recompute")
def apply_summary_changes(conn, *, day):
    if conn.in_transaction:
        raise ValueError("Summary writer requires an idle connection")
    try:
        conn.execute("BEGIN IMMEDIATE")
        result = recompute_daily_summary(conn, trade_date=day)
        conn.commit()
        return result
    except BaseException:
        conn.rollback()
        raise


def repair_missing_market_summaries(root, sessions):
    """Recompute missing source scopes and mirror missing target scopes by date.

    Only dates with existing stock features qualify. The caller obtains the
    bounded session list from the persisted trading calendar.
    """
    root = Path(root).resolve()
    days = sorted(set(sessions))
    if len(days) > 60:
        raise ValueError("Summary repair is limited to 60 sessions")
    result = {"dates": [], "changed_rows": 0}
    from .platform_v2 import layout_version

    if layout_version(root) == 3:
        with stock_connection(root, read_only=False) as source:
            for day in days:
                scopes = {
                    r[0]
                    for r in source.execute(
                        "SELECT scope FROM market_daily_summary "
                        "WHERE frequency='D' AND period_key=?",
                        (day,),
                    )
                }
                if (
                    set(SCOPES) <= scopes
                    or not source.execute(
                        "SELECT 1 FROM daily_features WHERE trade_date=? LIMIT 1", (day,)
                    ).fetchone()
                ):
                    continue
                changed = apply_summary_changes(source, day=day)["changed_rows"]
                result["dates"].append(day)
                result["changed_rows"] += changed
        return result
    with pool_lock(root, write=True), stock_connection(root, read_only=False) as source:
        require_current_mirror(root)
        mirror_path = root / "features.sqlite"
        target = (
            sqlite3.connect(mirror_path.as_uri() + "?mode=ro", uri=True)
            if mirror_path.is_file()
            else None
        )
        try:
            for day in days:
                if not source.execute(
                    "SELECT 1 FROM daily_features WHERE trade_date=? LIMIT 1", (day,)
                ).fetchone():
                    continue
                sql = "SELECT scope FROM {} WHERE frequency='D' AND period_key=?"
                source_scopes = {
                    row[0] for row in source.execute(sql.format("market_daily_summary"), (day,))
                }
                target_scopes = (
                    {row[0] for row in target.execute(sql.format("market_regime_features"), (day,))}
                    if target is not None
                    else source_scopes
                )
                if set(SCOPES) <= source_scopes and set(SCOPES) <= target_scopes:
                    continue
                with source:
                    source.execute("BEGIN IMMEDIATE")
                    state = source.execute(
                        "SELECT revision FROM dataset_state WHERE dataset='stock_raw'"
                    ).fetchone()
                    previous = state[0] if state else 0
                    changed = 0
                    if not set(SCOPES) <= source_scopes:
                        changed = recompute_daily_summary(source, trade_date=day)["changed_rows"]
                    revision = previous + int(bool(changed))
                    if changed:
                        source.execute(
                            "INSERT INTO dataset_state VALUES ('stock_raw',?,?,?) "
                            "ON CONFLICT(dataset) DO UPDATE SET revision=excluded.revision,"
                            "updated_at=excluded.updated_at",
                            (revision, day, time.time_ns() // 1000),
                        )
                if target is not None:
                    mirror_platform_v2(
                        root,
                        symbols=[],
                        dates=[day],
                        raw_revision=revision,
                        previous_raw_revision=previous,
                    )
                result["dates"].append(day)
                result["changed_rows"] += changed
        finally:
            if target is not None:
                target.close()
    return result
