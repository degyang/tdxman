"""Preserve source rank and finite budgets across both public clients."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from tdxman.exceptions import TdxDecodeError
from tdxman.mac.client import AsyncMacClient, MacClient
from tdxman.mac.enums import BoardType, Category
from tdxman.mac.models import BoardInfo, MacQuoteField


def client_and_call(asynchronous, batches, method, count):
    client = object.__new__(AsyncMacClient if asynchronous else MacClient)
    client._execute = AsyncMock(side_effect=batches) if asynchronous else Mock(side_effect=batches)
    kwargs = dict(count=count)
    if method == "get_stock_quotes_list":
        args = [Category.BOARD_GN]
    elif method == "get_board_members":
        args = ["880710"]
    else:
        args = [BoardType.GN]
    result = getattr(client, method)(*args, **kwargs)
    return client, asyncio.run(result) if asynchronous else result


def quotes(start, count):
    return [
        MacQuoteField(1, f"{i:06}", str(i), {"close": float(i + 1)})
        for i in range(start, start + count)
    ]


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("method", ["get_stock_quotes_list", "get_board_members"])
@pytest.mark.parametrize("count", [1, 80, 81])
def test_exact_quota_preserves_rank(asynchronous, method, count):
    batches = [quotes(0, min(count, 80))] + ([quotes(80, 1)] if count == 81 else [])
    client, frame = client_and_call(asynchronous, batches, method, count)
    assert list(frame["code"]) == [f"{i:06}" for i in range(count)]
    assert len(frame) == count
    assert client._execute.call_count == len(batches)
    assert frame.attrs["next_offset"] == count
    assert not frame.attrs["source_exhausted"]
    if count == 81:
        assert client._execute.call_args_list[-1].args[0]._page_size == 1


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("method", ["get_stock_quotes_list", "get_board_members"])
def test_short_page_and_duplicates_fail_closed(asynchronous, method):
    _, frame = client_and_call(asynchronous, [quotes(0, 3)], method, 100)
    assert frame.attrs["complete"] and frame.attrs["source_exhausted"]
    assert frame.attrs["next_offset"] is None
    with pytest.raises(TdxDecodeError, match="repeated"):
        client_and_call(asynchronous, [quotes(0, 80), quotes(79, 1)], method, 81)
    with pytest.raises(TdxDecodeError, match="more"):
        client_and_call(asynchronous, [quotes(0, 80), quotes(80, 2)], method, 81)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_empty_page_failure_and_invalid_budget(asynchronous):
    _, frame = client_and_call(asynchronous, [[]], "get_stock_quotes_list", 80)
    assert frame.empty and frame.attrs["source_exhausted"]
    with pytest.raises(OSError):
        client_and_call(asynchronous, [OSError("source failed")], "get_stock_quotes_list", 80)
    with pytest.raises(ValueError):
        client_and_call(asynchronous, [], "get_stock_quotes_list", 0)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_directory_public_total_and_changed_source(asynchronous):
    client = object.__new__(AsyncMacClient if asynchronous else MacClient)
    sequence = [0]

    def execute(command):
        number = sequence[0]
        sequence[0] += 1
        command.total = 151 + number
        count = 150 if number == 0 else 1
        return [
            BoardInfo(1, f"{i:06}", str(i), 1, 0, 1, 1, "600000", "stock", 1, 0, 1)
            for i in range(number * 150, number * 150 + count)
        ]

    client._execute = AsyncMock(side_effect=execute) if asynchronous else Mock(side_effect=execute)
    result = client.get_board_list(BoardType.GN, count=200)
    frame = asyncio.run(result) if asynchronous else result
    assert len(frame) == 151
    assert frame.attrs["source_total"] == 152
    assert frame.attrs["source_total_changed"]
    assert not frame.attrs["complete"]


@pytest.mark.parametrize("asynchronous", [False, True])
def test_explicit_quotes_report_missing_identity_fields_and_valid_zero(asynchronous):
    from tdxman.codec.bitmap import FieldBit as F

    client = object.__new__(AsyncMacClient if asynchronous else MacClient)
    response = [MacQuoteField(1, "600519", "stock", {"close": 0})]
    client._execute = (
        AsyncMock(return_value=response) if asynchronous else Mock(return_value=response)
    )
    result = client.get_stock_quotes(
        [(1, "600519"), (0, "000001")], fields=[F.CLOSE, F.SERVER_UPDATE_DATE]
    )
    frame = asyncio.run(result) if asynchronous else result
    assert frame.close.iloc[0] == 0
    assert not frame.attrs["complete"]
    assert frame.attrs["missing_identities"] == [dict(market=0, code="000001")]
    assert frame.attrs["missing_fields"] == ["server_update_date"]
