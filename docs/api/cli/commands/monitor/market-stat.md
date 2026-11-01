# `tdxman market-stat`

```bash
tdxman market-stat --format table
```

无历史、`--count` 或日期选项。CLI 用 `MacClient.get_stock_quotes` 同时获取上海 `880005`、`880001`、`880006`，再转换成全市场涨跌统计：上涨、下跌、平盘、总家数、成交额/量、总市值、涨跌停家数。`suspended_count = max(0, total - up - down - neutral)` 是残差估算，不是服务端独立停牌字段；`total_market_cap` 依项目既有约定由 `880001.close × 1e10` 换算。`query_time` 是本机上海时间，不是源数据时间。三条报价缺失或字段无效会报错，不以零补齐。

不要把 `docs/api/signatures.md` 的 `TdxClient.get_market_stat()` 当成此 CLI 的当前实现；该 API 走标准协议，CLI 走 MAC 报价和本地转换。CLI：`src/tdxman/cli/cmd_monitor.py`；API：`src/tdxman/mac/client.py#get_stock_quotes`；口径：[项目 README](../../../../../README.md)「资金 / 监控」。

[CLI 总入口](../../README.md) · [命令索引](../../commands.md)
