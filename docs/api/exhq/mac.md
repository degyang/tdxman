# MAC 扩展行情

[扩展行情](README.md) · [精确签名](../signatures.md)

`MacExClient` 与 `AsyncMacExClient` 主要返回 DataFrame；扩展市场编号可用 `ExMarket`，例如 `ExMarket.HK_MAIN_BOARD`。日期参数 `query_date` 是 `datetime.date` 或 `None`，与标准扩展接口的 `YYYYMMDD` 整数不同。

| 方法 | 参数及语义 | 返回/限制 |
| --- | --- | --- |
| `goods_count(market=None)` | 可指定市场统计商品数 | 整数 |
| `goods_list(market, start=0, count=600)` | 商品目录偏移分页 | DataFrame |
| `goods_quotes(stocks, fields=None)` | `(market, code)` 列表批量报价，单次最多 80 只 | DataFrame |
| `goods_quotes_list(market, start=0, count=100, sort_type=SortType.CODE, sort_order=SortOrder.NONE)` | 先取市场商品目录，再批量补报价；`sort_type` / `sort_order` 当前预留、未实际排序 | DataFrame；实际最多返回约 80 只 |
| `goods_kline(market, code, period=Period.DAILY, start=0, count=800, adjust=Adjust.NONE)` | 商品 K 线 | DataFrame；单请求，不自动跨页 |
| `goods_tick_chart(market, code, query_date=None)` | 指定日期分时 | DataFrame |
| `goods_chart_sampling(market, code)` | 图表采样 | DataFrame |
| `goods_transaction(market, code, query_date=None, start=0, count=2000)` | 当日或指定日期逐笔 | DataFrame |

```python
from datetime import date
from tdxman import ExMarket, MacExClient, Period

with MacExClient.from_best_host() as client:
    market = ExMarket.HK_MAIN_BOARD
    catalog = client.goods_list(market, count=100)
    # Use a code returned by the selected market before requesting bars.
    if not catalog.empty:
        print(catalog.head())
```

服务端市场目录及商品代码可能不同；先查看目录，再调用单商品接口。`goods_kline` 的 `start` 是偏移量，`count` 超出服务端单页能力时不会自动补齐。CLI `tdxman ex tick --date` 当前将日期作为整数传给要求 `date` 对象的接口，会报错；Python API 请传 `datetime.date`。源码：`src/tdxman/ex/mac_client.py`、`src/tdxman/mac/enums.py`、`src/tdxman/cli/cmd_ex.py`。
