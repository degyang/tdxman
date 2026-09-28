"""Measure a complete bounded stock event/amount stream without retaining history."""

import argparse
import json
import resource
import time
from pathlib import Path

from aspool import DataPool


def measure(root, start, end, page_rows):
    started = time.monotonic()
    pool = DataPool(root)
    total = pages = 0
    previous = None
    amount = 0.0
    with pool.iter_limit_events_with_amount(
        start=start,
        end=end,
        fields=["trade_date", "symbol", "amount", "consecutive_up"],
        batch_days=7,
        max_rows=page_rows,
    ) as stream:
        for frame in stream:
            assert len(frame) <= page_rows
            for row in frame.itertuples(index=False):
                key = (row.trade_date.isoformat(), row.symbol)
                assert previous is None or previous < key
                previous = key
            total += len(frame)
            amount += float(frame.amount.sum())
            pages += 1
        assert stream.completed
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if peak > 2 * 1024 * 1024:
        raise RuntimeError("Peak RSS exceeds 2 GiB")
    return dict(
        root=str(Path(root).resolve()),
        start=start,
        end=end,
        rows=total,
        pages=pages,
        max_rows=page_rows,
        amount_sum=amount,
        seconds=round(time.monotonic() - started, 3),
        peak_rss_kib=peak,
        rss_limit_gib=2,
        passed=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--max-rows", type=int, default=25000)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = measure(args.root, args.start, args.end, args.max_rows)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
