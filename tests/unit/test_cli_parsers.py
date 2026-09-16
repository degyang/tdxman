"""CLI parameter parser tests."""

from tdxman.cli.parsers import parse_board_type
from tdxman.mac.enums import BoardType


def test_parse_board_type_supports_second_level_industry() -> None:
    assert parse_board_type("HY2") is BoardType.HY2
    assert parse_board_type("industry2") is BoardType.HY2
