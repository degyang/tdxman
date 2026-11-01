---
name: tdxman-market
description: 使用 tdxman CLI 查询 A 股报价、K 线、分时、逐笔成交及已观察交易日；适用于股票和沪深标准指数行情取数。
---

# tdxman-market

按需求读取对应命令参考，并在执行前用该命令的 `--help` 核对当前选项。共用的标的格式、输出与服务器规则见 [tdxman-shared](../tdxman-shared/SKILL.md)。

| 需要 | 命令参考 |
| --- | --- |
| 单只股票或沪深标准指数历史 K 线 | [kline](references/kline.md) |
| 单只/少量标的实时快照，或 BaoStock 历史日线补充 | [quote](references/quote.md) |
| 市场分类排序报价及标准指数报价目录 | [quote-list](references/quote-list.md) |
| 单日或五日分时图 | [tick](references/tick.md) |
| 当日或指定日逐笔成交 | [transaction](references/transaction.md) |
| 从上证指数日线判断已观察交易日 | [workdays](references/workdays.md) |

普通股票和沪深标准指数 K 线走不同 API；扩展市场指数、港股、美股及期货用 [tdxman-ex](../tdxman-ex/SKILL.md)。本地 vipdoc 用 [tdxman-offline](../tdxman-offline/SKILL.md)。
