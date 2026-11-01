"""Abnormal stock union, source flags, and completeness checks."""

import json
from unittest.mock import MagicMock

import pandas as pd
import pytest
from click.testing import CliRunner

from tdxman.cli import cli
from tdxman.cli.cmd_stock_status import _st_type


def setup_source(monkeypatch, quotes=None):
    rows = [
        dict(symbol=f"60000{i}.SH", code=f"60000{i}", market="SH", name=name, board="主板")
        for i, name in enumerate(["普通股", "ST甲", "*ST乙", "停牌股"])
    ]
    monkeypatch.setattr("tdxman.cli.cmd_stock_status.read_stock_rows", lambda *args: rows)
    client = MagicMock()
    client.get_security_quotes.return_value = pd.DataFrame(
        quotes
        if quotes is not None
        else [
            dict(market=1, code=r["code"], trading_status_raw=32 if i >= 2 else 0, vol=0)
            for i, r in enumerate(rows)
        ]
    )
    ctx = MagicMock()
    ctx.__enter__.return_value = client
    monkeypatch.setattr("tdxman.cli.conn.get_tdx_quote_client", lambda: ctx)
    return client


@pytest.mark.parametrize(
    "name,expected",
    [
        ("ST甲", "ST"),
        ("*ST甲", "*ST"),
        ("SST甲", "ST"),
        ("S*ST甲", "*ST"),
        (" st甲 ", "ST"),
        ("普通ST", None),
        ("普通股", None),
    ],
)
def test_st_prefix(name, expected):
    assert _st_type(name) == expected


def test_union_deduplicates_and_does_not_infer_from_zero_volume(monkeypatch):
    setup_source(monkeypatch)
    result = CliRunner().invoke(cli, ["abnormal-list", "SH"])
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    rows = json.loads(result.stdout)
    assert [r["code"] for r in rows] == ["600001", "600002", "600003"]
    assert rows[1]["st_type"] == "*ST" and rows[1]["is_suspended"]
    assert rows[2]["st_type"] is None
    assert rows[0]["is_suspended"] is False
    assert all(r["checked_at"] for r in rows)


def test_st_only_does_not_query_quotes(monkeypatch):
    client = setup_source(monkeypatch)
    result = CliRunner().invoke(cli, ["abnormal-list", "--type", "st"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert len(rows) == 2
    assert all(r["is_suspended"] is None for r in rows)
    client.get_security_quotes.assert_not_called()


def test_suspended_only_preserves_st_and_table_columns(monkeypatch):
    setup_source(monkeypatch)
    result = CliRunner().invoke(
        cli, ["abnormal-list", "--type", "suspended", "--format", "table", "--progress"]
    )
    assert result.exit_code == 0, result.output
    assert "*ST乙" in result.stdout and "停牌股" in result.stdout
    assert "st_type" in result.stdout and "is_suspended" in result.stdout
    assert "已检查" in result.stderr and "已检查" not in result.stdout


@pytest.mark.parametrize(
    "quotes",
    [
        [],
        [dict(market=1, code="600000", trading_status_raw=None)],
        [dict(market=1, code="600000", trading_status_raw=32)] * 2,
        [dict(market=1, code="999999", trading_status_raw=32)],
    ],
)
def test_incomplete_status_does_not_overwrite(monkeypatch, tmp_path, quotes):
    setup_source(monkeypatch, quotes)
    target = tmp_path / "abnormal.json"
    target.write_text("previous")
    result = CliRunner().invoke(cli, ["abnormal-list", "--output", str(target)])
    assert result.exit_code != 0
    assert target.read_text() == "previous"


def test_quote_status_word_is_retained():
    from pathlib import Path

    from tdxman.commands.security_quotes import GetSecurityQuotesCmd
    from tdxman.models.enums import Market

    body = bytes.fromhex((Path(__file__).parents[1] / "fixtures/security_quotes.hex").read_text())
    rows = GetSecurityQuotesCmd([(Market.SH, "600000")]).parse_response(body)
    assert rows
    assert all(isinstance(r.trading_status_raw, int) for r in rows)
