"""管理命令：ping, version。"""

from __future__ import annotations

from pathlib import Path

import click

from .help import StandardHelpCommand
from .output import output_options


@click.command(cls=StandardHelpCommand)
@click.option("--timeout", default=5.0, help="测速超时（秒）")
@output_options
def ping(timeout: float, output_fmt: str, output_path: Path | None) -> None:
    """测量通达信服务器延迟。

    示例：

      tdxman ping

      tdxman ping --timeout 3 --format table
    """
    import pandas as pd

    from ..transport.sync import ping_all, ping_mac_all
    from .output import print_output

    fmt = output_fmt

    click.echo("正在测速标准服务器...", err=True)
    std_results = ping_all(timeout=timeout)
    click.echo("正在测速MAC服务器...", err=True)
    mac_results = ping_mac_all(timeout=timeout)

    rows: list[dict[str, str | float]] = []
    for host, latency in std_results:
        rows.append({"group": "standard", "host": host, "latency_ms": round(latency * 1000, 1)})
    for host, latency in mac_results:
        rows.append({"group": "mac", "host": host, "latency_ms": round(latency * 1000, 1)})

    df = pd.DataFrame(rows)
    print_output(df, fmt, output_path)


def _probe_endpoint(protocol: str, endpoint: dict, timeout: float) -> dict:
    """Validate an endpoint with a small real business request."""
    from time import perf_counter

    host, port = endpoint["host"], endpoint["port"]
    started = perf_counter()
    if protocol == "standard":
        from ..client import TdxClient
        from ..models.enums import KlineCategory, Market

        with TdxClient(host, port=port, timeout=timeout, auto_reconnect=False) as client:
            frame = client.get_index_bars(Market.SH, "000001", KlineCategory.DAY, 0, 1)
            if frame.empty:
                raise ValueError("standard daily K line is empty")
    elif protocol == "mac":
        from ..mac.client import MacClient
        from ..mac.enums import Period
        from ..models.enums import Market

        with MacClient(host, port=port, timeout=timeout, auto_reconnect=False,
                       heartbeat_interval=0) as client:
            frame = client.get_stock_kline(Market.SH, "000001", Period.DAILY, count=1)
            if frame.empty:
                raise ValueError("MAC daily K line is empty")
    else:
        if endpoint.get("dialect") == "mac":
            from ..ex.commands.get_instrument_count import GetExInstrumentCountCmd
            from ..ex.commands.get_instrument_info import GetExInstrumentInfoCmd
            from ..ex.mac_client import MacExClient

            with MacExClient(host, port=port, timeout=timeout, auto_reconnect=False) as client:
                if client._execute(GetExInstrumentCountCmd()) <= 0:
                    raise ValueError("MAC EX instrument count is empty")
                instruments = client._execute(GetExInstrumentInfoCmd(start=0, count=1))
                if not instruments:
                    raise ValueError("MAC EX instrument directory is empty")
                instrument = instruments[0]
                quote = client.goods_quotes([(instrument.market, instrument.code)])
                if quote.empty:
                    raise ValueError("MAC EX quote is empty")
        else:
            from ..ex.client import ExTdxClient

            with ExTdxClient(host, port=port, timeout=timeout, auto_reconnect=False) as client:
                if client.get_instrument_count() <= 0:
                    raise ValueError("EX instrument count is empty")
                instruments = client.get_instrument_info(0, 1)
                if not instruments:
                    raise ValueError("EX instrument directory is empty")
                instrument = instruments[0]
                quote = client.get_instrument_quote(instrument.market, instrument.code)
                if quote is None:
                    raise ValueError("EX quote is empty")
    return {**endpoint, "latency_ms": round((perf_counter() - started) * 1000, 3)}


@click.command("bestip", cls=StandardHelpCommand)
@click.option("--protocol", type=click.Choice(["standard", "mac", "ex", "all"]),
              default="all", show_default=True)
@click.option("--timeout", type=click.FloatRange(min=0.1), default=3.0, show_default=True)
@click.option("--limit", type=click.IntRange(min=1), default=5, show_default=True)
def bestip(protocol: str, timeout: float, limit: int) -> None:
    """以真实行情请求验证候选服务器，并保存本机可用排名。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from ..bestip import candidates, ranked, save_rankings

    selected = [protocol] if protocol != "all" else ["standard", "mac", "ex"]
    groups, failures = {}, []
    for name in selected:
        passed = []
        with ThreadPoolExecutor(max_workers=min(16, len(candidates(name)))) as executor:
            tasks = {executor.submit(_probe_endpoint, name, endpoint, timeout): endpoint
                     for endpoint in candidates(name)}
            for task in as_completed(tasks):
                try:
                    passed.append(task.result())
                except Exception as exc:  # endpoint failures are expected during probing
                    failures.append((name, tasks[task]["host"], str(exc)))
        passed.sort(key=lambda item: item["latency_ms"])
        if not passed:
            raise click.ClickException(f"{name} 没有通过真实行情验证的服务器")
        groups[name] = passed
    existing = {name: ranked(name) for name in ("standard", "mac", "ex")}
    existing.update(groups)
    save_rankings(existing)
    for name in selected:
        click.echo(f"{name}：{len(groups[name])}/{len(candidates(name))} 可用")
        for item in groups[name][:limit]:
            click.echo(f"  {item['host']}:{item['port']}  {item['latency_ms']:.1f} ms")
    if failures:
        click.echo(f"未通过验证：{len(failures)} 个", err=True)


@click.command(cls=StandardHelpCommand)
def version() -> None:
    """显示版本号。"""
    from .. import __version__

    click.echo(f"tdxman {__version__}")
