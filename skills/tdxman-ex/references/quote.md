# `tdxman ex quote`

```bash
tdxman ex quote HK_MAIN_BOARD 00700 --format table
tdxman ex quote US_STOCK AAPL --format json
```

`MARKET` 与 `CODE` 都必填；代码先从对应 `ex quote-list` 核对。CLI 调用 `MacExClient.goods_quotes([(market, code)])`，得到一只扩展市场商品的当前报价。Python API 可批量不超过 80 只，但本 CLI 命令一次只接受一个 `CODE`。没有日期、历史或排序选项；历史价格用 `ex kline`。CLI：`src/tdxman/cli/cmd_ex.py`；API：`src/tdxman/ex/mac_client.py#goods_quotes`。
