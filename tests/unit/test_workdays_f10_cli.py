"""Contracts for the new trading-day and TQLEX CLI commands."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest
from click.testing import CliRunner

from tdxman.cli import cli
from tdxman.exceptions import TdxCommandError
from tdxman.f10 import F10Client
from tdxman.models.enums import Market
from tdxman.workdays import WorkdayService


class FakeBars:
    def __init__(self) -> None:
        self.calls = 0

    def get_stock_kline(self, market, code, period, *, count):
        self.calls += 1
        assert (market, code, count) == (Market.SH, "000001", 10000)
        return pd.DataFrame({"datetime": ["2026-05-27", "2026-05-29", "2026-06-01"]})


def test_workdays_uses_observed_sessions_and_preserves_unknown_dates() -> None:
    client = FakeBars()
    calendar = WorkdayService(client)  # type: ignore[arg-type]

    assert calendar.next_workday("2026-05-28") == date(2026, 5, 29)
    assert calendar.previous_workday("2026-05-28") == date(2026, 5, 27)
    assert calendar.is_workday("2026-05-28") is False
    assert calendar.is_workday("2026-06-02") is None
    assert calendar.next_workday("2026-06-01") is None
    assert calendar.range("2026-05-27", "2026-06-01") == [
        date(2026, 5, 27),
        date(2026, 5, 29),
        date(2026, 6, 1),
    ]
    with pytest.raises(ValueError, match="超出"):
        calendar.range("2026-05-27", "2026-06-02")
    assert client.calls == 1


def test_workdays_cli_reports_unknown_outside_coverage() -> None:
    @contextmanager
    def fake_client():
        yield FakeBars()

    with patch("tdxman.cli.conn.get_mac_client", fake_client):
        result = CliRunner().invoke(cli, ["workdays", "--date", "2026-06-02"])

    assert result.exit_code == 0, result.output
    row = json.loads(result.output)[0]
    assert row["is_open"] is None
    assert row["next"] is None
    assert row["coverage_end"] == "2026-06-01"


def test_f10_client_parses_multiple_tables_and_duplicate_columns() -> None:
    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps(self.payload).encode()

    identity = FakeResponse(
        {
            "ErrorCode": 0,
            "ResultSets": [{"ColName": ["T002", "sc"], "Content": [["600519", 1]]}],
        }
    )
    profile = FakeResponse(
        {
            "ErrorCode": 0,
            "ResultSets": [
                {
                    "ResultSetKey": "basic",
                    "ColName": ["T001", "T001"],
                    "Content": [["甲", "乙"]],
                },
                {
                    "ResultSetKey": "detail",
                    "ColDes": [{"Name": "项目"}],
                    "Content": [["净资产"]],
                },
            ],
        }
    )
    with patch("tdxman.f10.urlopen", side_effect=[identity, profile]) as request:
        response = F10Client().query(Market.SH, "600519", "profile")

    payload = json.loads(request.call_args_list[1].args[0].data)
    assert payload == {"Params": ["8", "600519", ""]}
    assert response.entry == "CWServ.tdxf10_gg_gsgk"
    assert response.tables[0].rows[0] == {"T001": "甲", "T001__2": "乙"}
    assert response.to_frame()["result_set"].tolist() == ["basic", "detail"]


def test_f10_rejects_a_code_from_the_wrong_market() -> None:
    class WrongMarketResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps(
                {"ResultSets": [{"ColName": ["T002", "sc"], "Content": [["000001", 0]]}]}
            ).encode()

    with patch("tdxman.f10.urlopen", return_value=WrongMarketResponse()) as request:
        with pytest.raises(TdxCommandError, match="不匹配"):
            F10Client().query(Market.SH, "000001", "profile")
    assert request.call_count == 1


def test_f10_cli_formats_detailed_rows_and_reports_server_errors() -> None:
    from tdxman.f10 import F10Response, F10Table

    response = F10Response(
        entry="CWServ.tdxf10_gg_cwfx",
        tables=(F10Table("report", ({"T001": "资产负债表", "value": 12.5},)),),
        raw={},
    )
    with patch("tdxman.f10.F10Client.query", return_value=response) as query:
        result = CliRunner().invoke(
            cli, ["f10", "600519.SH", "--section", "report", "--format", "table"]
        )
    assert result.exit_code == 0, result.output
    assert "资产负债表" in result.output
    query.assert_called_once_with(
        Market.SH, "600519", "report", report_type="zcfzb", report_date=None
    )

    with patch("tdxman.f10.F10Client.query", side_effect=TdxCommandError("bad Entry")):
        failed = CliRunner().invoke(cli, ["f10", "600519.SH"])
    assert failed.exit_code == 1
    assert "bad Entry" in failed.output


def test_root_help_has_requested_command_groups() -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0, result.output
    commands = result.output.split("Commands:", 1)[1].split("Options:", 1)[0]
    headings = ["实时行情", "实时市场全景", "历史行情", "个股数据", "板块数据", "扩展市场", "工具"]
    positions = [commands.index(heading) for heading in headings]
    assert positions == sorted(positions)
    assert "workdays" in result.output
    assert "f10" in result.output
