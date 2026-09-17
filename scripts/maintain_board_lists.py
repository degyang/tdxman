"""Run with the project's installed environment; only --write updates configuration."""

import json
from pathlib import Path

import click

from aspool.index_lists import DEFAULT_LIST, atomic_json, collect, difference
from tdxman.cli.conn import get_mac_client
from tdxman.exceptions import TdxError


@click.command()
@click.option("--target", type=click.Choice(["index"]), default="index")
@click.option("--output", type=click.Path(path_type=Path), default=DEFAULT_LIST)
@click.option("--write", is_flag=True, help="显示差异后原子更新清单；默认仅预览。")
def main(target, output, write):
    """维护指数清单并输出新增、删除和字段变化；预留其他清单目标扩展。"""
    try:
        old = json.loads(output.read_text()) if output.exists() else {}
        with get_mac_client() as client:
            new, excluded = collect(client)
        diff = difference(old, new)
        diff["excluded"] = excluded
        click.echo(json.dumps(diff, ensure_ascii=False, indent=2))
        if write:
            atomic_json(output, new)
    except (ValueError, OSError, TdxError) as exc:
        raise click.ClickException(str(exc)) from exc


if __name__ == "__main__":
    main()
