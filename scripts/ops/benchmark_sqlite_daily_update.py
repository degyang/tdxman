"""Measure a 500-stock hot update on an isolated recent all-market slice.

The source is opened read-only. The fresh target receives synthetic price
corrections for measurement and must never be promoted as a market dataset.
"""

from __future__ import annotations

import argparse
import json
import resource
import time
from pathlib import Path

from derive_stocks_sqlite import catalog_inputs

from aspool.sqlite_daily_update import apply_daily_changes
from aspool.sqlite_market_summary import recompute_daily_summary
from aspool.sqlite_stock_store import stock_connection


def run(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    if source == target:
        raise ValueError("Benchmark requires a fresh isolated target")
    calendar, _ = catalog_inputs(source)
    with stock_connection(source) as original:
        end = original.execute("SELECT max(trade_date) FROM daily_features").fetchone()[0]
    sessions = sorted(d for d, opened in calendar.items() if opened and d <= end)
    warm_start, window = sessions[-45], sessions[-5:]
    started = time.monotonic()
    with stock_connection(target, create=True, read_only=False) as conn:
        conn.execute(
            "ATTACH DATABASE ? AS original", ((source / "stocks.sqlite").as_uri() + "?mode=ro",)
        )
        # Attached databases otherwise default to a tiny independent cache.
        # These caps apply only to constructing the disposable benchmark slice.
        conn.execute("PRAGMA original.cache_size=-131072")
        conn.execute("PRAGMA main.cache_size=-131072")
        conn.execute("BEGIN")
        for table in ("daily_bars", "daily_features"):
            conn.execute(
                f"INSERT INTO main.{table} SELECT * FROM original.{table} WHERE trade_date>=?",
                (warm_start,),
            )
        conn.execute(
            "INSERT INTO main.corporate_actions SELECT * FROM original.corporate_actions "
            "WHERE record_kind='factor' AND valid_through>=?",
            (warm_start,),
        )
        conn.commit()
        conn.execute("DETACH DATABASE original")
        conn.execute("PRAGMA main.cache_size=-32768")
        conn.execute("BEGIN IMMEDIATE")
        for day in window:
            recompute_daily_summary(conn, trade_date=day)
        conn.commit()
        copy_seconds = time.monotonic() - started
        symbols = [
            r[0]
            for r in conn.execute(
                "SELECT symbol FROM daily_features WHERE trade_date=? AND calc_status='TRADED' "
                "ORDER BY symbol LIMIT 500",
                (end,),
            )
        ]
        updates = []
        for symbol in symbols:
            for day, close, high in conn.execute(
                "SELECT b.trade_date,b.close,b.high FROM daily_bars b JOIN daily_features f "
                "USING(symbol,trade_date) WHERE b.symbol=? AND b.trade_date BETWEEN ? AND ? "
                "AND f.calc_status='TRADED'",
                (symbol, window[0], window[-1]),
            ):
                corrected = round(close + 0.01, 2)
                updates.append(
                    dict(symbol=symbol, trade_date=day, close=corrected, high=max(high, corrected))
                )
        result = apply_daily_changes(conn, bars=updates, market_sessions=sessions)
        repeat = apply_daily_changes(conn, bars=updates, market_sessions=sessions)
        assert (
            repeat["changed_rows"]
            == repeat["recomputed_feature_rows"]
            == repeat["summary_rows"]
            == 0
        )
        assert set(result["affected_sessions"]) <= set(window)
        assert result["summary_rows"] == 2 * len(result["affected_sessions"])
        return dict(
            source=str(source),
            target=str(target),
            synthetic_target=True,
            symbols=len(symbols),
            input_rows=len(updates),
            window=window,
            copy_seconds=round(copy_seconds, 3),
            update=result,
            repeat=repeat,
            max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.source, args.target)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False))
