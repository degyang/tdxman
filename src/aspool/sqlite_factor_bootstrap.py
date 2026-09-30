"""Verified listing-episode factor initialization; bounded explicit maintenance.

Price actions use SDK per-share amounts. Vendor affine prices validate action
completeness; the public adjustment contract remains cumulative scalar factors.
"""

import hashlib
import json
import math
import re
import time
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from .api_contract import DataPoolError
from .pool import pool_lock
from .sqlite_canonical import canonical_writer
from .sqlite_daily_derived import classify_trading
from .sqlite_reference_factors import _insert_action, build_selected_factors, normalize_actions
from .sqlite_stock_store import stock_connection

METHOD = "listing-event-bootstrap-v1"
MAX_BARS = 2000


def validate_evidence(symbol, listing_date, as_of, frames, events):
    """Reject truncated episodes, unsupported actions and inconsistent OHLC pairs."""
    if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", symbol):
        raise ValueError("Expected canonical stock symbol")
    date.fromisoformat(listing_date)
    date.fromisoformat(as_of)
    arrays, days = {}, None
    for name in ("NONE", "QFQ", "HFQ"):
        frame = frames[name].copy()
        axis = pd.to_datetime(frame.datetime).dt.strftime("%Y-%m-%d").tolist()
        if not axis or axis != sorted(set(axis)) or len(axis) >= MAX_BARS:
            raise ValueError("Empty, duplicate, unordered or potentially truncated price history")
        if axis[0] != listing_date or axis[-1] != as_of:
            raise ValueError("Prices must cover the exact current listing episode through as-of")
        if days is not None and days != axis:
            raise ValueError("Adjustment streams have different date axes")
        days = axis
        values = frame[["open", "high", "low", "close"]].to_numpy(dtype=float)
        if not np.isfinite(values).all():
            raise ValueError("Non-finite price evidence")
        if name == "NONE" and (
            (values <= 0).any()
            or (values[:, 2] > np.minimum(values[:, 0], values[:, 3])).any()
            or (values[:, 1] < np.maximum(values[:, 0], values[:, 3])).any()
        ):
            raise ValueError("Invalid raw price evidence")
        arrays[name] = values
    selected = []
    slots = Counter()
    for raw in events:
        event = dict(raw)
        day = str(event.get("effective_date", event.get("date")))[:10]
        date.fromisoformat(day)
        category = int(event["category"])
        slots[(day, category)] += 1
        if not listing_date <= day <= as_of:
            continue
        if category not in range(1, 11):
            raise ValueError(f"Unsupported price event category {category}")
        event.update(effective_date=day, source="tdx:xdxr")
        event.setdefault("source_key", f"category={category}:slot={slots[(day, category)]}")
        if category == 1:
            for canonical, original in (
                ("cash_dividend_per_share", "fenhong"),
                ("bonus_shares_per_share", "songzhuangu"),
                ("rights_shares_per_share", "peigu"),
                ("rights_price", "peigujia"),
            ):
                event[canonical] = event.get(canonical, event.get(original))
        selected.append(event)
    groups = normalize_actions(selected)
    price_events = [e for g in groups for e in g["events"] if e["category"] == 1]
    # Multiple actions on a date require a separately verified ordering contract.
    if len({e["effective_date"] for e in price_events}) != len(price_events):
        raise ValueError("Ambiguous same-day price actions")
    if any(e["effective_date"] == listing_date for e in price_events):
        raise ValueError("Listing-day action has no verified preceding traded close")
    axis = np.array(days)
    expected = {name: arrays["NONE"].copy() for name in ("QFQ", "HFQ")}
    for name in expected:
        for event in price_events if name == "QFQ" else reversed(price_events):
            cash = event["cash_dividend_per_share"]
            rights = event["rights_shares_per_share"]
            price = event.get("rights_price") or 0
            divisor = 1 + event["bonus_shares_per_share"] + rights
            mask = (
                axis < event["effective_date"] if name == "QFQ" else axis >= event["effective_date"]
            )
            if name == "QFQ":
                expected[name][mask] = (expected[name][mask] - cash + rights * price) / divisor
            else:
                expected[name][mask] = expected[name][mask] * divisor + cash - rights * price
        if (np.abs(expected[name] - arrays[name]) > 0.0051 + np.abs(arrays[name]) * 1.3e-7).any():
            raise ValueError(f"{name} prices disagree with complete event history")
    proof = dict(
        method=METHOD,
        symbol=symbol,
        listing_date=listing_date,
        as_of=as_of,
        rows=len(days),
        events=selected,
        prices={name: value.tolist() for name, value in arrays.items()},
        dates=days,
    )
    digest = hashlib.sha256(json.dumps(proof, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return dict(
        symbol=symbol,
        listing_date=listing_date,
        as_of=as_of,
        days=days,
        raw=arrays["NONE"].tolist(),
        validation_prices={name: values.tolist() for name, values in arrays.items()},
        events=selected,
        price_events=price_events,
        proof=dict(
            method=METHOD,
            source="tdx:xdxr+mac:none,qfq,hfq",
            sha256=digest,
            listing_date=listing_date,
            as_of=as_of,
            rows=len(days),
        ),
    )


def fetch_evidence(symbol, listing_date, as_of):
    from .source_retry import read_with_retry

    return read_with_retry(lambda: _fetch_evidence(symbol, listing_date, as_of))


def _fetch_evidence(symbol, listing_date, as_of):
    from tdxman.client import TdxClient
    from tdxman.mac.client import MacClient
    from tdxman.mac.enums import Adjust, Period
    from tdxman.models.enums import Market

    from .enrichment import _fetch

    code, market = symbol.split(".")
    with TdxClient.from_best_host(timeout=8) as client:
        source = _fetch(client, (code, market))
    finance = source["finance"]
    if int(finance.get("market", -1)) != int(Market[market]) or str(
        finance.get("ipo_date")
    ) != listing_date.replace("-", ""):
        raise ValueError("Finance identity or current listing date disagrees with directory")
    with MacClient.from_best_host(timeout=8) as client:
        frames = {
            kind.name: client.get_stock_kline(
                Market[market], code, Period.DAILY, count=MAX_BARS, adjust=kind
            )
            for kind in (Adjust.NONE, Adjust.QFQ, Adjust.HFQ)
        }
    if any(frame.empty for frame in frames.values()):
        from .source_retry import EmptySourceResponse

        raise EmptySourceResponse("Empty factor-verification price stream")
    if as_of > source["fetched_date"]:
        raise ValueError("Cannot prove future event coverage")
    result = validate_evidence(symbol, listing_date, as_of, frames, source["events"])
    result["proof"]["finance_identity"] = dict(
        code=code,
        market=int(finance["market"]),
        ipo_date=int(finance["ipo_date"]),
        fetched_date=source["fetched_date"],
    )
    return result


def _recompute_ma(conn, item, factors):
    """Only adjustment-dependent fields change; preserve established limit facts."""
    symbol = item["symbol"]
    cursor = conn.execute(
        "SELECT f.trade_date,f.ma20,f.above_ma20,f.updated_at,f.trading_status,"
        "b.trade_date AS bar_date,b.open,b.high,b.low,b.close,b.volume,b.amount "
        "FROM daily_features f LEFT JOIN daily_bars b USING(symbol,trade_date) "
        "WHERE f.symbol=? AND f.trade_date BETWEEN ? AND ? ORDER BY f.trade_date",
        (symbol, item["listing_date"], item["as_of"]),
    )
    names = [c[0] for c in cursor.description]
    history = deque(maxlen=20)
    changed = set()
    for raw in cursor:
        row = dict(zip(names, raw))
        day = row["trade_date"]
        factor = next(
            f["cumulative_factor"]
            for f in reversed(factors)
            if f["valid_from"] <= day <= f["valid_through"]
        )
        ma = above = None
        if classify_trading(row) == "TRADED":
            history.append((row["close"], factor))
            # Existing public daily-derived contract excludes Beijing MA20.
            if len(history) == 20 and not symbol.endswith(".BJ"):
                ma = math.fsum(close * (scale / factor) / 20 for close, scale in history)
                above = int(row["close"] > ma)
        if (ma, above) != (row["ma20"], row["above_ma20"]):
            conn.execute(
                "UPDATE daily_features SET ma20=?,above_ma20=?,updated_at=? "
                "WHERE symbol=? AND trade_date=?",
                (ma, above, max(time.time_ns() // 1000, row["updated_at"] + 1), symbol, day),
            )
            changed.add(day)
    return changed


@canonical_writer("factor_bootstrap")
def publish_bootstrap(conn, evidence, *, maintenance=False):
    """Publish bounded factors, MA20 and summaries with the durable shared writer."""
    from .sqlite_market_summary import recompute_daily_summary

    items = list(evidence)
    if not items:
        return dict(symbols=0, factors=0, feature_rows=0, summary_rows=0)
    if len(items) > (200 if maintenance else 32) or len({i["symbol"] for i in items}) != len(items):
        raise ValueError("Bootstrap symbol budget exceeded or duplicate symbols")
    if sum(len(i["days"]) for i in items) > (100_000 if maintenance else 1920):
        raise ValueError("Bootstrap row budget exceeded")
    if not maintenance and any(len(i["days"]) > 60 for i in items):
        raise ValueError("Older missing factors require explicit --maintenance")
    if conn.in_transaction:
        raise ValueError("Bootstrap owns its transaction")
    result = dict(symbols=0, factors=0, feature_rows=0, summary_rows=0)
    affected = set()
    feature_budget_used = 0
    deadline = time.monotonic() + (1800 if maintenance else 60)
    conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    try:
        conn.execute("BEGIN IMMEDIATE")
        for item in items:
            symbol, first, last = item["symbol"], item["listing_date"], item["as_of"]
            existing = conn.execute(
                "SELECT 1 FROM corporate_actions WHERE symbol=? AND record_kind IN "
                "('factor','factor_anchor') LIMIT 1",
                (symbol,),
            ).fetchone()
            if existing:
                continue
            local = conn.execute(
                "SELECT trade_date,open,high,low,close FROM daily_bars WHERE symbol=? "
                "AND trade_date BETWEEN ? AND ? ORDER BY trade_date LIMIT 2001",
                (symbol, first, last),
            ).fetchall()
            if [r[0] for r in local] != item["days"] or any(
                any(abs(a - b) > 0.001 for a, b in zip(row[1:], prices))
                for row, prices in zip(local, item["raw"])
            ):
                raise ValueError(f"{symbol}: local raw history differs; explicit repair required")
            feature_dates = conn.execute(
                "SELECT trade_date FROM daily_features WHERE symbol=? AND trade_date BETWEEN ? "
                "AND ? ORDER BY trade_date LIMIT ?",
                (symbol, first, last, 2001 if maintenance else 61),
            ).fetchall()
            feature_budget_used += len(feature_dates)
            if len(feature_dates) > (2000 if maintenance else 60) or feature_budget_used > (
                100_000 if maintenance else 1920
            ):
                raise ValueError("Bootstrap feature-history budget exceeded")
            missing = conn.execute(
                "SELECT count(*) FROM daily_bars b LEFT JOIN daily_features f "
                "USING(symbol,trade_date) WHERE b.symbol=? AND b.trade_date BETWEEN ? AND ? "
                "AND f.trade_date IS NULL",
                (symbol, first, last),
            ).fetchone()[0]
            if missing:
                raise ValueError(f"{symbol}: missing daily feature rows")
            closes = {}
            for event in item["price_events"]:
                day = event["effective_date"]
                from .sqlite_reference_factors import _coverage_previous_close

                prior = _coverage_previous_close(conn, symbol, day)
                if not prior or prior[0] < first:
                    raise ValueError(f"{symbol}: missing traded event predecessor")
                # Adjust a suspended predecessor through any intervening actions.
                value = prior[1]
                for earlier in item["price_events"]:
                    if prior[0] < earlier["effective_date"] < day:
                        value = (
                            value
                            - earlier["cash_dividend_per_share"]
                            + earlier["rights_shares_per_share"]
                            * (earlier.get("rights_price") or 0)
                        ) / (
                            1
                            + earlier["bonus_shares_per_share"]
                            + earlier["rights_shares_per_share"]
                        )
                closes[day] = value
            initial_end = (
                (
                    date.fromisoformat(item["price_events"][0]["effective_date"])
                    - timedelta(days=1)
                ).isoformat()
                if item["price_events"]
                else last
            )
            factors = build_selected_factors(
                events=item["price_events"],
                prior_closes=closes,
                verified_no_event_range=(first, initial_end, "tdx:listing_episode"),
                through=last,
            )
            for event in item["events"]:
                _insert_action(conn, symbol, event, "tdx:xdxr")
            payload = json.dumps(dict(bootstrap=item["proof"]), sort_keys=True)
            for factor in factors:
                conn.execute(
                    "INSERT INTO corporate_actions(symbol,effective_date,record_kind,source,"
                    "source_key,event_factor,cumulative_factor,valid_from,valid_through,"
                    "factor_basis,payload_json,updated_at) "
                    "VALUES (?,?,'factor','derived:event_chain','selected',?,?,?,?,?,?,?)",
                    (
                        symbol,
                        factor["effective_date"],
                        factor["event_factor"],
                        factor["cumulative_factor"],
                        factor["valid_from"],
                        factor["valid_through"],
                        factor["factor_basis"],
                        payload,
                        time.time_ns() // 1000,
                    ),
                )
            changed = _recompute_ma(conn, item, factors)
            affected.update(changed)
            result["feature_rows"] += len(changed)
            result["symbols"] += 1
            result["factors"] += len(factors)
        for day in sorted(affected):
            if conn.execute(
                "SELECT 1 FROM market_daily_summary WHERE frequency IN ('W','M') "
                "AND period_start<=? AND period_end>=? LIMIT 1",
                (day, day),
            ).fetchone():
                raise DataPoolError(
                    "PERIOD_UPDATE_NOT_READY", "Existing period summaries require a period writer"
                )
            stats = recompute_daily_summary(conn, trade_date=day, inputs_changed=True)
            result["summary_rows"] += stats["changed_rows"]
        conn.commit()
    except BaseException:
        conn.set_progress_handler(None, 0)
        conn.rollback()
        raise
    finally:
        conn.set_progress_handler(None, 0)
    return result


def bootstrap_factors(root, *, as_of=None, symbols=(), maintenance=False, fetcher=fetch_evidence):
    root = Path(root).resolve()
    with pool_lock(root), duckdb.connect(str(root / "catalog.duckdb"), read_only=True) as catalog:
        latest = catalog.execute(
            "SELECT max(trade_date) FROM security_calendar WHERE is_open"
        ).fetchone()[0]
        if latest is None:
            raise ValueError("No confirmed market session")
        target = as_of or latest.isoformat()
        if (
            target > latest.isoformat()
            or not catalog.execute(
                "SELECT 1 FROM security_calendar WHERE trade_date=? AND is_open", [target]
            ).fetchone()
        ):
            raise ValueError("As-of must be a confirmed market session")
        directory = catalog.execute(
            "SELECT symbol,listing_date FROM securities WHERE asset_type='stock' AND active "
            "AND listing_date<=? AND (delisting_date IS NULL OR delisting_date>?) ORDER BY symbol",
            [target, target],
        ).fetchall()
    requested = set(symbols)
    if requested - {s for s, _ in directory}:
        raise ValueError("Requested symbol lacks a current verified listing date")
    candidates, deferred = [], []
    with stock_connection(root) as conn:
        for symbol, listing in directory:
            if requested and symbol not in requested:
                continue
            if conn.execute(
                "SELECT 1 FROM corporate_actions WHERE symbol=? AND record_kind IN "
                "('factor','factor_anchor') LIMIT 1",
                (symbol,),
            ).fetchone():
                continue
            # Bounded index walk, never scan full history for daily discovery.
            rows = conn.execute(
                "SELECT trade_date FROM daily_bars WHERE symbol=? "
                "AND trade_date>=? AND trade_date<=? ORDER BY trade_date LIMIT ?",
                (symbol, listing.isoformat(), target, 2001 if maintenance else 61),
            ).fetchall()
            if not rows:
                continue
            if len(rows) > (1999 if maintenance else 60):
                deferred.append(
                    dict(symbol=symbol, reason="explicit_maintenance_or_longer_source_required")
                )
                continue
            candidates.append((symbol, listing.isoformat()))
    if len(candidates) > (200 if maintenance else 32):
        raise ValueError("Too many missing symbols; select an explicit bounded batch")
    evidence, failed = [], []

    def fetch_candidate(candidate):
        symbol, listing = candidate
        try:
            return fetcher(symbol, listing, target), None
        except Exception as exc:
            return None, dict(symbol=symbol, error=str(exc))

    with ThreadPoolExecutor(max_workers=4) as executor:
        for item, error in executor.map(fetch_candidate, candidates):
            if error:
                failed.append(error)
            else:
                evidence.append(item)
    evidence_path = None
    if evidence:
        from .index_lists import atomic_json

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        evidence_path = root.parent / ".local/reports/factor-bootstrap" / (stamp + "-evidence.json")
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(evidence_path, evidence)
    with pool_lock(root, write=True), stock_connection(root, read_only=False) as conn:
        result = publish_bootstrap(conn, evidence, maintenance=maintenance)
    return dict(
        as_of=target,
        method=METHOD,
        **result,
        failed=failed,
        deferred=deferred,
        evidence_path=str(evidence_path) if evidence_path else None,
    )
