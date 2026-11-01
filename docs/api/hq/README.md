# 标准行情 API（7709）

[总入口](../README.md) · [方法签名](../signatures.md)

`TdxClient` 与 `AsyncTdxClient` 提供同名查询；异步版在调用前加 `await`。两者主要返回 `pandas.DataFrame`，连接方式见[连接契约](../connections.md)。

- [证券目录、行情与交易数据](market.md)：证券列表、报价、K 线、分时、逐笔、除权、资金流及市场统计。
- [公司资料与文件](finance-files.md)：财务快照、公司信息、板块、报表及历史财务文件。

```python
from tdxman import KlineCategory, Market, TdxClient

with TdxClient.from_best_host() as client:
    bars = client.get_security_bars(Market.SH, "600519", KlineCategory.DAY, 0, 30)
    print(bars[["datetime", "open", "close"]].tail())
```

`get_security_bars` 的参数顺序是市场、代码、周期、偏移、数量；这和 [tdxpy 标准行情](https://tdxpy.readthedocs.io/zh-cn/latest/api/hq/)的部分示例不同。这里的方法及列名以 `src/tdxman/client.py` 和 `src/tdxman/commands/` 为准。
