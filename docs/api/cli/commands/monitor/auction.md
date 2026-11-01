# `tdxman auction`

```bash
tdxman auction 000001.SZ --format table
```

必须显式指定市场，兼容 `SZ 000001`。无日期或分页参数；CLI 调用 `MacClient.get_auction(market, code)`，返回当前服务器可取的单只标的集合竞价数据。无记录或空数组不能直接证明该标的未交易，应检查查询时间和服务器状态。需要正常实时快照用 `quote`，不是把竞价结果当收盘价。CLI：`src/tdxman/cli/cmd_auction.py`；API：`src/tdxman/mac/client.py#get_auction`；示例：[项目 README](../../../../../README.md)「资金 / 监控」。

[CLI 总入口](../../README.md) · [命令索引](../../commands.md)
