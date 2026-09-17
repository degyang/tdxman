from click.testing import CliRunner

from tdxman.cli import cli


def _help(*args: str) -> str:
    result = CliRunner().invoke(cli, [*args, "--help"], prog_name="tdxman")
    assert result.exit_code == 0, result.output
    return result.output


def test_all_leaf_command_help_uses_options_reference_examples_order():
    commands = [
        ("auction",),
        ("belong-board",),
        ("board-members",),
        ("capital-flow",),
        ("finance",),
        ("fund-flow",),
        ("kline",),
        ("market-stat",),
        ("markets",),
        ("offline",),
        ("ping",),
        ("quote",),
        ("quote-list",),
        ("server-info",),
        ("symbol-info",),
        ("tick",),
        ("transaction",),
        ("unusual",),
        ("version",),
        ("ex", "kline"),
        ("ex", "markets"),
        ("ex", "quote"),
        ("ex", "quote-list"),
        ("ex", "tick"),
    ]
    for command in commands:
        output = _help(*command)
        assert output.index("Options:") < output.index("示例:")


def test_ex_group_help_uses_commands_then_options_then_examples():
    output = _help("ex")
    assert output.index("Commands:") < output.index("Options:") < output.index("示例:")
