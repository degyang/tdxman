# `tdxman markets`

```bash
tdxman markets --format table
```

列出 `SH`、`SZ`、`BJ` 和示例代码。数据由 `src/tdxman/cli/cmd_info.py` 内置的三行表生成，不连接服务器，不代表某标的已上市或当前可交易。对应 Python 枚举是 `tdxman.models.enums.Market`（`SZ=0, SH=1, BJ=2`）。此命令接受共用 `--format`、`--output`、`--fields`；默认 JSON 记录数组。扩展市场名称另用 `tdxman ex markets`。

获取实际股票清单用 [`tdxman stock-codes`](../market/stock-codes.md)。

[CLI 总入口](../../README.md) · [命令索引](../../commands.md)
