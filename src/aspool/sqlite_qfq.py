"""Downloaded forward-adjusted OHLC are base observations, not calculated factors."""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path

PRICES = ("open", "high", "low", "close")
TABLE = "stock_qfq_bars"
DDL = """
CREATE TABLE IF NOT EXISTS stock_qfq_bars (
    symbol TEXT NOT NULL, trade_date TEXT NOT NULL,
    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
    source TEXT NOT NULL, source_as_of TEXT NOT NULL, updated_at INTEGER NOT NULL,
    PRIMARY KEY(symbol,trade_date)
) STRICT, WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS stock_qfq_by_date ON stock_qfq_bars(trade_date,symbol);
"""


def ensure_qfq_schema(root):
    """Add an optional base table under the existing pool lock; schema stays additive."""
    from .pool import pool_lock
    from .sqlite_publication import assert_published

    root = Path(root).resolve()
    with pool_lock(root, write=True):
        assert_published(root)
        with sqlite3.connect((root / "stocks.sqlite").as_uri() + "?mode=rw", uri=True) as conn:
            conn.executescript("BEGIN IMMEDIATE;" + DDL + "COMMIT;")


def valid_prices(row, *, positive=False):
    values = [row.get(k) for k in PRICES]
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
        return False
    op, hi, lo, cl = values
    return (not positive or lo > 0) and lo <= min(op, cl) <= max(op, cl) <= hi


def paired_reference(raw, qfq, previous_raw, previous_qfq):
    """Map the previous adjusted close into today's raw price scale.

    Fit an affine map, not close_qfq/close_raw: cash dividends can add a constant.
    Flat/rounded bars cannot determine the slope; retain a labelled raw fallback.
    All four prices must agree to the provider's displayed precision.
    """
    if not previous_raw or not valid_prices(previous_raw, positive=True):
        return None, None
    fallback = (previous_raw["close"], "estimated:previous_raw_close")
    if not qfq or not previous_qfq or not valid_prices(raw, positive=True):
        return fallback
    if not valid_prices(qfq) or not valid_prices(previous_qfq):
        return fallback
    if qfq.get("source_as_of") != previous_qfq.get("source_as_of"):
        return fallback
    if qfq.get("source") != previous_qfq.get("source"):
        return fallback
    if shared_affine_map(raw, qfq, previous_raw, previous_qfq):
        return previous_raw["close"], "derived:paired_qfq_no_action"
    spread = raw["high"] - raw["low"]
    if spread < 0.01 or qfq["high"] - qfq["low"] <= 0:
        return fallback
    slope = (qfq["high"] - qfq["low"]) / spread
    intercept = qfq["close"] - slope * raw["close"]
    tolerance = 0.0051 + abs(qfq["close"]) * 2e-7
    if any(abs(qfq[k] - (slope * raw[k] + intercept)) > tolerance for k in PRICES):
        return fallback
    reference = (previous_qfq["close"] - intercept) / slope
    if not math.isfinite(reference) or reference <= 0:
        return fallback
    # Display rounding can move a reconstructed reference by a cent. When the
    # whole two-day pair shares one map, the exact prior raw close is preferable.
    if all(
        abs(previous_qfq[k] - (slope * previous_raw[k] + intercept)) <= tolerance for k in PRICES
    ):
        return previous_raw["close"], "derived:paired_qfq_no_action"
    return reference, "derived:paired_qfq_affine"


def shared_affine_map(raw, qfq, previous_raw, previous_qfq):
    """Allow independent cent rounding of all eight prices in a shared map.

    A slope taken from only today's high/low can amplify one-cent display
    rounding and falsely infer a corporate action. Intersect all pairwise
    feasible slope intervals instead; they express the existence of one common
    intercept satisfying the displayed rounding bounds.
    """
    points = [(r[k], q[k]) for r, q in ((raw, qfq), (previous_raw, previous_qfq)) for k in PRICES]
    lower, upper = 0.0, math.inf
    tolerance = 0.0102 + max(abs(y) for _, y in points) * 4e-7
    for index, (x, y) in enumerate(points):
        for other_x, other_y in points[index + 1 :]:
            dx, dy = x - other_x, y - other_y
            if abs(dx) < 1e-9:
                if abs(dy) > tolerance:
                    return False
                continue
            a, b = (dy - tolerance) / dx, (dy + tolerance) / dx
            lower, upper = max(lower, min(a, b)), min(upper, max(a, b))
            if lower > upper or upper <= 0:
                return False
    return upper > 0 and lower <= upper


