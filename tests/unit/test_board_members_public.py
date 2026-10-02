"""Public immutable member reads with pagination and strict source identity."""

import pytest

from aspool import DataPool
from aspool.api_contract import DataPoolError
from aspool.sqlite_board_daily import publish_board_snapshot
from aspool.sqlite_six_dimension import upgrade_six_dimension_schema
from aspool.sqlite_stock_store import stock_connection
from tests.unit.test_storage_single_authority import canonical  # noqa: F401


def test_snapshot_members_remain_fixed_across_current_changes(canonical):  # noqa: F811
    upgrade_six_dimension_schema(canonical)
    with stock_connection(canonical, read_only=False) as conn:
        identity = publish_board_snapshot(
            conn,
            kind="concept",
            as_of="2026-10-01",
            boards=[
                dict(board_id="880710", board_name="seed", members=["000001.SZ", "600519.SH"]),
                dict(board_id="880903", board_name="fish", members=["000001.SZ"]),
            ],
        )
    pool = DataPool(canonical)
    all_rows = pool.read_board_members(
        snapshot_id=identity, kind="concept", board_ids=["880710", "880903"]
    )
    assert len(all_rows) == 3 and all_rows.attrs["total_members"] == 3
    assert all_rows.attrs["complete"] and not all_rows.attrs["point_in_time"]
    first = pool.read_board_members(
        snapshot_id=identity, kind="concept", board_ids=["880710", "880903"], limit=2
    )
    second = pool.read_board_members(
        snapshot_id=identity,
        kind="concept",
        board_ids=["880710", "880903"],
        limit=2,
        offset=first.attrs["next_offset"],
    )
    assert len(first) == 2 and len(second) == 1
    assert second.attrs["next_offset"] is None
    with stock_connection(canonical, read_only=False) as conn:
        newer = publish_board_snapshot(
            conn,
            kind="concept",
            as_of="2026-10-02",
            boards=[
                dict(board_id="880710", board_name="seed", members=["000001.SZ"]),
            ],
        )
    assert newer != identity
    again = pool.read_board_members(
        snapshot_id=identity, kind="concept", board_ids=["880710", "880903"]
    )
    assert again.to_dict("records") == all_rows.to_dict("records")
    with pytest.raises(DataPoolError) as exc:
        pool.read_board_members(snapshot_id=identity, kind="concept", board_ids=["880999"])
    assert exc.value.code == "BOARD_NOT_FOUND"
    with pytest.raises(DataPoolError) as exc:
        pool.read_board_members(snapshot_id="unknown", kind="concept", board_ids=["880710"])
    assert exc.value.code == "SNAPSHOT_NOT_FOUND"


@pytest.mark.parametrize(
    "args",
    [
        dict(snapshot_id="", kind="concept", board_ids=["880710"]),
        dict(snapshot_id="test", kind="GN", board_ids=["880710"]),
        dict(snapshot_id="test", kind="concept", board_ids=[]),
        dict(snapshot_id="test", kind="concept", board_ids=["880710", "880710"]),
        dict(snapshot_id="test", kind="concept", board_ids=["880710"], limit=0),
    ],
)
def test_invalid_membership_scope_is_not_partial_success(canonical, args):  # noqa: F811
    with pytest.raises(DataPoolError) as exc:
        DataPool(canonical).read_board_members(**args)
    assert exc.value.code == "INVALID_ARGUMENT"


def test_refresh_rejects_incomplete_source_directory():
    from unittest.mock import Mock

    import pandas as pd

    from aspool.sqlite_board_daily import fetch_board_category

    listing = pd.DataFrame([dict(code="880710", name="board")])
    listing.attrs["complete"] = False
    client = Mock()
    client.get_board_list.return_value = listing
    with pytest.raises(ValueError, match="Incomplete or changing"):
        fetch_board_category(client, "concept")
    client.get_board_members.assert_not_called()
