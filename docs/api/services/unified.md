# 统一 MAC 客户端

[辅助服务](README.md) · [完整代理签名](../signatures.md)

`UnifiedTdxClient` / `AsyncUnifiedTdxClient` 把 [MAC A 股](../mac/README.md) `get_*` 方法转发给 `MacClient`，把 [MAC 扩展](../exhq/mac.md) `goods_*` 方法转发给 `MacExClient`。它不代理标准 `TdxClient` 或标准 `ExTdxClient`。进入上下文时连接 A 股服务，第一次调用 `goods_*` 时才连接扩展服务；退出时关闭已建立的连接。

```python
from tdxman import ExMarket, Market, Period, UnifiedTdxClient

with UnifiedTdxClient() as client:
    shares = client.get_stock_kline(Market.SH, "600519", Period.DAILY, count=10)
    # The extended-market code must come from the selected market catalog.
    goods = client.goods_list(ExMarket.HK_MAIN_BOARD, count=20)
```

代理的参数、返回类型和限制与目标方法相同，分别见 [MAC 行情](../mac/market.md)、[板块与监控](../mac/boards.md)、[MAC 扩展](../exhq/mac.md)。`get_minute_bars` 没有列在当前统一客户端公开代理中，调用时应直接使用 `MacClient`。源码：`src/tdxman/unified.py`。
