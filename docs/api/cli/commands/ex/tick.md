# `tdxman ex tick`

```bash
tdxman ex tick HK_MAIN_BOARD 00700 --format table
```

`MARKET CODE` 为扩展市场与商品代码；缺省查询当前日。CLI 调用 `MacExClient.goods_tick_chart(market, code, query_date)`，API 期望 `datetime.date`，但当前 CLI 的 `--date` 解析为 `YYYYMMDD` 整数并原样传入；指定 `--date` 可能在请求构造时抛出属性错误。需要指定历史日期时，先在 Python API 中传 `datetime.date`，或待 CLI 修复后再使用此选项。此已知限制依据 `src/tdxman/cli/cmd_ex.py`、`src/tdxman/ex/mac_client.py#goods_tick_chart` 与 `src/tdxman/mac/commands/symbol_tick_chart.py`。空结果仍须结合市场交易时间判断。

[CLI 总入口](../../README.md) · [命令索引](../../commands.md)
