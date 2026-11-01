---
name: tdxman-shared
description: 使用 tdxman CLI 检查版本、市场代码、服务器状态和本机服务器排名，并理解共用标的格式及输出规则；不用于具体行情或数据池维护。
---

# tdxman-shared

`tdxman` 以 `600519.SH`、`000001.SZ`、`430047.BJ` 标识 A 股证券，也接受 `SH 600519` 等兼容写法。显式市场不会按代码前缀自动纠正。先用 [markets](references/markets.md) 识别市场；港股、期货等代码用 `tdxman ex markets`。

数据命令默认 JSON 记录数组；`--format table` 终端为网格表，写文件时为 `.md`；CSV 为 `.csv`。`--output` 路径没有后缀时当作目录，并按标的代码生成文件；带后缀的文件名须与格式匹配。`--fields basic|all` 只影响表格列。大结果先落盘，汇报行数、时间范围和来源；空数组不等于已确认休市。`version`、`bestip` 有各自的纯文本输出契约。

| 需要 | 命令参考 |
| --- | --- |
| 查看 CLI 版本 | [version](references/version.md) |
| A 股市场代码 | [markets](references/markets.md) |
| 测试标准/MAC 服务器延迟 | [ping](references/ping.md) |
| 验证服务器并写本机排名 | [bestip](references/bestip.md) |
| 查询服务器交易时段 | [server-info](references/server-info.md) |

CLI 没有 `capabilities` 或 `schema` 子命令。首次使用或版本变化时执行 `tdxman --help`、目标命令 `--help`；接口映射以命令实现及 [README Python API](../../README.md#python-api) 为准。`aspool` 是另一套本地数据池 CLI。
