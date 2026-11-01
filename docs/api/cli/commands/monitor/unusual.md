# `tdxman unusual`

```bash
tdxman unusual SH --count 100 --format table
```

`MARKET` 是 `SH`、`SZ`、`BJ` 等市场代码，不是单只股票；`--count` 默认 600，`--start` 默认 0，是服务端记录偏移。CLI 调用 `MacClient.get_unusual(market, start, count)`，获取市场异动记录，没有指定历史日期的选项。需要较大批量时落盘并核对实际条数及事件时间，不能把未返回的记录解释为没有异动。CLI：`src/tdxman/cli/cmd_monitor.py`；API：`src/tdxman/mac/client.py#get_unusual`。

[CLI 总入口](../../README.md) · [命令索引](../../commands.md)
