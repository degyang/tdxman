from click.testing import CliRunner

from aspool.cli import cli


def _help(*args: str) -> str:
    result = CliRunner().invoke(cli, [*args, "--help"], prog_name="aspool")
    assert result.exit_code == 0, result.output
    return result.output


def test_aspool_root_help_uses_grouped_commands_options_and_examples():
    output = _help()
    assert output.index("Commands:") < output.index("Options:") < output.index("示例:")
    assert "初始化与导入" in output
    assert "同步与维护" in output
    assert "读取与检查" in output


def test_aspool_leaf_help_uses_options_reference_and_examples_order():
    for command in ("init", "import", "sync", "update", "fundamentals", "status", "query"):
        output = _help(command)
        assert output.index("Options:") < output.index("参数说明:") < output.index("示例:")


def test_aspool_help_documents_conditional_sync_options():
    output = _help("sync")
    assert "仅 source=tdx 时有效" in output
    assert "aspool sync --source free-stockdb --period daily" in output


def test_all_sync_keeps_index_bootstrap_unbounded(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("aspool.cli._run_all", lambda target, **kwargs: calls.append(kwargs))
    result = CliRunner().invoke(cli, ["sync", "--type", "all", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert calls[0]["count"] == 10
    assert calls[0]["index_count"] is None


def test_aspool_query_rejects_invalid_symbol_without_traceback():
    result = CliRunner().invoke(cli, ["query", "INVALID"])
    assert result.exit_code == 2
    assert "Traceback" not in result.output
    assert "SYMBOL 应为市场加六位代码" in result.output


def test_aspool_query_reports_missing_symbol_without_traceback(tmp_path):
    result = CliRunner().invoke(cli, ["query", "SZ999999", "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "本地数据池未找到 SZ999999 的 daily 数据" in result.output


def test_aspool_tdx_sync_rejects_empty_pool_without_traceback(tmp_path):
    result = CliRunner().invoke(cli, ["sync", "--source", "tdx", "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "tdx 同步只修补已导入标的" in result.output
    assert "aspool import --period daily" in result.output
