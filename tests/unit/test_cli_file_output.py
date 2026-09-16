from __future__ import annotations

import pandas as pd
import pytest
from click import UsageError

from tdxman.cli.output import print_output


def test_writes_json_to_explicit_file(tmp_path) -> None:
    target = tmp_path / "006324.json"

    print_output(pd.DataFrame([{"code": "006324", "price": 12.346}]), "json", target)

    assert target.read_text(encoding="utf-8") == '[{"code":"006324","price":12.35}]'


def test_directory_uses_symbol_filename_and_markdown_table(tmp_path) -> None:
    print_output(
        pd.DataFrame([{"date": "2026-09-16", "close": 12.346}]),
        "table",
        tmp_path / "data",
        filename="006324",
    )

    target = tmp_path / "data" / "006324.md"
    assert target.exists()
    assert "| date" in target.read_text(encoding="utf-8")


def test_multi_symbol_rejects_a_single_file(tmp_path) -> None:
    with pytest.raises(UsageError, match="多个标的"):
        print_output(
            pd.DataFrame([{"code": "000001"}, {"code": "600519"}]),
            "json",
            tmp_path / "quotes.json",
            split_rows=True,
        )


def test_multi_symbol_writes_one_file_per_symbol(tmp_path) -> None:
    print_output(
        pd.DataFrame([{"code": "000001"}, {"code": "600519"}]),
        "csv",
        tmp_path / "quotes",
        split_rows=True,
    )

    assert (tmp_path / "quotes" / "000001.csv").exists()
    assert (tmp_path / "quotes" / "600519.csv").exists()
