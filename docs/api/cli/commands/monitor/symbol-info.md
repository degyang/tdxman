# `tdxman symbol-info`

```bash
tdxman symbol-info 000001.SZ --format table
tdxman symbol-info 600519.SH --source baostock --format json
```

默认 `--source tdx` 调用 `MacClient.get_symbol_info(market, code)` 获取当前个股特征快照。`--source baostock` 调用 `BaostockClient.get_stock_basic(market, code)`，返回沪深 A 股当前名称、上市日期、退出日期、来源和抓取时间；不支持北交所或指数。两条路径字段和时效不同，按 `source` 区分后再比较。无历史日期参数；BaoStock 资料不应当作历史名称序列。CLI：`src/tdxman/cli/cmd_info.py`；API：`src/tdxman/mac/client.py#get_symbol_info`、`src/tdxman/baostock.py#get_stock_basic`。

[CLI 总入口](../../README.md) · [命令索引](../../commands.md)
