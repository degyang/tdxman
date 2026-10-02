"""Bounded alternate-source collection for securities missing from MAC history."""

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

from .qfq_audit import save, stage_connection
from .sqlite_qfq import PRICES, valid_prices


def collect_fallback(root, out, start, end):
    import baostock as bs

    with stage_connection(out) as stage:
        candidates = [
            r[0]
            for r in stage.execute(
                "SELECT symbol FROM collection WHERE start=? AND end=? AND status!='collected'",
                (start, end),
            )
        ]
    results = []
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(login.error_msg)
    try:
        basic = bs.query_stock_basic()
        listing_rows = []
        while basic.error_code == "0" and basic.next():
            listing_rows.append(dict(zip(basic.fields, basic.get_row_data())))
        if basic.error_code != "0":
            raise RuntimeError("Listing dates: " + basic.error_msg)
        save(out / "listing-source.json", listing_rows)
        with sqlite3.connect((root / "stocks.sqlite").as_uri() + "?mode=ro", uri=True) as local:
            for symbol in candidates:
                count = local.execute(
                    "SELECT count(*) FROM daily_bars WHERE symbol=? AND trade_date BETWEEN ? AND ?",
                    (symbol, start, end),
                ).fetchone()[0]
                if not count or symbol.endswith(".BJ"):
                    results.append(
                        dict(
                            symbol=symbol,
                            local_rows=count,
                            status="not_supported" if count else "outside_window",
                        )
                    )
                    continue
                code, market = symbol.split(".")
                maps = []
                for flag in ("3", "2"):
                    query = bs.query_history_k_data_plus(
                        market.lower() + "." + code,
                        "date,open,high,low,close,preclose,volume,amount,tradestatus,isST",
                        start_date=(date.fromisoformat(start) - timedelta(days=45)).isoformat(),
                        end_date=end,
                        frequency="d",
                        adjustflag=flag,
                    )
                    rows = {}
                    while query.error_code == "0" and query.next():
                        r = dict(zip(query.fields, query.get_row_data()))
                        rows[r["date"]] = {
                            k: float(v) if v else None for k, v in r.items() if k != "date"
                        }
                    if query.error_code != "0":
                        raise RuntimeError(f"{symbol}: {query.error_code} {query.error_msg}")
                    maps.append(rows)
                raw, qfq = maps
                shared = sorted(set(raw) & set(qfq))
                days = [d for d in shared if start <= d <= end]
                before = [d for d in shared if d < start]
                days = before[-1:] + days
                observations = []
                for day in days:
                    r, q = raw[day], qfq[day]
                    if not valid_prices(r, positive=True) or not valid_prices(q):
                        continue
                    observations.append(
                        dict(
                            symbol=symbol,
                            trade_date=day,
                            raw={
                                **{k: r[k] for k in PRICES},
                                "vol": r["volume"],
                                "amount": r["amount"],
                            },
                            qfq={k: q[k] for k in PRICES},
                            source="baostock:history",
                            source_as_of=datetime.now(timezone.utc).date().isoformat(),
                            facts=dict(
                                pre_close=r["preclose"],
                                is_st=r["isST"],
                                trading_status="SUSPENDED" if r["tradestatus"] == 0 else "TRADING",
                            ),
                        )
                    )
                with stage_connection(out) as stage:
                    stage.executemany(
                        "INSERT OR REPLACE INTO observations VALUES (?,?,?)",
                        [
                            (symbol, r["trade_date"], json.dumps(r, allow_nan=False))
                            for r in observations
                        ],
                    )
                result = dict(
                    symbol=symbol,
                    local_rows=count,
                    paired_rows=len(observations),
                    status="collected" if observations else "source_unavailable",
                )
                results.append(result)
                save(out / "fallback-report.json", results)
                print(json.dumps(result), flush=True)
    finally:
        bs.logout()
    save(out / "fallback-report.json", results)
    return results


def collect_gaps(root, out, start, end):
    """Fetch explicit missing base dates, including source-confirmed suspensions."""
    from collections import defaultdict

    import baostock as bs

    with sqlite3.connect((root / "stocks.sqlite").as_uri() + "?mode=ro", uri=True) as conn:
        conn.execute(
            "ATTACH DATABASE ? AS features", ((root / "features.sqlite").as_uri() + "?mode=ro",)
        )
        gaps = conn.execute(
            "SELECT symbol,trade_date FROM features.stock_daily_features "
            "WHERE trade_date BETWEEN ? AND ? "
            "EXCEPT SELECT symbol,trade_date FROM daily_bars WHERE trade_date BETWEEN ? AND ?",
            (start, end, start, end),
        ).fetchall()
    by_symbol = defaultdict(list)
    with stage_connection(out) as stage:
        for symbol, day in gaps:
            if not stage.execute(
                "SELECT 1 FROM observations WHERE symbol=? AND trade_date=?", (symbol, day)
            ).fetchone():
                by_symbol[symbol].append(day)
    save(out / "gap-inventory.json", dict(by_symbol))
    results = []
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(login.error_msg)
    try:
        for symbol, days in by_symbol.items():
            if symbol.endswith(".BJ"):
                results.append(dict(symbol=symbol, status="source_unsupported", missing_days=days))
                continue
            code, market = symbol.split(".")
            maps = []
            for flag in ("3", "2"):
                query = bs.query_history_k_data_plus(
                    market.lower() + "." + code,
                    "date,open,high,low,close,preclose,volume,amount,tradestatus,isST",
                    start_date=min(days),
                    end_date=max(days),
                    frequency="d",
                    adjustflag=flag,
                )
                mapping = {}
                while query.error_code == "0" and query.next():
                    row = dict(zip(query.fields, query.get_row_data()))
                    if row["date"] in days:
                        mapping[row["date"]] = {
                            k: float(v) if v else None for k, v in row.items() if k != "date"
                        }
                if query.error_code != "0":
                    raise RuntimeError(f"{symbol}: {query.error_code} {query.error_msg}")
                maps.append(mapping)
            observations = []
            for day in sorted(set(maps[0]) & set(maps[1])):
                r, q = maps[0][day], maps[1][day]
                if not valid_prices(r, positive=True) or not valid_prices(q):
                    continue
                observations.append(
                    dict(
                        symbol=symbol,
                        trade_date=day,
                        raw={
                            **{k: r[k] for k in PRICES},
                            "vol": r["volume"],
                            "amount": r["amount"],
                        },
                        qfq={k: q[k] for k in PRICES},
                        source="baostock:history",
                        source_as_of=datetime.now(timezone.utc).date().isoformat(),
                        facts=dict(
                            pre_close=r["preclose"],
                            is_st=r["isST"],
                            trading_status="SUSPENDED" if r["tradestatus"] == 0 else "TRADING",
                        ),
                    )
                )
            with stage_connection(out) as stage:
                stage.executemany(
                    "INSERT OR IGNORE INTO observations VALUES (?,?,?)",
                    [
                        (symbol, r["trade_date"], json.dumps(r, allow_nan=False))
                        for r in observations
                    ],
                )
            result = dict(
                symbol=symbol,
                requested=len(days),
                received=len(observations),
                missing_days=sorted(set(days) - {r["trade_date"] for r in observations}),
            )
            results.append(result)
            save(out / "gap-source-report.json", results)
            print(json.dumps(result), flush=True)
    finally:
        bs.logout()
    save(out / "gap-source-report.json", results)
    return results
