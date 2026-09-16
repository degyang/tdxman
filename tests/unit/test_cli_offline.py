from __future__ import annotations

import struct

from click.testing import CliRunner

from tdxman.cli import cli

_DAILY_RECORD = struct.Struct("<IIIIIfII")


def _write_daily_file(vipdoc, code: str = "600519") -> None:
    filepath = vipdoc / "sh" / "lday" / f"sh{code}.day"
    filepath.parent.mkdir(parents=True)
    filepath.write_bytes(
        _DAILY_RECORD.pack(20260915, 127100, 128500, 127000, 127275, 1000.0, 10000, 0)
        + _DAILY_RECORD.pack(20260916, 127393, 127498, 125410, 125800, 2000.0, 20000, 0)
    )


def test_offline_reads_the_latest_local_daily_bar_with_count(tmp_path) -> None:
    vipdoc = tmp_path / "vipdoc"
    _write_daily_file(vipdoc)

    result = CliRunner().invoke(
        cli,
        ["offline", "SH", "600519", "--vipdoc", str(vipdoc), "--count", "1", "--format", "table"],
    )

    assert result.exit_code == 0, result.output
    assert "1258.00" in result.output
    assert "1272.75" not in result.output


def test_offline_kline_honours_latest_based_paging(tmp_path) -> None:
    vipdoc = tmp_path / "vipdoc"
    _write_daily_file(vipdoc)

    result = CliRunner().invoke(
        cli,
        ["offline", "SH", "600519", "--vipdoc", str(vipdoc), "--count", "1"],
    )

    assert result.exit_code == 0, result.output
    assert "2026-09-16" in result.output
    assert "2026-09-15" not in result.output
