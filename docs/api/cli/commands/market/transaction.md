# `tdxman transaction`

```bash
tdxman transaction 600519.SH --count 100 --format table
tdxman transaction 600519.SH --date 20250115 --count 2000 --format json --output output/600519.json
```

`--date` 为 `YYYYMMDD` 整数，默认今天；`--count` 默认 2000；`--start` 默认 0，是距最新记录的偏移，不是日期。CLI 调用 `MacClient.get_transactions(market, code, count, start, date)`，客户端按协议页自动分页。`docs/api/signatures.md` 的标准协议 `get_transaction_data` / `get_history_transaction_data` 是其他 API，单页上限 800，不能据此把 CLI `--count` 固定为 800。

逐笔方向字段可能含中性或集合竞价，不宜把所有成交量直接解释为主动买入/卖出。大请求落盘后检查返回日期、条数与分页重叠。CLI：`src/tdxman/cli/cmd_transaction.py`；API：`src/tdxman/mac/client.py#get_transactions`；字段：`src/tdxman/models/timeseries.py` 的逐笔模型。

[CLI 总入口](../../README.md) · [命令索引](../../commands.md)
