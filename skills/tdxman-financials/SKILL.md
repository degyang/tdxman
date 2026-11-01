---
name: tdxman-financials
description: 使用 tdxman CLI 查询最新财务摘要、除权除息事件、7615 F10 公司资料及个股历史日资金流；适用于单只 A 股的基础财务和资金数据。
---

# tdxman-financials

按需要选择命令，不把最新快照误当指定历史时点的财务事实：

| 需要 | 命令参考 |
| --- | --- |
| 最新财务结构化摘要 | [finance](references/finance.md) |
| 除权除息和股本变动事件 | [xdxr](references/xdxr.md) |
| 公司概况、报表和其他 F10 栏目 | [f10](references/f10.md) |
| 按日汇总的历史资金流向 | [fund-flow](references/fund-flow.md) |

当日资金快照另用 [capital-flow](../tdxman-monitor/references/capital-flow.md)。共用标的及输出规则见 [tdxman-shared](../tdxman-shared/SKILL.md)；执行前读对应 reference 并核对目标 `--help`。
