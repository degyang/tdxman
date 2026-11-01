---
name: tdxman-ex
description: 使用 tdxman ex CLI 发现扩展市场并查询港股、美股、期货和扩展指数的商品目录、报价、K 线及分时。
---

# tdxman-ex

扩展市场使用独立 `MARKET CODE` 参数，不使用 A 股 `CODE.SH` 格式。先查询 [ex markets](references/markets.md) 得到市场名称，再用 [ex quote-list](references/quote-list.md) 发现代码；CLI 不自动从代码推断扩展市场。

| 需要 | 命令参考 |
| --- | --- |
| 扩展市场代码列表 | [markets](references/markets.md) |
| 一个市场的商品/指数目录 | [quote-list](references/quote-list.md) |
| 单个商品报价 | [quote](references/quote.md) |
| 单个商品历史 K 线 | [kline](references/kline.md) |
| 单日分时 | [tick](references/tick.md) |

执行前读对应 reference 和目标命令 `--help`。本命令组通过 MAC EX 端点读取；共用输出规则见 [tdxman-shared](../tdxman-shared/SKILL.md)。沪深标准指数仍用普通 [kline](../tdxman-market/references/kline.md)。
