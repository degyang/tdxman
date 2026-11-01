# `tdxman tick`

```bash
tdxman tick 600519.SH --format table
tdxman tick 600519.SH --date 20250115 --format json --output output/600519.json
tdxman tick 600519.SH --days 5 --format csv --output output/600519.csv
```

标的用 `CODE.MARKET` 或 `MARKET CODE`；`--date` 是 `YYYYMMDD` 整数，缺省查询当前日期。`--days` 在 CLI 只接受 `1`（默认）或 `5`：一天调用 `MacClient.get_tick_chart(market, code, date)`，五天调用 `get_tick_charts(..., date=date, days=5)`；Python API 虽允许其他天数，CLI 没有开放。多日请求的日期作为起始/锚定日期传给 API，不保证每个自然日都有交易数据。返回是分时图采样，逐笔成交另用 `transaction`。CLI：`src/tdxman/cli/cmd_tick.py`；API：`src/tdxman/mac/client.py#get_tick_chart,get_tick_charts`；文档：`README.md`「分时」。
