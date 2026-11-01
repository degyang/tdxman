# `tdxman fund-flow`

```bash
tdxman fund-flow 600519.SH --start 0 --count 20 --closed-only --format table
```

`--start` 默认 0，是从最新记录往前的偏移；`--count` 默认 30；`--closed-only` 排除运行机器本地日期当天可能未完整的记录。CLI 调用标准协议 `TdxClient.get_history_fund_flow(market, code, start, count)`：优先读历史资金流直连接口；无直接结果时按日 K 日期和历史逐笔成交重算，日 K 解码失败时可能借 MAC 日线确定日期。后备路径通常更慢，不能仅凭返回行数断言与直连完全同质。

仅用于个股，指数逐笔方向不适合推导方向性资金流。输出按 `super_*`、`large_*`、`medium_*`、`small_*` 分级，金额单位为元；核对实际日期，`--start` 不是日期。CLI：`src/tdxman/cli/cmd_finance.py`；API：`src/tdxman/client.py#get_history_fund_flow`；字段：`src/tdxman/models/stats.py` 的资金流模型。

[CLI 总入口](../../README.md) · [命令索引](../../commands.md)
