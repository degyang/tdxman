# MAC 行情 API（7709）

[总入口](../README.md) · [精确签名](../signatures.md)

`MacClient` 与 `AsyncMacClient` 提供相同的市场方法，主要返回 `pandas.DataFrame`。异步版使用 `async with` 与 `await`；连接方法见[通用契约](../connections.md)。

- [报价、K 线、分时和逐笔](market.md)
- [MAC 报价字段位图](fields.md)：字段预设、每个位值及输出列名。
- [板块、竞价与监控](boards.md)
- [服务器和文件接口](files.md)

```python
from tdxman import Adjust, MacClient, Market, Period

with MacClient.from_best_host() as client:
    bars = client.get_stock_kline(Market.SH, "600519", Period.DAILY,
                                  count=100, adjust=Adjust.QFQ)
```

MAC 的 `Period` 和标准协议的 `KlineCategory` 值有交集，但请传对应枚举。MAC 查询可能在 `DataFrame.attrs` 中保留完整性和缺失字段信息，见[连接与输出](../connections.md)。源码：`src/tdxman/mac/client.py` 与 `src/tdxman/mac/commands/`。
