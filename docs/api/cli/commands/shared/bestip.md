# `tdxman bestip`

```bash
tdxman bestip --protocol mac --timeout 3 --limit 5
```

`--protocol standard|mac|ex|all` 默认 `all`；`--timeout` 默认 3 秒且至少 0.1；`--limit` 默认 5 且至少 1，仅控制每组打印的领先服务器数。此命令不是只读测速：它对候选端点做实际行情请求，通过后原子写入设备本地 `settings/bestip.json` 排名。标准协议读一根上证指数日线，MAC 读一根日线，EX 检查目录与报价；某选中协议没有通过验证的服务器时失败。

输出为纯文本可用数和排名，失败数写 stderr，不提供 `--format`/`--output`。只有要更新本机优选服务器时执行；仅排查延迟用 [ping](ping.md)。对应 API：`src/tdxman/bestip.py` 的 `candidates`、`ranked`、`save_rankings`；CLI 路由见 `src/tdxman/cli/cmd_admin.py`。

[CLI 总入口](../../README.md) · [命令索引](../../commands.md)