def read_downloaded_qfq(root, *, start, end, symbols=None, max_rows=100_000):
    """Read the single latest downloaded snapshot; volume stays in raw bars."""
    from datetime import date

    import pandas as pd

    from .api_contract import DataPoolError
    from .pool import _normalize_symbol
    from .sqlite_stock_store import stock_connection

    if not 0 <= (date.fromisoformat(end) - date.fromisoformat(start)).days <= 3660:
        raise DataPoolError("INVALID_ARGUMENT", "Specify at most ten years")
    if not 1 <= max_rows <= 1_000_000:
        raise DataPoolError("INVALID_ARGUMENT", "max_rows must be between 1 and 1000000")
    symbols = [_normalize_symbol(s) for s in symbols] if symbols is not None else None
    with stock_connection(Path(root), read_only=True) as conn:
        if not conn.execute("SELECT 1 FROM main.sqlite_master WHERE name=?", (TABLE,)).fetchone():
            return pd.DataFrame(columns=["symbol", "trade_date", *PRICES, "source", "source_as_of"])
        where, params = ["trade_date BETWEEN ? AND ?"], [start, end]
        if symbols is not None:
            if not symbols:
                return pd.DataFrame()
            where.append("symbol IN (" + ",".join("?" for _ in symbols) + ")")
            params.extend(symbols)
        cur = conn.execute(
            "SELECT * FROM stock_qfq_bars WHERE "
            + " AND ".join(where)
            + " ORDER BY symbol,trade_date LIMIT ?",
            [*params, max_rows + 1],
        )
        frame = pd.DataFrame(cur.fetchall(), columns=[v[0] for v in cur.description])
        if len(frame) > max_rows:
            raise DataPoolError("LOCAL_READ_BUDGET_EXCEEDED", "Narrow the qfq window or symbols")
    frame.attrs.update(
        layer="base",
        adjustment="downloaded_qfq",
        snapshot="latest_source",
        volume="read from unadjusted daily bars",
    )
    return frame


def refresh_references(conn, symbol, days, *, allow_fallback=False):
    """Rebuild qfq-derived references after a base-only replica or daily update."""
    from .sqlite_daily_derived import _factor_at, finite
    from .sqlite_daily_update import _read

    if not conn.execute("SELECT 1 FROM main.sqlite_master WHERE name=?", (TABLE,)).fetchone():
        return
    for day in days:
        key = (symbol, day)
        raw, fact = _read(conn, "daily_bars", key), _read(conn, "daily_features", key)
        if not raw or not fact:
            continue
        source = str(fact.get("source_pre_close_source") or "")
        if (
            finite(fact.get("source_pre_close"), positive=True)
            and source
            and not source.startswith(("raw_fallback:", "derived:", "estimated:"))
        ):
            continue
        qfq = _read(conn, TABLE, key)
        if not qfq and not allow_fallback:
            continue
        row = conn.execute(
            "SELECT trade_date FROM daily_bars WHERE symbol=? AND trade_date<? "
            "AND volume>0 AND low>0 ORDER BY trade_date DESC LIMIT 1",
            (symbol, day),
        ).fetchone()
        if not row:
            continue
        previous = (symbol, row[0])
        reference, basis = paired_reference(
            raw, qfq, _read(conn, "daily_bars", previous), _read(conn, TABLE, previous)
        )
        if allow_fallback and basis == "estimated:previous_raw_close":
            old_factor, new_factor = (
                _factor_at(conn, symbol, previous[1]),
                _factor_at(conn, symbol, day),
            )
            if old_factor and new_factor:
                reference = _read(conn, "daily_bars", previous)["close"] * old_factor / new_factor
                basis = "derived:previous_close+selected_factors"
        if reference is not None:
            conn.execute(
                "UPDATE daily_features SET pre_close=?,pre_close_source=? "
                "WHERE symbol=? AND trade_date=?",
                (reference, basis, symbol, day),
            )
