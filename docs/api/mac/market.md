# MAC 行情：报价与时间序列

[MAC 入口](README.md) · [参数签名](../signatures.md)

| 方法 | 输入与用途 | 结果/限制 |
| --- | --- | --- |
| `get_stock_quotes(stocks, fields=None)` | `(market, code)` 列表；可指定字段集合 | 批量报价 DataFrame；单请求最多约 80 只，检查 `attrs` 的缺失信息 |
| `get_stock_quotes_list(category, start=0, count=80, ...)` | `Category` 市场或板块分类，排序字段/方向、过滤标志及字段；`count` 1–100000、`start` 0–100000 | 排序后的报价页；自动按最多 80 只分页 |
| `get_stock_kline(market, code, period=Period.DAILY, start=0, count=800, times=1, adjust=Adjust.NONE)` | K 线；`times` 用于多分钟/多日/多秒周期 | 自动以约 700 条分页获取，请检查返回行数和首尾日期 |
| `get_minute_bars(market, code, *, date, since=None, max_pages=4, page_size=240, asset_type='stock')` | 特定日期的分钟线，可从 `since` 起筛选 | DataFrame；`max_pages` 1–16、`page_size` 1–700，页数限制可能截断目标区间 |
| `get_tick_chart(market, code, date=None)` | 单日分时，日期使用 `YYYYMMDD` 整数或 `None` | 分时 DataFrame |
| `get_tick_charts(market, code, date=None, days=5)` | 连续若干日分时 | 多日 DataFrame |
| `get_chart_sampling(market, code)` | 图表采样数据 | 采样 DataFrame |
| `get_transactions(market, code, count=2000, start=0, date=None)` | 逐笔；`date` 为 `YYYYMMDD` 整数或 `None` | 按约 1000 条分页读取 |
| `get_symbol_info(market, code)` | 证券概况 | 单证券属性 DataFrame |

`market` 接受整数市场码，A 股通常传 `Market.SH/SZ/BJ`；`code` 是证券代码字符串。`fields` 的预设、字段位和列名见[字段表](fields.md)；未提供时使用客户端默认字段。结果列取决于请求字段及协议返回。批量报价的 `attrs` 包含 `requested_count`、`returned_count`、`complete`、`missing_identities`、`missing_fields`，还声明价格/量单位随资产变化；报价列表的 `attrs` 包含 `start`、`next_offset`、`source_exhausted`、`truncated`。先检查这些元信息再用于覆盖统计，不要按 CLI 的两位小数格式解释原始数值。

`get_tick_chart` 的记录列来自 `MacTick`（时间、价格、均价、量、动量），另加 `date`、`pre_close`、`datetime`。`attrs` 的 `source_date` 与 `date_verified` 标记服务器是否明确提供日期；请求历史日期时若服务端返回另一日期会抛 `TdxDecodeError`。该分时接口不提供完整 OHLC 与成交额，不能直接当作一分钟 K 线。完整字段索引见[模型字段](../schema-fields.md)。

`get_minute_bars` 的 `date` 可用 `datetime.date`、`YYYYMMDD` 整数或 ISO 日期字符串；`since` 是同一天的无时区 `datetime` 或 ISO 时间戳。`asset_type` 只接受 `stock`、`index`、`etf`。结果列为 `datetime/open/high/low/close/amount`，股票/ETF 另有 `vol`；`attrs` 记录实际首末时间、请求页数、是否覆盖窗口起点及是否耗尽预算。它不会推断分钟是否连续或当日是否收盘。

```python
from tdxman import Category, MacClient, Market, Period, SortOrder, SortType

with MacClient.from_best_host() as client:
    leaders = client.get_stock_quotes_list(Category.A, count=80,
                                           sort_type=SortType.CHANGE_PCT,
                                           sort_order=SortOrder.DESC)
    bars = client.get_stock_kline(Market.SZ, "000001", Period.DAILY, count=30)
    print(leaders.attrs, bars.tail(2))
```

`Period.MINS`、`DAYS`、`SECONDS` 与 `times` 组合使用；普通日线传 `Period.DAILY`。复权枚举 `Adjust.NONE/QFQ/HFQ`。更多枚举见[类型与字段](../models.md)。源码：`src/tdxman/mac/client.py`、`mac/commands/symbol_quotes.py`、`symbol_bar.py`、`symbol_transaction.py`、`src/tdxman/mac/minute_window.py`。
