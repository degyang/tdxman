# `tdxman capital-flow`

```bash
tdxman capital-flow 600519.SH --format table
```

无日期或历史分页选项。CLI 调用 `MacClient.get_capital_flow(market, code)`，读取单只证券当日资金流快照；服务端无数据时 API 返回空表。常见列包含 `date`、主力/小单/中单/大单的流入、流出、净额，具体字段以 JSON 返回为准。它不同于按日历史资金流 `tdxman fund-flow`；不把交易时段内结果当作终日定稿。CLI：`src/tdxman/cli/cmd_capital.py`；API：`src/tdxman/mac/client.py#get_capital_flow`；列说明：[项目 README](../../../../../README.md)「资金流向」。

[CLI 总入口](../../README.md) · [命令索引](../../commands.md)
