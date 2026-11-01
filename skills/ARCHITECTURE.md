# tdxman CLI Skills 目录架构

本目录参照 `hithink-finance-cli/skills` 的「业务域 `SKILL.md` + 逐命令 `references/`」形式。每个 reference 同时核对 CLI 参数和对应 Python API 的行为；以当前命令 `--help` 与源码为准，README 和 API 文档提供语义说明。`tdxman` 没有 `capabilities` / `schema` 命令，不照搬参考项目的调用方式。

样例获批后，已补齐当前 `tdxman` 根命令和 `ex` 子命令的业务域入口及逐命令 reference。`aspool` 是独立的数据池维护 CLI，不属于本轮 `tdxman` 查询 skill。

| 目录 | 拟覆盖命令 | 核对重点 |
| --- | --- | --- |
| `tdxman-shared/` | `version`, `ping`, `bestip`, `markets`, `server-info`；共用输出与连接规则 | JSON/CSV/表格、文件输出、市场代码、服务器选择 |
| `tdxman-market/` | `kline`, `quote`, `quote-list`, `tick`, `transaction`, `workdays` | 股票和标准指数、实时与历史、偏移分页、数据源 |
| `tdxman-board/` | `board-list`, `board-members`, `belong-board` | 板块、标准指数目录和实时成分的区别 |
| `tdxman-financials/` | `finance`, `xdxr`, `f10`, `fund-flow` | 最新摘要、权息事件、7615 F10、历史资金流 |
| `tdxman-monitor/` | `auction`, `capital-flow`, `unusual`, `market-stat`, `symbol-info` | 集合竞价、当日资金、异动及全市场快照口径 |
| `tdxman-ex/` | `ex markets`, `ex quote`, `ex quote-list`, `ex kline`, `ex tick` | 扩展市场代码与 A 股命令分流 |
| `tdxman-offline/` | `offline` | 本地 vipdoc 配置、周期和在线时效边界 |

逐命令 reference 的写法以 [kline](tdxman-market/references/kline.md) 为例：可执行命令、参数选择、CLI 到 API 的路径与差异、分页/输出、错误与验收；不把 API 签名误作 CLI 的直接参数。具体命令如果没有分页、文件输出或独立 Python API，就只记录实际存在的能力。

`manifest.json` 按参考项目记录当前 CLI 版本和各 skill Markdown 文件的 SHA-256，供目录发布或同步前核对；它不改变 CLI 行为。
