# 标准扩展行情

[扩展行情](README.md) · [精确签名](../signatures.md)

`ExTdxClient` 和 `AsyncExTdxClient` 有同名查询。这里**不直接返回 DataFrame**；需自行把模型列表转换为表格。`market` 使用扩展市场代码，不能把普通沪深 `Market` 直接用于期货或港股。先调用 `get_markets()` 读取服务器市场目录。

| 方法 | 参数及语义 | 返回 |
| --- | --- | --- |
| `get_markets()` | 服务端扩展市场目录 | `list[ExMarketInfo]` |
| `get_instrument_count()` | 合约总数 | `int` |
| `get_instrument_info(start, count=100)` | 合约目录偏移分页 | `list[ExInstrumentInfo]` |
| `get_instrument_quote(market, code)` | 单合约报价 | `ExInstrumentQuote` 或 `None` |
| `get_instrument_quote_list(market, category, start=0, count=80)` | 分类报价分页；当前解析器只支持 `category=2` 港股或 `3` 期货 | `list[OrderedDict]`；其他类别抛 `TdxCommandError` |
| `get_instrument_bars(category, market, code, start=0, count=700)` | K 线周期、市场、合约、偏移 | `list[ExInstrumentBar]` |
| `get_history_instrument_bars_range(market, code, start_date, end_date)` | 日期范围；整数 `YYYYMMDD` | `list[ExInstrumentBar]` |
| `get_minute_time_data(market, code)` | 当日分时 | `list[ExMinuteBar]` |
| `get_history_minute_time_data(market, code, date)` | 历史分时，日期整数 `YYYYMMDD` | `list[ExMinuteBar]` |
| `get_transaction_data(market, code, start=0, count=1800)` | 当日逐笔 | `list[ExTransactionRecord]` |
| `get_history_transaction_data(market, code, date, start=0, count=1800)` | 历史逐笔 | `list[ExTransactionRecord]` |

```python
from dataclasses import asdict
from tdxman import ExTdxClient

with ExTdxClient.from_best_host() as client:
    markets = client.get_markets()
    instruments = client.get_instrument_info(0, 100)
    rows = [asdict(item) for item in instruments]
```

`get_instrument_bars` 的 `category` 在最前面，与标准股票 K 线的参数顺序不同；查询历史范围时端点数据可不完整。扩展市场码可参考[类型与字段](../models.md)，但实际可用范围以 `get_markets()` 为准。源码：`src/tdxman/ex/client.py`、`src/tdxman/ex/commands/`、`src/tdxman/ex/models.py`。
