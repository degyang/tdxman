# 标准行情：目录与市场数据

[标准行情](README.md) · [精确签名](../signatures.md)

下表中的 `market` 是 `Market`（`SZ=0`、`SH=1`、`BJ=2`），`code` 是不带交易所后缀的字符串。除 `get_security_count` 与 `get_price_limits` 外，这组方法返回 DataFrame。异步客户端同名方法的参数和结果一致。

| 方法 | 主要参数与用途 | 返回要点 |
| --- | --- | --- |
| `get_security_count(market)` | 证券目录条数 | 整数 |
| `get_security_list(market, start)` | 从目录偏移量开始读取一页 | 证券代码、名称等目录列；页大小由协议决定 |
| `get_security_list_all(pages='all')` | 合并沪深 A 股目录并尝试挂载行业；整数表示每个市场最多读取的页数 | `all` 结果缓存本地；失败页会跳过，行业配置失败时相关字段为空，使用前检查覆盖 |
| `get_security_quotes(stocks)` | `[(Market.SH, '600519'), ...]` 批量报价；一次 1–80 只 | 价格、五档买卖盘、量额、服务器时间等；未知字段保留为 `unknown_*` |
| `get_price_limits(market, code, name, pre_close)` | 根据证券身份与昨收计算涨跌停价 | `(上限, 下限)`，无法确定时元素可为 `None` |
| `get_security_bars(market, code, category, start, count=800)` | 证券 K 线，`KlineCategory` 周期 | 日期、OHLC、量额；单次协议查询约 800 条 |
| `get_index_bars(market, code, category, start, count=800)` | 指数 K 线 | 与证券 K 线相同的形态，需用指数代码 |
| `get_minute_time_data(market, code)` | 当日分时 | 时间、价格、成交等分钟序列 |
| `get_history_minute_time_data(market, code, date)` | 指定历史交易日分时 | `date` 为 `YYYYMMDD` 整数 |
| `get_transaction_data(market, code, start, count=800)` | 当日逐笔 | `start` 从最新记录向前偏移 |
| `get_history_transaction_data(market, code, date, start, count=800)` | 历史逐笔 | `date` 为 `YYYYMMDD` 整数 |
| `get_xdxr_info(market, code)` | 除权除息与股本事件 | 事件日期、类别和对应事件字段 |
| `get_market_stat()` | 市场统计 | 汇总统计行，非单只股票行情 |
| `get_fund_flow(market, code)` | 近期资金流 | 指标依服务器数据而定 |
| `get_history_fund_flow(market, code, start, count)` | 历史资金流分页 | `start` 是偏移量 |

`get_security_bars` 和 `get_index_bars` 的日线及以上周期用 `date` 列，分钟周期用 `datetime`；都由原始年月日时分字段合成。`get_index_bars` 遇到连接或解码异常会尝试其他已配置的标准服务器，最终仍可能抛错。

`KlineCategory` 的 `DAY=4`、`WEEK=5`、`MONTH=6`、`MIN_1=7` 等值见[类型与字段](../models.md)。`get_security_list_all` 只构建沪深 A 股目录，不等价于逐市场全部品种。日期、偏移和数量不要混用；完整参数见[签名总表](../signatures.md)。

`get_security_list_all(pages='all')` 使用 `~/.tdxman/cache/security_list_all.json`，缓存有效期约一天；整数 `pages` 不使用该缓存。缓存或分页结果需自行检查证券覆盖，尤其是在某一页请求失败后。

`get_price_limits()` 按当前交易状态及 `pre_close` 计算；对上市初期标的，会请求少量日 K 根数估计已上市交易日。它没有历史交易日期参数，无法据此回填历史涨跌停状态。无法确认规则或无涨跌幅时元素可为 `None`；需要显式三态依据时见[规则工具](../utilities.md)。

`get_market_stat()` 从上海 `880005`、`880001`、`880006` 三只统计标的组装 `MarketStat`：上涨、下跌、平盘、总家数、成交量额、市值与涨跌停家数。`suspended_count` 是 `max(0, total - up - down - neutral)` 残差估计；`query_time` 是本机上海时间。缺少任一标的时会报错，不会补零。CLI 同名命令使用 MAC 报价及本地转换，见[命令页](../cli/commands/monitor/market-stat.md)。

`get_fund_flow()` 基于当日 L1 逐笔记录分类计算；`get_history_fund_flow()` 优先读取 Category 22 历史资金流，服务端失败或返回空时会按日 K 日期和历史逐笔重算，必要时用 MAC 日 K 确定日期。不同路径的来源和耗时可能不同，核对日期与返回行数。资金流模型字段见[字段索引](../schema-fields.md)。

```python
from tdxman import KlineCategory, Market, TdxClient

with TdxClient.from_best_host() as client:
    quotes = client.get_security_quotes([(Market.SH, "600519"), (Market.SZ, "000001")])
    bars = client.get_security_bars(Market.SH, "600519", KlineCategory.DAY, 0, 20)
    events = client.get_xdxr_info(Market.SH, "600519")
```

源码：`src/tdxman/client.py`、`src/tdxman/commands/`、`src/tdxman/codec/price_rules.py`。实时与历史可用范围由所连服务器决定，空表需要结合目录、时间和请求区间判断。
