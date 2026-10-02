"""Bounded MAC minute fallback and overlapping GN sample after standard host failure."""

import json
import time
from pathlib import Path

from tdxman.mac.client import MacClient
from tdxman.mac.enums import Period

report = json.loads(Path("/tmp/tdxman-ca-probe.json").read_text())
with MacClient.from_best_host(timeout=5, auto_reconnect=False) as client:
    dispatches = [0]
    original = client._execute

    def execute(command):
        dispatches[0] += 1
        if dispatches[0] > 20:
            raise ValueError("Supplement protocol budget exceeded")
        return original(command)

    client._execute = execute

    def call(name, fn):
        tick, before = time.monotonic(), dispatches[0]
        item = dict(name=name, network=True, public_sdk_calls=1, route="MAC bounded fallback")
        try:
            frame = fn()
            item.update(
                rows=len(frame),
                columns=list(frame.columns),
                sample=json.loads(frame.head(3).to_json(orient="records", date_format="iso")),
                tail=json.loads(frame.tail(2).to_json(orient="records", date_format="iso")),
            )
            if "datetime" in frame and len(frame):
                item["date_range"] = [str(frame["datetime"].min()), str(frame["datetime"].max())]
                item["per_day"] = frame["datetime"].astype(str).str[:10].value_counts().to_dict()
            if name == "members_880903_overlap":
                identities = {
                    f"{r['code']}.{ {1: 'SH', 0: 'SZ', 2: 'BJ'}[r['market']] }"
                    for r in frame.to_dict("records")
                }
                prior = next(r for r in report["calls"] if r["name"] == "members_880710")
                base = set(prior["identities"])
                item["identities"] = sorted(identities)
                report["overlap_pair"] = dict(
                    boards=["880710", "880903"],
                    counts=[len(base), len(identities)],
                    union=len(base | identities),
                    overlap=sorted(base & identities),
                    quote_union_batch_estimate=(len(base | identities) + 79) // 80,
                )
        except Exception as exc:
            item["error"] = type(exc).__name__ + ": " + str(exc)
        finally:
            item["elapsed_seconds"] = round(time.monotonic() - tick, 4)
            item["protocol_dispatches"] = dispatches[0] - before
            report["calls"].append(item)
            print(name, item.get("rows"), item.get("error", ""), flush=True)

    call("members_880903_overlap", lambda: client.get_board_members("880903", count=320))
    for market, code in [(1, "000001"), (0, "399001"), (0, "399006"), (1, "000680"), (1, "880008")]:
        call(
            code + "_mac_minute",
            lambda market=market, code=code: client.get_stock_kline(
                market, code, Period.MIN_1, start=0, count=240
            ),
        )
        call(
            code + "_mac_daily",
            lambda market=market, code=code: client.get_stock_kline(
                market, code, Period.DAILY, start=0, count=2
            ),
        )
    call(
        "gn_mac_historical_window",
        lambda: client.get_stock_kline(1, "880710", Period.MIN_1, start=0, count=800),
    )
    for day in [20260930, 20260901]:
        call(
            "stock_mac_chart_" + str(day),
            lambda day=day: client.get_tick_chart(1, "600519", date=day),
        )
Path("/tmp/tdxman-ca-probe.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n"
)
