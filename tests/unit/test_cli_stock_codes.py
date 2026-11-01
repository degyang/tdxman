"""Directory pagination, classification and failure behavior without network access."""

import json
from unittest.mock import MagicMock

import pandas as pd
import pytest
from click.testing import CliRunner

from tdxman.cli import cli
from tdxman.cli.cmd_stock_codes import _collect
from tdxman.models.enums import Market


def page(*pairs):
    return pd.DataFrame([dict(code=c, name=n) for c, n in pairs])


def standard(monkeypatch, pages, count):
    client = MagicMock()
    client.get_security_count.return_value = count
    client.get_security_list.side_effect = pages
    context = MagicMock()
    context.__enter__.return_value = client
    monkeypatch.setattr("tdxman.cli.conn.get_tdx_client", lambda: context)
    return client


def test_classification_and_short_standard_pages(monkeypatch):
    client = standard(
        monkeypatch,
        [
            page(("600000", "浦发银行"), ("510050", "ETF")),
            page(("688001", "科创股"), ("689009", "CDR")),
        ],
        4,
    )
    result = CliRunner().invoke(cli, ["stock-codes", "SH"])
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    rows = json.loads(result.output)
    assert [r["symbol"] for r in rows] == ["600000.SH", "688001.SH", "689009.SH"]
    assert [r["board"] for r in rows] == ["主板", "科创板", "CDR"]
    assert client.get_security_list.call_args_list[1].args == (Market.SH, 2)


def test_filters_keep_leading_zero(monkeypatch):
    standard(monkeypatch, [page(("000001", "平安银行"), ("300001", "创业股"))], 2)
    result = CliRunner().invoke(
        cli,
        [
            "stock-codes",
            "SZ",
            "--scope",
            "main",
            "--code",
            "sz000001,600000.SH",
            "--name",
            "平安银行",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == [
        dict(symbol="000001.SZ", code="000001", market="SZ", name="平安银行", board="主板")
    ]


@pytest.mark.parametrize(
    "pages,count",
    [
        ([page(("600000", "A")), pd.DataFrame()], 2),
        ([page(("600000", "A")), page(("600000", "A"))], 2),
        ([page(("600000", ""))], 1),
        ([pd.DataFrame([dict(code="600000", name="A", market=0)])], 1),
    ],
)
def test_bad_directory_does_not_emit_or_overwrite(monkeypatch, tmp_path, pages, count):
    standard(monkeypatch, pages, count)
    target = tmp_path / "codes.json"
    target.write_text("previous")
    result = CliRunner().invoke(cli, ["stock-codes", "SH", "--output", str(target)])
    assert result.exit_code != 0
    assert target.read_text() == "previous"


def test_count_change_rejects_snapshot(monkeypatch):
    client = standard(monkeypatch, [page(("600000", "A"))], 1)
    client.get_security_count.side_effect = [1, 2]
    result = CliRunner().invoke(cli, ["stock-codes", "SH"])
    assert result.exit_code != 0
    assert "总数变化" in result.output


def test_bj_uses_mac_only(monkeypatch):
    client = MagicMock()
    client.get_stock_quotes_list.return_value = pd.DataFrame(
        [
            dict(code="920001", name="北交股", market=2),
            dict(code="430047", name="北交旧代码", market=2),
        ]
    )
    ctx = MagicMock()
    ctx.__enter__.return_value = client
    monkeypatch.setattr("tdxman.cli.conn.get_mac_client", lambda: ctx)
    standard_factory = MagicMock(side_effect=AssertionError("standard BJ forbidden"))
    monkeypatch.setattr("tdxman.cli.conn.get_tdx_client", standard_factory)
    result = CliRunner().invoke(cli, ["stock-codes", "--scope", "bse"])
    assert result.exit_code == 0, result.output
    assert len(json.loads(result.output)) == 2
    standard_factory.assert_not_called()


def test_mac_full_page_continues():
    client = MagicMock()
    client.get_stock_quotes_list.side_effect = [
        page(*[(f"920{i:03}", "股") for i in range(80)]),
        page(("920080", "尾页")),
    ]
    rows = _collect(client, "BJ", mac=True)
    assert len(rows) == 81
    assert client.get_stock_quotes_list.call_args_list[1].kwargs["start"] == 80


def test_invalid_filters_fail_before_connect(monkeypatch):
    factory = MagicMock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr("tdxman.cli.conn.get_tdx_client", factory)
    for args in [["SH", "--scope", "bse"], ["SH", "--code", "123"]]:
        result = CliRunner().invoke(cli, ["stock-codes", *args])
        assert result.exit_code == 2, result.output
    factory.assert_not_called()


def test_empty_filter_result_and_csv(monkeypatch):
    standard(monkeypatch, [page(("600000", "A"))], 1)
    result = CliRunner().invoke(cli, ["stock-codes", "SH", "--name", "不存在"])
    assert result.exit_code == 0
    assert json.loads(result.output) == []
    standard(monkeypatch, [page(("000001", "平安银行"))], 1)
    result = CliRunner().invoke(cli, ["stock-codes", "SZ", "--format", "csv"])
    assert result.exit_code == 0, result.output
    assert "000001.SZ,000001,SZ,平安银行,主板" in result.output


def test_table_progress_uses_stderr(monkeypatch):
    standard(monkeypatch, [page(("600000", "浦发银行"))], 1)
    result = CliRunner().invoke(cli, ["stock-codes", "SH", "--format", "table", "--progress"])
    assert result.exit_code == 0, result.output
    assert "正在连接" in result.stderr
    assert "已读取 1/1" in result.stderr
    assert "完成：1 只股票" in result.stderr
    assert "正在连接" not in result.stdout
    assert "600000" in result.stdout


def test_bj_empty_directory_retries_complete_source(monkeypatch):
    from tdxman.cli.cmd_stock_codes import _read_bj_rows

    first = MagicMock()
    first.__enter__.return_value.get_stock_quotes_list.return_value = pd.DataFrame()
    monkeypatch.setattr("tdxman.cli.conn.get_mac_client", lambda: first)
    monkeypatch.setattr("tdxman.bestip.preferred", lambda _: [dict(host="alternative", port=7709)])
    alternate = MagicMock()
    alternate.__enter__.return_value.get_stock_quotes_list.return_value = page(("920001", "北交股"))
    factory = MagicMock(return_value=alternate)
    monkeypatch.setattr("tdxman.mac.client.MacClient", factory)
    messages = []
    rows = _read_bj_rows(messages.append)
    assert [r["symbol"] for r in rows] == ["920001.BJ"]
    assert any("重试 alternative" in message for message in messages)
    assert factory.call_args.kwargs["auto_reconnect"] is False


def test_bj_all_sources_empty_do_not_overwrite(monkeypatch, tmp_path):
    first = MagicMock()
    first.__enter__.return_value.get_stock_quotes_list.return_value = pd.DataFrame()
    monkeypatch.setattr("tdxman.cli.conn.get_mac_client", lambda: first)
    monkeypatch.setattr("tdxman.bestip.preferred", lambda _: [dict(host="alternative", port=7709)])
    monkeypatch.setattr("tdxman.mac.client.MacClient", lambda *a, **kw: first)
    target = tmp_path / "previous.json"
    target.write_text("previous")
    result = CliRunner().invoke(cli, ["abnormal-list", "BJ", "--type", "st", "-o", str(target)])
    assert result.exit_code != 0
    assert "北交所目录重试失败" in result.output
    assert target.read_text() == "previous"
