import json

from click.testing import CliRunner

from aspool.cli import cli
from aspool.data_flow_contract import DATA_FLOWS, validate_data_flows


def test_every_production_data_block_declares_input_output_and_cli():
    validate_data_flows()
    assert {flow.name for flow in DATA_FLOWS} == {
        "securities",
        "security_calendar",
        "fundamentals",
        "shareholder_counts",
        "stock_daily_bars",
        "corporate_actions",
        "stock_adjustment_factors",
        "stock_daily_features",
        "market_daily_summary",
        "index_daily_bars",
        "etf_daily_bars",
        "etf_adjustment_factors",
    }
    assert all(
        flow.inputs and flow.outputs and flow.write_cli and flow.read_cli for flow in DATA_FLOWS
    )


def test_contract_cli_is_machine_readable():
    result = CliRunner().invoke(cli, ["contract", "--format", "json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert len(rows) == len(DATA_FLOWS)
    assert all(set(row) == {
        "name", "storage", "inputs", "write_cli", "outputs", "read_cli", "lifecycle"
    } for row in rows)
