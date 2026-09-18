"""CLI 证券标识端到端测试。

使用 CliRunner + mock 连接器验证新旧参数到达相同 market/code。
两个客户端同时 mock，确保不发生任何真实网络请求。

注意：测试使用真实股票代码 600519.SH / 000001.SZ。
000001.SH 是上证指数，会在 kline 中走指数分支，不作为股票用例。
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from click.testing import CliRunner

from tdxman.cli import cli, conn
from tdxman.models.enums import Market


@pytest.fixture
def mock_clients():
    """同时 mock MAC 与标准协议客户端，返回 (mac, tdx)。

    用真正的 contextmanager 替换 conn 工厂，使 `with get_mac_client() as c`
    拿到的就是同一个 mock；MagicMock 的 __enter__ 会返回另一个对象。
    """
    mac = MagicMock()
    tdx = MagicMock()

    @contextmanager
    def fake_mac():
        yield mac

    @contextmanager
    def fake_tdx():
        yield tdx

    with patch.object(conn, "get_mac_client", fake_mac), patch.object(
        conn, "get_tdx_client", fake_tdx
    ):
        yield mac, tdx


class TestKlineCLIE2E:
    """kline CLI 端到端测试（股票分支）。"""

    def test_new_format_stock(self, mock_clients):
        """新格式: kline 600519.SH 走股票分支。"""
        mac, _ = mock_clients
        mac.get_stock_kline.return_value = MagicMock()
        result = CliRunner().invoke(cli, ["kline", "600519.SH", "--count", "10"])
        assert result.exit_code == 0, result.output
        mac.get_stock_kline.assert_called_once()
        args = mac.get_stock_kline.call_args[0]
        assert args[0] == Market.SH
        assert args[1] == "600519"

    def test_old_format_stock(self, mock_clients):
        """旧格式: kline SH 600519 走股票分支。"""
        mac, _ = mock_clients
        mac.get_stock_kline.return_value = MagicMock()
        result = CliRunner().invoke(cli, ["kline", "SH", "600519", "--count", "10"])
        assert result.exit_code == 0, result.output
        mac.get_stock_kline.assert_called_once()
        args = mac.get_stock_kline.call_args[0]
        assert args[0] == Market.SH
        assert args[1] == "600519"

    def test_new_old_reach_same_query(self, mock_clients):
        """新旧格式到达相同 market/code。"""
        mac, _ = mock_clients
        mac.get_stock_kline.return_value = MagicMock()

        CliRunner().invoke(cli, ["kline", "000001.SZ", "--count", "10"])
        new_args = mac.get_stock_kline.call_args[0][:2]

        mac.reset_mock()
        mac.get_stock_kline.return_value = MagicMock()

        CliRunner().invoke(cli, ["kline", "SZ", "000001", "--count", "10"])
        old_args = mac.get_stock_kline.call_args[0][:2]

        assert new_args == old_args == (Market.SZ, "000001")

    def test_case_insensitive(self, mock_clients):
        """大小写: kline 600519.sh 规范化到 SH。"""
        mac, _ = mock_clients
        mac.get_stock_kline.return_value = MagicMock()
        result = CliRunner().invoke(cli, ["kline", "600519.sh", "--count", "10"])
        assert result.exit_code == 0, result.output
        args = mac.get_stock_kline.call_args[0]
        assert args[0] == Market.SH
        assert args[1] == "600519"

    def test_bj_market(self, mock_clients):
        """北交所: kline 830001.BJ。"""
        mac, _ = mock_clients
        mac.get_stock_kline.return_value = MagicMock()
        result = CliRunner().invoke(cli, ["kline", "830001.BJ", "--count", "10"])
        assert result.exit_code == 0, result.output
        args = mac.get_stock_kline.call_args[0]
        assert args[0] == Market.BJ
        assert args[1] == "830001"

    def test_invalid_format_rejected(self, mock_clients):
        """无效格式被拒。"""
        result = CliRunner().invoke(cli, ["kline", "abc", "--count", "10"])
        assert result.exit_code != 0

    def test_pure_code_rejected(self, mock_clients):
        """纯六位代码被拒（无市场）。"""
        result = CliRunner().invoke(cli, ["kline", "600519", "--count", "10"])
        assert result.exit_code != 0

    def test_index_branch_still_works(self, mock_clients):
        """指数代码仍走指数分支（000001.SH 是上证指数）。"""
        _, tdx = mock_clients
        tdx.get_index_bars.return_value = MagicMock()
        result = CliRunner().invoke(cli, ["kline", "000001.SH", "--count", "10"])
        assert result.exit_code == 0, result.output
        tdx.get_index_bars.assert_called_once()
        args = tdx.get_index_bars.call_args[0]
        assert args[0] == Market.SH
        assert args[1] == "000001"


class TestTickCLIE2E:
    """tick CLI 端到端测试。"""

    def test_new_format(self, mock_clients):
        mac, _ = mock_clients
        mac.get_tick_chart.return_value = MagicMock()
        result = CliRunner().invoke(cli, ["tick", "000001.SZ"])
        assert result.exit_code == 0, result.output
        args = mac.get_tick_chart.call_args[0]
        assert args[:2] == (Market.SZ, "000001")

    def test_old_format(self, mock_clients):
        mac, _ = mock_clients
        mac.get_tick_chart.return_value = MagicMock()
        result = CliRunner().invoke(cli, ["tick", "SZ", "000001"])
        assert result.exit_code == 0, result.output
        args = mac.get_tick_chart.call_args[0]
        assert args[:2] == (Market.SZ, "000001")

    def test_new_old_reach_same_query(self, mock_clients):
        mac, _ = mock_clients
        mac.get_tick_chart.return_value = MagicMock()
        CliRunner().invoke(cli, ["tick", "600519.SH"])
        new_args = mac.get_tick_chart.call_args[0][:2]

        mac.reset_mock()
        mac.get_tick_chart.return_value = MagicMock()
        CliRunner().invoke(cli, ["tick", "SH", "600519"])
        old_args = mac.get_tick_chart.call_args[0][:2]

        assert new_args == old_args == (Market.SH, "600519")


class TestFinanceCLIE2E:
    """finance CLI 端到端测试。"""

    def test_new_format(self, mock_clients):
        _, tdx = mock_clients
        tdx.get_finance_info.return_value = MagicMock()
        result = CliRunner().invoke(cli, ["finance", "000001.SZ"])
        assert result.exit_code == 0, result.output
        args = tdx.get_finance_info.call_args[0]
        assert args[:2] == (Market.SZ, "000001")

    def test_old_format(self, mock_clients):
        _, tdx = mock_clients
        tdx.get_finance_info.return_value = MagicMock()
        result = CliRunner().invoke(cli, ["finance", "SZ", "000001"])
        assert result.exit_code == 0, result.output
        args = tdx.get_finance_info.call_args[0]
        assert args[:2] == (Market.SZ, "000001")

    def test_new_old_reach_same_query(self, mock_clients):
        _, tdx = mock_clients
        tdx.get_finance_info.return_value = MagicMock()
        CliRunner().invoke(cli, ["finance", "000001.SZ"])
        new_args = tdx.get_finance_info.call_args[0][:2]

        tdx.reset_mock()
        tdx.get_finance_info.return_value = MagicMock()
        CliRunner().invoke(cli, ["finance", "SZ", "000001"])
        old_args = tdx.get_finance_info.call_args[0][:2]

        assert new_args == old_args == (Market.SZ, "000001")


class TestFundFlowCLIE2E:
    """fund-flow CLI 端到端测试。"""

    def test_new_format(self, mock_clients):
        _, tdx = mock_clients
        tdx.get_history_fund_flow.return_value = MagicMock()
        result = CliRunner().invoke(cli, ["fund-flow", "000001.SZ"])
        assert result.exit_code == 0, result.output
        args = tdx.get_history_fund_flow.call_args[0]
        assert args[:2] == (Market.SZ, "000001")

    def test_old_format(self, mock_clients):
        _, tdx = mock_clients
        tdx.get_history_fund_flow.return_value = MagicMock()
        result = CliRunner().invoke(cli, ["fund-flow", "SZ", "000001"])
        assert result.exit_code == 0, result.output
        args = tdx.get_history_fund_flow.call_args[0]
        assert args[:2] == (Market.SZ, "000001")

    def test_new_old_reach_same_query(self, mock_clients):
        _, tdx = mock_clients
        tdx.get_history_fund_flow.return_value = MagicMock()
        CliRunner().invoke(cli, ["fund-flow", "000001.SZ"])
        new_args = tdx.get_history_fund_flow.call_args[0][:2]

        tdx.reset_mock()
        tdx.get_history_fund_flow.return_value = MagicMock()
        CliRunner().invoke(cli, ["fund-flow", "SZ", "000001"])
        old_args = tdx.get_history_fund_flow.call_args[0][:2]

        assert new_args == old_args == (Market.SZ, "000001")


class TestRemainingSingleSymbolCLIE2E:
    """transaction / auction / capital-flow / belong-board / symbol-info 端到端。"""

    @pytest.mark.parametrize(
        "command, method, client_kind",
        [
            ("transaction", "get_transactions", "mac"),
            ("auction", "get_auction", "mac"),
            ("capital-flow", "get_capital_flow", "mac"),
            ("belong-board", "get_belong_board", "mac"),
            ("symbol-info", "get_symbol_info", "mac"),
        ],
    )
    def test_new_old_reach_same_query(self, mock_clients, command, method, client_kind):
        mac, _ = mock_clients
        target = getattr(mac, method)
        target.return_value = MagicMock()

        result = CliRunner().invoke(cli, [command, "600519.SH"])
        assert result.exit_code == 0, result.output
        new_args = target.call_args[0][:2]

        mac.reset_mock()
        target = getattr(mac, method)
        target.return_value = MagicMock()
        result = CliRunner().invoke(cli, [command, "SH", "600519"])
        assert result.exit_code == 0, result.output
        old_args = target.call_args[0][:2]

        assert new_args == old_args == (Market.SH, "600519")

    @pytest.mark.parametrize(
        "command", ["transaction", "auction", "capital-flow", "belong-board", "symbol-info"]
    )
    def test_rejects_bare_code(self, mock_clients, command):
        """纯六位代码（无市场）被拒。"""
        result = CliRunner().invoke(cli, [command, "600519"])
        assert result.exit_code != 0


class TestQuoteCLIE2E:
    """quote CLI 端到端测试。"""

    def test_new_old_identical_rendered_output(self, mock_clients):
        """新旧调用渲染出的内容完全一致（非仅路由参数）。"""
        mac, _ = mock_clients
        frame = pd.DataFrame(
            [
                {
                    "market": 1,
                    "code": "600519",
                    "name": "贵州茅台",
                    "price": 1800.0,
                    "pre_close": 1790.0,
                    "open": 1795.0,
                    "high": 1810.0,
                    "low": 1790.0,
                    "vol": 1000000.0,
                    "amount": 1.8e9,
                }
            ]
        )
        mac.get_stock_quotes.return_value = frame

        new = CliRunner().invoke(cli, ["quote", "600519.SH"])
        old = CliRunner().invoke(cli, ["quote", "SH 600519"])

        assert new.exit_code == 0, new.output
        assert old.exit_code == 0, old.output
        assert new.output == old.output

    def test_new_format(self, mock_clients):
        mac, _ = mock_clients
        mac.get_stock_quotes.return_value = MagicMock()
        result = CliRunner().invoke(cli, ["quote", "600519.SH"])
        assert result.exit_code == 0, result.output
        assert mac.get_stock_quotes.call_args[0][0] == [(Market.SH, "600519")]

    def test_old_format(self, mock_clients):
        mac, _ = mock_clients
        mac.get_stock_quotes.return_value = MagicMock()
        result = CliRunner().invoke(cli, ["quote", "SZ 000001"])
        assert result.exit_code == 0, result.output
        assert mac.get_stock_quotes.call_args[0][0] == [(Market.SZ, "000001")]

    def test_mixed_format(self, mock_clients):
        mac, _ = mock_clients
        mac.get_stock_quotes.return_value = MagicMock()
        result = CliRunner().invoke(cli, ["quote", "600519.SH,SZ 000001"])
        assert result.exit_code == 0, result.output
        assert mac.get_stock_quotes.call_args[0][0] == [
            (Market.SH, "600519"),
            (Market.SZ, "000001"),
        ]
