"""Finite read-only checks of the remediated public interfaces."""

import inspect
import json
import time
from pathlib import Path

from aspool import DataPool
from tdxman.codec.bitmap import FieldBit as F
from tdxman.mac.client import MacClient
from tdxman.mac.enums import BoardType, Category

ROOT = Path("/mnt/d/Workstation/Services/tdxman/data")
pool = DataPool(ROOT)
report = {
    "root": str(ROOT),
    "production_writes": False,
    "calls": [],
    "signatures": {
        "members": str(inspect.signature(DataPool.read_board_members)),
        "minutes": str(inspect.signature(MacClient.get_minute_bars)),
    },
}


def call(name, fn):
    tick = time.monotonic()
    item = {"name": name}
    try:
        frame = fn()
        item.update(
            rows=len(frame),
            columns=list(frame.columns),
            attrs=frame.attrs,
            sample=json.loads(frame.head(3).to_json(orient="records", date_format="iso")),
            tail=json.loads(frame.tail(1).to_json(orient="records", date_format="iso")),
        )
        report["calls"].append(item)
        return frame
    except Exception as exc:
        item["error"] = type(exc).__name__ + ": " + str(exc)
        report["calls"].append(item)
    finally:
        item["seconds"] = round(time.monotonic() - tick, 4)
        print(name, item.get("rows"), item.get("error", ""), flush=True)


boards = pool.read_board_daily(start="2026-09-30", end="2026-09-30", kind="concept")
snapshot = boards.loc[boards.board_id == "880710", "snapshot_id"].iloc[0]
call(
    "fixed_snapshot_members",
    lambda: pool.read_board_members(
        snapshot_id=snapshot, kind="concept", board_ids=["880710", "880903"]
    ),
)
call(
    "financial_date_semantics",
    lambda: pool.read_fundamental_reports(
        symbols=["000001.SZ"], start="2026-01-01", end="2026-09-30"
    ),
)
with MacClient.from_best_host(timeout=5, auto_reconnect=False) as client:
    call("GN_directory", lambda: client.get_board_list(BoardType.GN, count=600))
    eighty = call("rank80", lambda: client.get_stock_quotes_list(Category.BOARD_GN, count=80))
    eightyone = call("rank81", lambda: client.get_stock_quotes_list(Category.BOARD_GN, count=81))
    if eighty is not None and eightyone is not None:
        report["rank_prefix_preserved"] = list(eighty.code) == list(eightyone.code)[:80]
    call(
        "GN_minutes_whole_day",
        lambda: client.get_minute_bars(
            1, "880710", date="2026-09-30", asset_type="index", max_pages=2
        ),
    )
    call(
        "GN_minutes_tail",
        lambda: client.get_minute_bars(
            1,
            "880710",
            date="2026-09-30",
            since="2026-09-30T14:45:00",
            page_size=20,
            asset_type="index",
        ),
    )
    for day in [20260930, 20260901]:
        call(
            "historical_chart_" + str(day),
            lambda day=day: client.get_tick_chart(1, "600519", date=day),
        )
    fields = [F.CLOSE, F.TOTAL_SHARES, F.FLOAT_SHARES, F.SERVER_UPDATE_DATE, F.SERVER_UPDATE_TIME]
    call(
        "dated_share_source",
        lambda: client.get_stock_quotes([(0, "000001"), (1, "600519")], fields=fields),
    )
Path("/tmp/tdxman-ca-remediation-live.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n"
)
