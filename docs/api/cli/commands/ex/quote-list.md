# `tdxman ex quote-list`

```bash
tdxman ex quote-list HK_MAIN_BOARD --count 100 --format table
tdxman ex quote-list CSI_INDEX --count 600 --format json --output output/csi-index.json
```

`MARKET` 用 [ex markets](markets.md) 返回的市场名；`--count` 默认 600，`--start` 默认 0，是该市场商品目录中的偏移。CLI 调用 `MacExClient.goods_list(market, start, count)`：先定位市场在扩展商品全集中的区间，再按市场过滤。这是**商品目录**，不是实时价格列表；单只价格再用 `ex quote`。分页时增大 `--start` 并检查实际返回条数及重复代码。大目录用 `--output` 保存。CLI：`src/tdxman/cli/cmd_ex.py`；API：`src/tdxman/ex/mac_client.py#goods_list`；示例：[项目 README](../../../../../README.md)「扩展市场」。

[CLI 总入口](../../README.md) · [命令索引](../../commands.md)
