# `tdxman quote`

```bash
tdxman quote "000001.SZ,600519.SH" --format table
tdxman quote 600519.SH --source baostock --count 30 --format json --output output/600519.json
```

`STOCKS` 是逗号分隔的显式市场标的，兼容 `"SZ 000001,SH 600519"`。默认 `--source tdx` 调用 `MacClient.get_stock_quotes(stocks)` 返回当前报价；底层单次最多 80 个不同标的，CLI 不自动分批。`--count`、`--start-date`、`--end-date` 只允许在 `--source baostock`，日期格式 `YYYY-MM-DD`。

BaoStock 路径逐只调用 `BaostockClient.get_daily(..., count=30 或指定值, start=..., end=...)`，返回沪深 A 股未复权历史日线和明确报告的停牌状态，不是实时快照；不支持 BJ、指数或其他资产。指定日期区间仍只返回区间内最近 `count` 条。多标的输出到文件时 `--output` 必须为目录，CLI 按代码拆分文件；如不同市场有同一六位代码，行会合并到同一个代码文件，应按 `market` 再区分。空结果检查来源与请求窗口，勿直接推断休市。

CLI：`src/tdxman/cli/cmd_quote.py`；API：`src/tdxman/mac/client.py#get_stock_quotes`、`src/tdxman/baostock.py#get_daily`；语义：`README.md`「行情」。
