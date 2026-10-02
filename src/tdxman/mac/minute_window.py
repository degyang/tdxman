"""Finite minute windows shared by synchronous and asynchronous public clients."""

from datetime import date as Date
from datetime import datetime

import pandas as pd

from ..exceptions import TdxDecodeError


def arguments(day, since, max_pages, page_size, asset_type):
    if isinstance(day, int):
        day = Date(day // 10000, day % 10000 // 100, day % 100)
    elif isinstance(day, str):
        day = Date.fromisoformat(day)
    if (
        type(day) is not Date
        or type(max_pages) is not int
        or not 1 <= max_pages <= 16
        or type(page_size) is not int
        or not 1 <= page_size <= 700
        or asset_type not in {"stock", "index", "etf"}
    ):
        raise ValueError("Expected a date, asset_type and max_pages=1..16/page_size=1..700")
    floor = datetime.combine(day, datetime.min.time())
    if since is not None:
        floor = datetime.fromisoformat(since) if isinstance(since, str) else since
        if not isinstance(floor, datetime) or floor.tzinfo is not None or floor.date() != day:
            raise ValueError("since must be a Shanghai wall-clock timestamp on the requested date")
    return day, floor


def consume(pages, frame, floor, page_size):
    if len(frame) > page_size:
        raise TdxDecodeError("Minute source returned more rows than requested")
    if frame.empty:
        return True
    if "datetime" not in frame:
        raise TdxDecodeError("Minute response has no timestamp")
    times = pd.to_datetime(frame["datetime"])
    if times.isna().any() or times.duplicated().any():
        raise TdxDecodeError("Invalid or repeated minute timestamps")
    if pages:
        old_min = min(pd.to_datetime(p["datetime"]).min() for p in pages)
        if times.max() >= old_min:
            raise TdxDecodeError("Minute pagination repeated or shifted a timestamp")
    pages.append(frame)
    return times.min().to_pydatetime() <= floor or len(frame) < page_size


def finish(pages, day, floor, calls, page_size, stopped, asset_type):
    fields = ["datetime", "open", "high", "low", "close", "amount"]
    if asset_type != "index":
        fields.append("vol")
    frame = pd.concat(pages, ignore_index=True) if pages else pd.DataFrame(columns=fields)
    if not frame.empty:
        times = pd.to_datetime(frame["datetime"])
        frame = frame.loc[(times >= floor) & (times.dt.date == day)].sort_values("datetime").copy()
    frame = frame.reindex(columns=fields)
    all_times = pd.concat([p["datetime"] for p in pages], ignore_index=True) if pages else None
    reached = bool(pages and pd.to_datetime(all_times).min().to_pydatetime() <= floor)
    frame.attrs.update(
        requested_date=day.isoformat(),
        requested_since=floor.isoformat(),
        source_oldest=str(pd.to_datetime(all_times).min()) if pages else None,
        source_latest=str(pd.to_datetime(all_times).max()) if pages else None,
        actual_start=str(frame["datetime"].min()) if len(frame) else None,
        actual_end=str(frame["datetime"].max()) if len(frame) else None,
        returned_rows=len(frame),
        protocol_requests=calls,
        request_count_basis="page_dispatches_excluding_transport_retries",
        page_size=page_size,
        window_start_reached=reached,
        budget_exhausted=not stopped,
        minute_continuity="not_validated",
        finality="not_provided",
        availability="available" if len(frame) else "not_returned",
        timezone="Asia/Shanghai",
        revision_policy="latest_source_values",
        price_unit="point" if asset_type == "index" else "CNY/share",
        amount_unit="CNY",
        volume_unit="not_provided" if asset_type == "index" else "share",
        asset_type=asset_type,
        source_consistency="best_effort",
    )
    return frame
