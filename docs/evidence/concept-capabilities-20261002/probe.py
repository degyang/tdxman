"""Bounded read-only CA capability probe; no collection or database updates."""

import argparse
import inspect
import json
import sqlite3
import time
from importlib.metadata import version
from pathlib import Path

from aspool import DataPool
from aspool.pool import pool_lock
from tdxman.client import TdxClient
from tdxman.mac.client import MacClient

STOCKS = [
    "000001.SZ",
    "600519.SH",
    "000858.SZ",
    "600036.SH",
    "002304.SZ",
    "300750.SZ",
    "688981.SH",
    "600900.SH",
]
SIGNATURES = {
    "DataPool": [
        "read_board_daily",
        "read_daily",
        "read_research_daily",
        "read_index_daily",
        "read_security_info",
        "read_security_daily",
        "read_limit_events",
        "read_fundamental_reports",
        "read_trading_calendar",
    ],
    "MacClient": [
        "get_board_list",
        "get_board_members",
        "get_stock_quotes",
        "get_stock_quotes_list",
        "get_stock_kline",
        "get_tick_chart",
    ],
    "TdxClient": ["get_index_bars", "get_security_bars", "get_history_minute_time_data"],
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--online", action="store_true")
    args = parser.parse_args()
    report = {
        "version": version("tdxman"),
        "source_paths": {},
        "signatures": {},
        "calls": [],
        "root": str(args.root.resolve()),
        "online": args.online,
        "constraints": "8 stocks; 2 GN; 30 sessions; at most 22 online SDK calls; "
        "source discovery and transport reconnect are separate costs",
    }
    for name, cls in [("DataPool", DataPool), ("MacClient", MacClient), ("TdxClient", TdxClient)]:
        report["source_paths"][name] = inspect.getfile(cls)
        report["signatures"][name] = {
            n: str(inspect.signature(getattr(cls, n))) for n in SIGNATURES[name] if hasattr(cls, n)
        }

    def call(name, fn, *, network=False, budget=None):
        tick = time.monotonic()
        item = {
            "name": name,
            "network": network,
            "public_sdk_calls": 1,
            "protocol_dispatches": None,
        }
        before = budget[0] if budget else None
        try:
            frame = fn()
            item.update(
                rows=len(frame),
                columns=list(frame.columns),
                attrs=frame.attrs,
                valid_counts=frame.notna().sum().to_dict(),
                sample=json.loads(frame.head(8).to_json(orient="records", date_format="iso")),
            )
            for field in ["date", "trade_date", "datetime"]:
                if field in frame and len(frame):
                    item[field + "_range"] = [str(frame[field].min()), str(frame[field].max())]
            if "datetime" in frame:
                item["per_day"] = frame["datetime"].astype(str).str[:10].value_counts().to_dict()
            if name.startswith("gn_matrix"):
                item["per_day"] = {str(k): int(v) for k, v in frame.groupby("date").size().items()}
                item["unique_boards"] = int(frame["board_id"].nunique())
                item["snapshot_ids"] = sorted(frame["snapshot_id"].dropna().unique().tolist())
            if name.startswith("members"):
                item["identities"] = sorted(
                    {
                        f"{r['code']}.{ {1: 'SH', 0: 'SZ', 2: 'BJ'}[r['market']] }"
                        for r in frame.to_dict("records")
                    }
                )
                item["duplicates"] = len(frame) - len(item["identities"])
                item["cap_reached"] = len(frame) >= 320
            report["calls"].append(item)
            return frame
        except Exception as exc:
            item.update(error=type(exc).__name__ + ": " + str(exc))
            report["calls"].append(item)
        finally:
            item["elapsed_seconds"] = round(time.monotonic() - tick, 4)
            if budget:
                item["protocol_dispatches"] = budget[0] - before
            print(name, item.get("rows"), item.get("error", ""), flush=True)

    pool = DataPool(args.root)
    with pool_lock(args.root):
        cal = call(
            "calendar", lambda: pool.read_trading_calendar(start="2026-08-01", end="2026-09-30")
        )
        sessions = cal.loc[cal["is_open"], "trade_date"].astype(str).tolist()
        for n in (7, 30):
            call(
                f"gn_matrix_{n}",
                lambda n=n: pool.read_board_daily(
                    start=sessions[-n], end="2026-09-30", kind="concept"
                ),
            )
        call(
            "stock_research_8",
            lambda: pool.read_research_daily(symbols=STOCKS, start="2026-09-30", end="2026-09-30"),
        )
        for adjust in ("none", "qfq"):
            call(
                "stock_" + adjust,
                lambda adjust=adjust: pool.read_daily(
                    symbols=STOCKS, start="2026-08-01", end="2026-09-30", adjust=adjust
                ),
            )
        call(
            "stock_status_8",
            lambda: pool.read_security_daily(symbols=STOCKS, start="2026-09-30", end="2026-09-30"),
        )
        call(
            "stock_limits_8",
            lambda: pool.read_limit_events(symbols=STOCKS, trade_date="2026-09-30"),
        )
        call("stock_directory_8", lambda: pool.read_security_info(symbols=STOCKS))
        call(
            "financial_reports_8",
            lambda: pool.read_fundamental_reports(
                symbols=STOCKS, start="2025-09-30", end="2026-09-30"
            ),
        )
        indices = ["000001.SH", "399001.SZ", "399006.SZ", "000680.SH", "880008.SH"]
        call(
            "index_daily_5",
            lambda: pool.read_index_daily(symbols=indices, start="2026-09-21", end="2026-09-30"),
        )
        # Internal diagnostics are explicitly distinct from a public reader capability.
        with sqlite3.connect(
            (args.root / "features.sqlite").resolve().as_uri() + "?mode=ro", uri=True
        ) as c:
            rows = c.execute(
                "SELECT s.snapshot_id,s.kind,s.board_id,s.board_name,s.members_json "
                "FROM board_snapshots s WHERE kind=? AND board_id IN (?,?) LIMIT 4",
                ("concept", "880710", "880706"),
            ).fetchall()
            report["internal_membership_diagnostic"] = [
                dict(
                    snapshot_id=r[0], kind=r[1], board_id=r[2], name=r[3], members=json.loads(r[4])
                )
                for r in rows
            ]
        with sqlite3.connect(
            (args.root / "stocks.sqlite").resolve().as_uri() + "?mode=ro", uri=True
        ) as c:
            marks = ",".join("?" for _ in STOCKS)
            report["internal_mv_source_diagnostic"] = c.execute(
                "SELECT symbol,ohlcv_source,total_share,float_share,total_mv,float_mv,"
                "total_share_source,float_share_source,total_mv_source,float_mv_source "
                "FROM daily_bars WHERE trade_date=? "
                f"AND symbol IN ({marks})",
                ["2026-09-30", *STOCKS],
            ).fetchall()
    if args.online:
        from tdxman.codec.bitmap import FieldBit as F
        from tdxman.models.enums import KlineCategory as K
        from tdxman.models.enums import Market

        def meter(client):
            budget = [0]
            original = client._execute

            def execute(command):
                budget[0] += 1
                if budget[0] > 30:
                    raise ValueError("Protocol dispatch budget exceeded")
                return original(command)

            client._execute = execute
            return budget

        tick = time.monotonic()
        try:
            with MacClient.from_best_host(timeout=5, auto_reconnect=False) as c:
                report["mac_discovery_seconds"] = round(time.monotonic() - tick, 4)
                budget = meter(c)
                frames = [
                    call(
                        "members_" + code,
                        lambda code=code: c.get_board_members(code, count=320),
                        network=True,
                        budget=budget,
                    )
                    for code in ["880710", "880706"]
                ]
                if all(f is not None for f in frames):
                    sets = [
                        {(int(r["market"]), str(r["code"])) for r in f.to_dict("records")}
                        for f in frames
                    ]
                    report["member_overlap"] = {
                        "union": len(sets[0] | sets[1]),
                        "overlap": len(sets[0] & sets[1]),
                    }
                    ids = sorted(sets[0] | sets[1])
                    if len(ids) <= 80:
                        call(
                            "member_union_quotes",
                            lambda: c.get_stock_quotes(
                                ids,
                                fields=[
                                    F.CLOSE,
                                    F.PRE_CLOSE,
                                    F.AMOUNT,
                                    F.SERVER_UPDATE_DATE,
                                    F.SERVER_UPDATE_TIME,
                                ],
                            ),
                            network=True,
                            budget=budget,
                        )
        except Exception as exc:
            report["mac_connection_error"] = str(exc)
        tick = time.monotonic()
        try:
            with TdxClient.from_best_host(timeout=5) as c:
                report["standard_discovery_seconds"] = round(time.monotonic() - tick, 4)
                budget = meter(c)
                for market, code in [
                    (Market.SH, "000001"),
                    (Market.SZ, "399001"),
                    (Market.SZ, "399006"),
                    (Market.SH, "000680"),
                    (Market.SH, "880008"),
                ]:
                    call(
                        code + "_minute",
                        lambda market=market, code=code: c.get_index_bars(
                            market, code, K.MIN_1, start=0, count=240
                        ),
                        network=True,
                        budget=budget,
                    )
                    call(
                        code + "_previous_daily",
                        lambda market=market, code=code: c.get_index_bars(
                            market, code, K.DAY, start=0, count=2
                        ),
                        network=True,
                        budget=budget,
                    )
                call(
                    "gn_historical_minute_window",
                    lambda: c.get_index_bars(Market.SH, "880710", K.MIN_1, start=0, count=800),
                    network=True,
                    budget=budget,
                )
                for day in [20260930, 20260901]:
                    call(
                        "stock_historical_minute_" + str(day),
                        lambda day=day: c.get_history_minute_time_data(Market.SH, "600519", day),
                        network=True,
                        budget=budget,
                    )
        except Exception as exc:
            report["standard_connection_error"] = str(exc)
            print("standard_connection_error", str(exc), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n")


if __name__ == "__main__":
    main()
