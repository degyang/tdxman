---
name: tdxman-board
description: 使用 tdxman CLI 查询通达信行业、概念、风格等板块目录、实时成分股及个股所属板块；也可发现沪深标准指数目录。
---

# tdxman-board

先区分板块/研究指数（常见 `881xxx`）和沪深标准指数。前者在 `board-list` 的 HY、GN 等分类；后者在 `--type ZS`。两类目录和实时成分不代表历史成分版本。

| 需要 | 命令参考 |
| --- | --- |
| 板块或标准指数目录 | [board-list](references/board-list.md) |
| 当前板块/支持的指数成分股报价 | [board-members](references/board-members.md) |
| 个股当前所属板块 | [belong-board](references/belong-board.md) |

执行前核对目标命令 `--help`，共用标的和输出规则见 [tdxman-shared](../tdxman-shared/SKILL.md)。K 线转 [tdxman-market](../tdxman-market/SKILL.md)，中证、国证等扩展市场指数转 [tdxman-ex](../tdxman-ex/SKILL.md)。
