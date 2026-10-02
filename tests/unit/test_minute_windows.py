"""Minute windows stay bounded and retain source dates instead of inventing coverage."""

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from tdxman.exceptions import TdxDecodeError
from tdxman.mac.client import AsyncMacClient, MacClient, _tick_frame
from tdxman.mac.commands.symbol_tick_chart import SymbolTickChartCmd


def bars(times):
    return pd.DataFrame(
        [
            dict(
                datetime=datetime.fromisoformat(t),
                open=10,
                high=11,
                low=9,
                close=10,
                amount=100,
                vol=10,
                float_shares=999,
            )
            for t in times
        ]
    )


@pytest.mark.parametrize("asynchronous", [False, True])
def test_tail_includes_overlap_without_polling_full_day(asynchronous):
    client = object.__new__(AsyncMacClient if asynchronous else MacClient)
    frame = bars(["2026-09-30T14:58:00", "2026-09-30T14:59:00", "2026-09-30T15:00:00"])
    client.get_stock_kline = (
        AsyncMock(return_value=frame) if asynchronous else Mock(return_value=frame)
    )
    result = client.get_minute_bars(
        1, "880710", date="2026-09-30", since="2026-09-30T14:59:00", page_size=3, asset_type="index"
    )
    result = asyncio.run(result) if asynchronous else result
    assert len(result) == 2 and result.attrs["protocol_requests"] == 1
    assert result.attrs["window_start_reached"]
    assert "vol" not in result and "float_shares" not in result
    assert client.get_stock_kline.call_args.kwargs["count"] == 3
    assert result.attrs["finality"] == "not_provided"


@pytest.mark.parametrize("asynchronous", [False, True])
def test_old_requested_day_reports_budget_not_fake_missing_session(asynchronous):
    client = object.__new__(AsyncMacClient if asynchronous else MacClient)
    frames = [
        bars(["2026-09-30T14:59:00", "2026-09-30T15:00:00"]),
        bars(["2026-09-30T14:57:00", "2026-09-30T14:58:00"]),
    ]
    client.get_stock_kline = (
        AsyncMock(side_effect=frames) if asynchronous else Mock(side_effect=frames)
    )
    result = client.get_minute_bars(1, "600519", date="2026-09-01", page_size=2, max_pages=2)
    result = asyncio.run(result) if asynchronous else result
    assert result.empty and result.attrs["budget_exhausted"]
    assert result.attrs["availability"] == "not_returned"
    assert not result.attrs["window_start_reached"]
    assert result.attrs["source_oldest"] == "2026-09-30 14:57:00"


def test_duplicate_shifted_pages_reject_incomplete_history():
    client = object.__new__(MacClient)
    frame = bars(["2026-09-30T14:59:00", "2026-09-30T15:00:00"])
    client.get_stock_kline = Mock(side_effect=[frame, frame])
    with pytest.raises(TdxDecodeError, match="repeated or shifted"):
        client.get_minute_bars(1, "600519", date="2026-09-29", page_size=2)


def test_historical_chart_uses_requested_day_header_not_latest_tail_reference():
    fixture = Path(__file__).parents[1] / "fixtures" / "mac_historical_tick_chart.hex"
    body = bytes.fromhex(fixture.read_text().splitlines()[1])
    chart = SymbolTickChartCmd(1, "600519").parse_response(bytes(body))
    frame = _tick_frame(chart, datetime(2026, 9, 1).date())
    assert chart.pre_close == 90
    assert frame["pre_close"].iloc[0] == 10
    assert str(frame["datetime"].iloc[0]) == "2026-09-01 09:31:00"
    assert frame.attrs["date_verified"]
    with pytest.raises(TdxDecodeError, match="different"):
        _tick_frame(chart, datetime(2026, 9, 30).date())
