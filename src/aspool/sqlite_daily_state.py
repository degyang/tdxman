"""Evidence-based historical state normalization with unchanged numeric dependencies."""

import hashlib
import json
import time

from .api_contract import DataPoolError
from .sqlite_canonical import canonical_writer
from .sqlite_daily_derived import classify_trading, finite

SOURCE = "derived:stored_ohlcv"


def has_real_trade(row):
    """Positive finite volume and valid prices establish an observed trade."""
    prices = [row.get(k) for k in ("open", "high", "low", "close")]
    return (
        all(finite(v, positive=True) for v in prices)
        and prices[2] <= min(prices[0], prices[3]) <= max(prices[0], prices[3]) <= prices[1]
        and finite(row.get("volume"), positive=True)
        and finite(row.get("amount"), positive=True)
    )


def numeric_digest(rows):
    """Exclude only the three owned state fields; all Enriched values are checked."""
    return hashlib.sha256(
        json.dumps(
            [
                {
                    k: v
                    for k, v in r.items()
                    if k not in {"trading_status", "trading_status_source", "updated_at"}
                }
                for r in rows
            ],
            sort_keys=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


@canonical_writer("daily_state_normalize")
def normalize_traded_states(conn, *, symbol, start, end, max_rows=5000, publication_stamp=None):
    """Normalize only absent states whose current numeric dependency inputs are identical.

    Every other case requires the full daily writer. This operation changes only
    one WAL store, features, and keeps its publication metadata in that transaction.
    Missing adjustment/ST/reference inputs remain missing; no new metric is invented.
    """
    if conn.in_transaction or not 1 <= max_rows <= 5000:
        raise ValueError("Expected idle connection and max_rows=1..5000")
    conn.execute("BEGIN IMMEDIATE")
    try:
        cursor = conn.execute(
            "SELECT f.*,b.open,b.high,b.low,b.close,b.volume,b.amount,b.trade_date AS bar_date "
            "FROM daily_features f JOIN daily_bars b USING(symbol,trade_date) "
            "WHERE f.symbol=? AND f.trade_date BETWEEN ? AND ? "
            "AND (f.trading_status IS NULL OR f.trading_status='') "
            "AND f.calc_status='TRADED' ORDER BY f.trade_date LIMIT ?",
            (symbol, start, end, max_rows + 1),
        )
        names = [x[0] for x in cursor.description]
        rows = [dict(zip(names, r)) for r in cursor]
        if len(rows) > max_rows:
            raise DataPoolError("LOCAL_UPDATE_BUDGET_EXCEEDED", "State window exceeds row budget")
        rows = [r for r in rows if has_real_trade(r)]
        before = numeric_digest(rows)
        if not rows:
            conn.rollback()
            return dict(changed_rows=0, enriched_verified_rows=0, numeric_sha256=before)
        # All consumers accept NULL and TRADING identically for these inputs:
        # daily classification, reference selection, valid-volume history,
        # six-dimension volumes, daily aggregates and board returns.
        for row in rows:
            assert classify_trading(row) == classify_trading(dict(row, trading_status="TRADING"))
        stamp = max(
            time.time_ns() // 1000, publication_stamp or 0, max(r["updated_at"] for r in rows) + 1
        )
        conn.executemany(
            "UPDATE daily_features SET trading_status='TRADING',trading_status_source=?,"
            "updated_at=? WHERE symbol=? AND trade_date=? "
            "AND (trading_status IS NULL OR trading_status='') AND calc_status='TRADED'",
            [(SOURCE, stamp, symbol, r["trade_date"]) for r in rows],
        )
        # Refresh existing publication stamps, including five following sessions.
        # Numeric inputs and outputs are reused only after the equivalence checks.
        first = min(r["trade_date"] for r in rows)
        last = max(r["trade_date"] for r in rows)
        successors = [
            r[0]
            for r in conn.execute(
                "SELECT trade_date FROM market_sessions WHERE trade_date>? "
                "ORDER BY trade_date LIMIT 5",
                (last,),
            )
        ]
        through = max([last] + successors)
        conn.execute(
            "UPDATE market_daily_summary SET updated_at=max(updated_at+1,?) "
            "WHERE period_start<=? AND period_end>=? AND updated_at<?",
            (publication_stamp or stamp, through, first, publication_stamp or stamp),
        )
        cursor = conn.execute(
            "SELECT f.*,b.open,b.high,b.low,b.close,b.volume,b.amount,b.trade_date AS bar_date "
            "FROM daily_features f JOIN daily_bars b USING(symbol,trade_date) "
            "WHERE f.symbol=? AND f.trade_date BETWEEN ? AND ? ORDER BY f.trade_date",
            (symbol, start, end),
        )
        names = [x[0] for x in cursor.description]
        after = {r["trade_date"]: r for r in (dict(zip(names, v)) for v in cursor)}
        selected = [after[r["trade_date"]] for r in rows]
        if numeric_digest(selected) != before or any(
            r["trading_status"] != "TRADING" or r["trading_status_source"] != SOURCE
            for r in selected
        ):
            raise DataPoolError("SOURCE_CHANGED", "State repair changed numeric Enriched inputs")
        conn.execute(
            "UPDATE features.feature_state SET updated_at=max(updated_at+1,?) "
            "WHERE scope_key='all'",
            (stamp,),
        )
        conn.commit()
        return dict(changed_rows=len(rows), enriched_verified_rows=len(rows), numeric_sha256=before)
    except BaseException:
        conn.rollback()
        raise


@canonical_writer("daily_state_normalize")
def finalize_state_publication(conn, *, start, end):
    """Publish a final cache stamp after an actual completed normalization run."""
    if conn.in_transaction:
        raise ValueError("Expected an idle connection")
    conn.execute("BEGIN IMMEDIATE")
    try:
        stamp = time.time_ns() // 1000
        following = [
            r[0]
            for r in conn.execute(
                "SELECT trade_date FROM market_sessions WHERE trade_date>? "
                "ORDER BY trade_date LIMIT 5",
                (end,),
            )
        ]
        through = max([end] + following)
        cursor = conn.execute(
            "UPDATE market_daily_summary SET updated_at=max(updated_at+1,?) "
            "WHERE period_start<=? AND period_end>=?",
            (stamp, through, start),
        )
        summaries = cursor.rowcount
        conn.execute(
            "UPDATE features.feature_state SET updated_at=max(updated_at+1,?) "
            "WHERE scope_key='all'",
            (stamp,),
        )
        conn.commit()
        return dict(summary_publication_rows=summaries, publication_stamp=stamp)
    except BaseException:
        conn.rollback()
        raise
