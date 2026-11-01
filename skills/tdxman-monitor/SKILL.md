---
name: tdxman-monitor
description: 使用 tdxman CLI 查询集合竞价、当日资金流向、市场异动、全市场涨跌统计和个股特征快照；适用于当前状态监测。
---

# tdxman-monitor

这些命令主要返回当前服务器可见状态；先确认用户需要的时间口径，再选命令。历史日资金流向见 [tdxman-financials](../tdxman-financials/SKILL.md)。

| 需要 | 命令参考 |
| --- | --- |
| 单只证券集合竞价 | [auction](references/auction.md) |
| 当日资金流向快照 | [capital-flow](references/capital-flow.md) |
| 指定市场异动记录 | [unusual](references/unusual.md) |
| A 股全市场涨跌家数等统计 | [market-stat](references/market-stat.md) |
| 个股特征或 BaoStock 上市基础资料 | [symbol-info](references/symbol-info.md) |

读取对应 reference 后执行目标命令 `--help`。共用标的格式、JSON 输出和文件规则见 [tdxman-shared](../tdxman-shared/SKILL.md)。
