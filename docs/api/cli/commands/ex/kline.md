# `tdxman ex kline`

```bash
tdxman ex kline HK_MAIN_BOARD 00700 --period DAILY --count 30 --format table
tdxman ex kline CSI_INDEX 000300 --period DAILY --count 120 --format json --output output/000300-ex.json
```

`MARKET CODE` 为扩展市场及该市场内代码。`--period` 默认 `DAILY`，解析器支持 `1MIN`、`5MIN`、`15MIN`、`30MIN`、`60MIN`、`WEEKLY`、`MONTHLY` 等周期，实际可用性还受该市场服务端限制；`--count` 默认 800，`--start` 默认 0，表示距最新记录的偏移；`--adjust` 默认 `NONE`，可选 `QFQ`、`HFQ`，应先确认目标资产是否有复权含义。

CLI 调用 `MacExClient.goods_kline(market, code, period, start, count, adjust)`，一次发出该 `count` 请求，**不采用普通股票 MAC API 的 700 根自动分页逻辑**；长历史分段时按实际返回长度调整 `--start` 并去重。扩展指数先用 `ex quote-list` 取代码；沪深标准指数用普通 `tdxman kline`。CLI：`src/tdxman/cli/cmd_ex.py`；API：`src/tdxman/ex/mac_client.py#goods_kline`；索引路由：[项目 README](../../../../../README.md)「指数类别与发现方式」。

[CLI 总入口](../../README.md) · [命令索引](../../commands.md)
