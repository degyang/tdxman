# CLI 参数总表

[CLI 总入口](README.md) · [命令索引](commands.md)

下表从当前 Click 命令定义提取参数名、默认值和帮助文字；跨参数约束及业务语义仍以各[命令页](commands.md)为准。`ARG` 表示位置参数，`—` 表示无默认值。

| 命令 | 参数 | 默认值 | 类型 / 说明 |
| --- | --- | --- | --- |
| `ping` | `--timeout` | `5.0` | FLOAT；测速超时（秒） |
| `ping` | `-f, --format` | `json` | Choice(json, table, csv)；输出格式 |
| `ping` | `-o, --output` | `—` | PATH；写入文件或目录；目录内文件名默认为标的代码 |
| `ping` | `--fields` | `basic` | Choice(basic, all)；表格输出字段；JSON 和 CSV 始终包含全部字段 |
| `bestip` | `--protocol` | `all` | Choice(standard, mac, ex, all)； |
| `bestip` | `--timeout` | `3.0` | FLOAT ≥ 0.1； |
| `bestip` | `--limit` | `5` | INT ≥ 1； |
| `version` | — | — | 无命令参数；输出纯文本版本号 |
| `kline` | `SYMBOL_OR_MARKET (ARG)` | `—` | TEXT；必填 |
| `kline` | `CODE (ARG)` | `—` | TEXT；可选 |
| `kline` | `--period` | `DAILY` | TEXT；K线周期: DAILY/5MIN/15MIN/30MIN/60MIN/1MIN/WEEKLY/MONTHLY |
| `kline` | `--count` | `800` | INTEGER；K线数量 |
| `kline` | `--start` | `0` | INTEGER；起始偏移（0=最新） |
| `kline` | `--adjust` | `NONE` | TEXT；复权: NONE/QFQ/HFQ |
| `kline` | `--source` | `tdx` | Choice(tdx, baostock)； |
| `kline` | `-f, --format` | `json` | Choice(json, table, csv)；输出格式 |
| `kline` | `-o, --output` | `—` | PATH；写入文件或目录；目录内文件名默认为标的代码 |
| `kline` | `--fields` | `basic` | Choice(basic, all)；表格输出字段；JSON 和 CSV 始终包含全部字段 |
| `quote` | `STOCKS (ARG)` | `—` | TEXT；必填 |
| `quote` | `--source` | `tdx` | Choice(tdx, baostock)； |
| `quote` | `--count` | `—` | INT ≥ 1；baostock 每只股票的历史日线数量，默认 30；tdx 为实时快照。 |
| `quote` | `--start-date` | `—` | DATE；baostock 历史区间起点，YYYY-MM-DD。 |
| `quote` | `--end-date` | `—` | DATE；baostock 历史区间终点，YYYY-MM-DD。 |
| `quote` | `-f, --format` | `json` | Choice(json, table, csv)；输出格式 |
| `quote` | `-o, --output` | `—` | PATH；写入文件或目录；目录内文件名默认为标的代码 |
| `quote` | `--fields` | `basic` | Choice(basic, all)；表格输出字段；JSON 和 CSV 始终包含全部字段 |
| `quote-list` | `CATEGORY (ARG)` | `A` | TEXT；可选 |
| `quote-list` | `--count` | `80` | INTEGER；请求数量 |
| `quote-list` | `--sort` | `CHANGE_PCT` | TEXT；排序字段: CHANGE_PCT/CODE/PRICE/VOLUME/TOTAL_AMOUNT/TURNOVER_RATE |
| `quote-list` | `--order` | `DESC` | TEXT；排序方向: DESC/ASC |
| `quote-list` | `-f, --format` | `json` | Choice(json, table, csv)；输出格式 |
| `quote-list` | `-o, --output` | `—` | PATH；写入文件或目录；目录内文件名默认为标的代码 |
| `quote-list` | `--fields` | `basic` | Choice(basic, all)；表格输出字段；JSON 和 CSV 始终包含全部字段 |
| `tick` | `SYMBOL_OR_MARKET (ARG)` | `—` | TEXT；必填 |
| `tick` | `CODE (ARG)` | `—` | TEXT；可选 |
| `tick` | `--date` | `—` | INTEGER；日期 YYYYMMDD（默认今天） |
| `tick` | `--days` | `1` | Choice(1, 5)；交易日数量：1 或 5 |
| `tick` | `-f, --format` | `json` | Choice(json, table, csv)；输出格式 |
| `tick` | `-o, --output` | `—` | PATH；写入文件或目录；目录内文件名默认为标的代码 |
| `tick` | `--fields` | `basic` | Choice(basic, all)；表格输出字段；JSON 和 CSV 始终包含全部字段 |
| `transaction` | `SYMBOL_OR_MARKET (ARG)` | `—` | TEXT；必填 |
| `transaction` | `CODE (ARG)` | `—` | TEXT；可选 |
| `transaction` | `--count` | `2000` | INTEGER；请求数量 |
| `transaction` | `--start` | `0` | INTEGER；起始偏移 |
| `transaction` | `--date` | `—` | INTEGER；日期 YYYYMMDD（默认今天） |
| `transaction` | `-f, --format` | `json` | Choice(json, table, csv)；输出格式 |
| `transaction` | `-o, --output` | `—` | PATH；写入文件或目录；目录内文件名默认为标的代码 |
| `transaction` | `--fields` | `basic` | Choice(basic, all)；表格输出字段；JSON 和 CSV 始终包含全部字段 |
| `auction` | `SYMBOL_OR_MARKET (ARG)` | `—` | TEXT；必填 |
| `auction` | `CODE (ARG)` | `—` | TEXT；可选 |
| `auction` | `-f, --format` | `json` | Choice(json, table, csv)；输出格式 |
| `auction` | `-o, --output` | `—` | PATH；写入文件或目录；目录内文件名默认为标的代码 |
| `auction` | `--fields` | `basic` | Choice(basic, all)；表格输出字段；JSON 和 CSV 始终包含全部字段 |
| `board-list` | `--type` | `ALL` | TEXT；板块类型（见下方对照表） |
| `board-list` | `--count` | `10000` | INTEGER；请求数量 |
| `board-list` | `-f, --format` | `json` | Choice(json, table, csv)；输出格式 |
| `board-list` | `-o, --output` | `—` | PATH；写入文件或目录；目录内文件名默认为标的代码 |
| `board-list` | `--fields` | `basic` | Choice(basic, all)；表格输出字段；JSON 和 CSV 始终包含全部字段 |
| `board-members` | `BOARD_SYMBOL (ARG)` | `—` | TEXT；必填 |
| `board-members` | `--count` | `100000` | INTEGER；请求数量 |
| `board-members` | `--sort` | `CHANGE_PCT` | TEXT；排序字段: CHANGE_PCT/CODE/PRICE/VOLUME |
| `board-members` | `--order` | `DESC` | TEXT；排序方向: DESC/ASC |
| `board-members` | `-f, --format` | `json` | Choice(json, table, csv)；输出格式 |
| `board-members` | `-o, --output` | `—` | PATH；写入文件或目录；目录内文件名默认为标的代码 |
| `board-members` | `--fields` | `basic` | Choice(basic, all)；表格输出字段；JSON 和 CSV 始终包含全部字段 |
| `belong-board` | `SYMBOL_OR_MARKET (ARG)` | `—` | TEXT；必填 |
| `belong-board` | `CODE (ARG)` | `—` | TEXT；可选 |
| `belong-board` | `-f, --format` | `json` | Choice(json, table, csv)；输出格式 |
| `belong-board` | `-o, --output` | `—` | PATH；写入文件或目录；目录内文件名默认为标的代码 |
| `belong-board` | `--fields` | `basic` | Choice(basic, all)；表格输出字段；JSON 和 CSV 始终包含全部字段 |
| `capital-flow` | `SYMBOL_OR_MARKET (ARG)` | `—` | TEXT；必填 |
| `capital-flow` | `CODE (ARG)` | `—` | TEXT；可选 |
| `capital-flow` | `-f, --format` | `json` | Choice(json, table, csv)；输出格式 |
| `capital-flow` | `-o, --output` | `—` | PATH；写入文件或目录；目录内文件名默认为标的代码 |
| `capital-flow` | `--fields` | `basic` | Choice(basic, all)；表格输出字段；JSON 和 CSV 始终包含全部字段 |
| `unusual` | `MARKET (ARG)` | `—` | TEXT；必填 |
| `unusual` | `--count` | `600` | INTEGER；请求数量 |
| `unusual` | `--start` | `0` | INTEGER；起始偏移 |
| `unusual` | `-f, --format` | `json` | Choice(json, table, csv)；输出格式 |
| `unusual` | `-o, --output` | `—` | PATH；写入文件或目录；目录内文件名默认为标的代码 |
| `unusual` | `--fields` | `basic` | Choice(basic, all)；表格输出字段；JSON 和 CSV 始终包含全部字段 |
| `market-stat` | `-f, --format` | `json` | Choice(json, table, csv)；输出格式 |
| `market-stat` | `-o, --output` | `—` | PATH；写入文件或目录；目录内文件名默认为标的代码 |
| `market-stat` | `--fields` | `basic` | Choice(basic, all)；表格输出字段；JSON 和 CSV 始终包含全部字段 |
| `server-info` | `-f, --format` | `json` | Choice(json, table, csv)；输出格式 |
| `server-info` | `-o, --output` | `—` | PATH；写入文件或目录；目录内文件名默认为标的代码 |
| `server-info` | `--fields` | `basic` | Choice(basic, all)；表格输出字段；JSON 和 CSV 始终包含全部字段 |
| `symbol-info` | `SYMBOL_OR_MARKET (ARG)` | `—` | TEXT；必填 |
| `symbol-info` | `CODE (ARG)` | `—` | TEXT；可选 |
| `symbol-info` | `--source` | `tdx` | Choice(tdx, baostock)； |
| `symbol-info` | `-f, --format` | `json` | Choice(json, table, csv)；输出格式 |
| `symbol-info` | `-o, --output` | `—` | PATH；写入文件或目录；目录内文件名默认为标的代码 |
| `symbol-info` | `--fields` | `basic` | Choice(basic, all)；表格输出字段；JSON 和 CSV 始终包含全部字段 |
| `abnormal-list` | `--progress` | `false` | 显示连接与分页进度（标准错误） |
| `abnormal-list` | `MARKET (ARG)` | `ALL` | Choice(ALL, SH, SZ, BJ) |
| `abnormal-list` | `--type` | `all` | Choice(all, st, suspended)；异常类型 |
| `abnormal-list` | `--scope` | `all` | Choice(all, main, star, chinext, bse, cdr) |
| `abnormal-list` | `--code` | `—` | 可重复或逗号分隔；代码格式同 stock-codes |
| `abnormal-list` | `--name` | `—` | 可重复；简称精确匹配 |
| `abnormal-list` | `-f, --format` | `json` | Choice(json, table, csv) |
| `abnormal-list` | `-o, --output` | `—` | PATH；目录内默认文件名 abnormal-list |
| `abnormal-list` | `--fields` | `basic` | Choice(basic, all)；JSON/CSV 包含全部字段 |
| `stock-codes` | `--progress` | `false` | 显示连接与分页进度（标准错误） |
| `stock-codes` | `MARKET (ARG)` | `ALL` | Choice(ALL, SH, SZ, BJ)；可选 |
| `stock-codes` | `--scope` | `all` | Choice(all, main, star, chinext, bse, cdr)；股票范围 |
| `stock-codes` | `--code` | `—` | 可重复或逗号分隔；六位代码、CODE.MARKET 或市场前缀格式 |
| `stock-codes` | `--name` | `—` | 可重复；证券简称精确匹配 |
| `stock-codes` | `-f, --format` | `json` | Choice(json, table, csv)；输出格式 |
| `stock-codes` | `-o, --output` | `—` | PATH；文件或目录，目录内默认文件名 stock-codes |
| `stock-codes` | `--fields` | `basic` | Choice(basic, all)；表格字段；JSON/CSV 包含全部字段 |
| `markets` | `-f, --format` | `json` | Choice(json, table, csv)；输出格式 |
| `markets` | `-o, --output` | `—` | PATH；写入文件或目录；目录内文件名默认为标的代码 |
| `markets` | `--fields` | `basic` | Choice(basic, all)；表格输出字段；JSON 和 CSV 始终包含全部字段 |
| `finance` | `SYMBOL_OR_MARKET (ARG)` | `—` | TEXT；必填 |
| `finance` | `CODE (ARG)` | `—` | TEXT；可选 |
| `finance` | `-f, --format` | `json` | Choice(json, table, csv)；输出格式 |
| `finance` | `-o, --output` | `—` | PATH；写入文件或目录；目录内文件名默认为标的代码 |
| `finance` | `--fields` | `basic` | Choice(basic, all)；表格输出字段；JSON 和 CSV 始终包含全部字段 |
| `xdxr` | `SYMBOL_OR_MARKET (ARG)` | `—` | TEXT；必填 |
| `xdxr` | `CODE (ARG)` | `—` | TEXT；可选 |
| `xdxr` | `-f, --format` | `json` | Choice(json, table, csv)；输出格式 |
| `xdxr` | `-o, --output` | `—` | PATH；写入文件或目录；目录内文件名默认为标的代码 |
| `xdxr` | `--fields` | `basic` | Choice(basic, all)；表格输出字段；JSON 和 CSV 始终包含全部字段 |
| `fund-flow` | `SYMBOL_OR_MARKET (ARG)` | `—` | TEXT；必填 |
| `fund-flow` | `CODE (ARG)` | `—` | TEXT；可选 |
| `fund-flow` | `--start` | `0` | INTEGER；距最新记录的起始偏移 |
| `fund-flow` | `--count` | `30` | INTEGER；请求数量 |
| `fund-flow` | `--closed-only` | `—` | BOOLEAN；排除当天可能尚未完整的数据 |
| `fund-flow` | `-f, --format` | `json` | Choice(json, table, csv)；输出格式 |
| `fund-flow` | `-o, --output` | `—` | PATH；写入文件或目录；目录内文件名默认为标的代码 |
| `fund-flow` | `--fields` | `basic` | Choice(basic, all)；表格输出字段；JSON 和 CSV 始终包含全部字段 |
| `offline` | `SYMBOL_OR_MARKET (ARG)` | `—` | TEXT；必填 |
| `offline` | `CODE (ARG)` | `—` | TEXT；可选 |
| `offline` | `--period` | `DAILY` | Choice(DAILY, 1MIN, 5MIN)；本地 K 线周期 |
| `offline` | `--count` | `800` | INT ≥ 1；返回数量 |
| `offline` | `--start` | `0` | INT ≥ 0；距最新记录的起始偏移 |
| `offline` | `--vipdoc` | `—` | PATH；vipdoc 目录；默认读取 settings/config.yaml 的 offline.vipdoc |
| `offline` | `-f, --format` | `json` | Choice(json, table, csv)；输出格式 |
| `offline` | `-o, --output` | `—` | PATH；写入文件或目录；目录内文件名默认为标的代码 |
| `offline` | `--fields` | `basic` | Choice(basic, all)；表格输出字段；JSON 和 CSV 始终包含全部字段 |
| `ex kline` | `MARKET (ARG)` | `—` | TEXT；必填 |
| `ex kline` | `CODE (ARG)` | `—` | TEXT；必填 |
| `ex kline` | `--period` | `DAILY` | TEXT；K线周期: DAILY/5MIN/15MIN/30MIN/60MIN/1MIN |
| `ex kline` | `--count` | `800` | INTEGER；K线数量 |
| `ex kline` | `--start` | `0` | INTEGER；起始偏移 |
| `ex kline` | `--adjust` | `NONE` | TEXT；复权: NONE/QFQ/HFQ |
| `ex kline` | `-f, --format` | `json` | Choice(json, table, csv)；输出格式 |
| `ex kline` | `-o, --output` | `—` | PATH；写入文件或目录；目录内文件名默认为标的代码 |
| `ex kline` | `--fields` | `basic` | Choice(basic, all)；表格输出字段；JSON 和 CSV 始终包含全部字段 |
| `ex quote` | `MARKET (ARG)` | `—` | TEXT；必填 |
| `ex quote` | `CODE (ARG)` | `—` | TEXT；必填 |
| `ex quote` | `-f, --format` | `json` | Choice(json, table, csv)；输出格式 |
| `ex quote` | `-o, --output` | `—` | PATH；写入文件或目录；目录内文件名默认为标的代码 |
| `ex quote` | `--fields` | `basic` | Choice(basic, all)；表格输出字段；JSON 和 CSV 始终包含全部字段 |
| `ex quote-list` | `MARKET (ARG)` | `—` | TEXT；必填 |
| `ex quote-list` | `--count` | `600` | INTEGER；请求数量 |
| `ex quote-list` | `--start` | `0` | INTEGER；起始偏移 |
| `ex quote-list` | `-f, --format` | `json` | Choice(json, table, csv)；输出格式 |
| `ex quote-list` | `-o, --output` | `—` | PATH；写入文件或目录；目录内文件名默认为标的代码 |
| `ex quote-list` | `--fields` | `basic` | Choice(basic, all)；表格输出字段；JSON 和 CSV 始终包含全部字段 |
| `ex tick` | `MARKET (ARG)` | `—` | TEXT；必填 |
| `ex tick` | `CODE (ARG)` | `—` | TEXT；必填 |
| `ex tick` | `--date` | `—` | INTEGER；日期 YYYYMMDD（默认今天） |
| `ex tick` | `-f, --format` | `json` | Choice(json, table, csv)；输出格式 |
| `ex tick` | `-o, --output` | `—` | PATH；写入文件或目录；目录内文件名默认为标的代码 |
| `ex tick` | `--fields` | `basic` | Choice(basic, all)；表格输出字段；JSON 和 CSV 始终包含全部字段 |
| `ex markets` | `-f, --format` | `json` | Choice(json, table, csv)；输出格式 |
| `ex markets` | `-o, --output` | `—` | PATH；写入文件或目录；目录内文件名默认为标的代码 |
| `ex markets` | `--fields` | `basic` | Choice(basic, all)；表格输出字段；JSON 和 CSV 始终包含全部字段 |
| `workdays` | `--date` | `—` | DATE； |
| `workdays` | `--start` | `—` | DATE； |
| `workdays` | `--end` | `—` | DATE； |
| `workdays` | `--max-bars` | `10000` | INT ≥ 1； |
| `workdays` | `-f, --format` | `json` | Choice(json, table, csv)； |
| `workdays` | `-o, --output` | `—` | PATH； |
| `f10` | `SYMBOL_OR_MARKET (ARG)` | `—` | TEXT；必填 |
| `f10` | `CODE (ARG)` | `—` | TEXT；可选 |
| `f10` | `--section` | `profile` | Choice(profile, report, business, dividends, shareholders, topics, announcements, news, governance, forecast)；资料栏目 |
| `f10` | `--report-type` | `zcfzb` | TEXT；report 栏目的报表类型 |
| `f10` | `--report-date` | `—` | TEXT；business 栏目的报告期；缺省使用最新可用报告期 |
| `f10` | `--timeout` | `8.0` | FLOAT ≥ 0.1； |
| `f10` | `-f, --format` | `json` | Choice(json, table, csv)； |
| `f10` | `-o, --output` | `—` | PATH； |

共 31 个叶子命令；`--help` 和根级 `--version` 由 Click 提供，未逐行列出。实际执行时以当前环境的 `tdxman COMMAND --help` 为准。
