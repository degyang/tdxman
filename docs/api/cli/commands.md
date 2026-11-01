# CLI 命令索引

[CLI 总入口](README.md) · [全部参数与默认值](options.md) · [API 总入口](../README.md)

每个链接包含可运行示例、业务参数默认值、所调用的 Python API、输出与限制。共用的 `--format`、`--output`、`--fields` 见 [CLI 总入口](README.md)。

| 数据域 | 命令 | 用途与主要参数 |
| --- | --- | --- |
| 状态 | [`abnormal-list`](commands/market/abnormal-list.md) | 当前 ST/*ST 与停牌；`--type all/st/suspended` |
| 目录 | [`stock-codes`](commands/market/stock-codes.md) | 当前沪深北股票清单；`MARKET`、`--scope`、`--code`、`--name` |
| 行情 | [`quote`](commands/market/quote.md) | 实时报价；`--source baostock` 则是历史日线 |
| 行情 | [`quote-list`](commands/market/quote-list.md) | 分类报价列表；`CATEGORY`、`--count`、排序 |
| 行情 | [`kline`](commands/market/kline.md) | 证券/标准指数 K 线；`--period`、`--count`、`--start`、`--adjust` |
| 行情 | [`tick`](commands/market/tick.md) | 单日或五日分时；`--date`、`--days` |
| 行情 | [`transaction`](commands/market/transaction.md) | 逐笔；`--date`、`--count`、`--start` |
| 行情 | [`workdays`](commands/market/workdays.md) | 已观测交易日；`--date` 或起止范围 |
| 财务 | [`finance`](commands/financials/finance.md) | 最新财务摘要 |
| 财务 | [`xdxr`](commands/financials/xdxr.md) | 除权除息/股本事件 |
| 财务 | [`fund-flow`](commands/financials/fund-flow.md) | 历史资金流；`--closed-only` 可排除当天 |
| 财务 | [`f10`](commands/financials/f10.md) | 独立 TQLEX 公司页面；`--section` 等 |
| 板块 | [`board-list`](commands/board/board-list.md) | 板块或标准指数目录；`--type ZS` 特殊路由 |
| 板块 | [`board-members`](commands/board/board-members.md) | 板块当前成分报价 |
| 板块 | [`belong-board`](commands/board/belong-board.md) | 个股所属板块 |
| 监控 | [`auction`](commands/monitor/auction.md) | 集合竞价 |
| 监控 | [`capital-flow`](commands/monitor/capital-flow.md) | 当日资金流快照 |
| 监控 | [`market-stat`](commands/monitor/market-stat.md) | 全市场涨跌统计 |
| 监控 | [`symbol-info`](commands/monitor/symbol-info.md) | 个股特征/基础信息 |
| 监控 | [`unusual`](commands/monitor/unusual.md) | 市场异动 |
| 离线 | [`offline`](commands/offline/offline.md) | 本机 vipdoc 日线/分钟线 |
| 扩展 | [`ex markets`](commands/ex/markets.md) | 本地扩展市场枚举 |
| 扩展 | [`ex quote-list`](commands/ex/quote-list.md) | 扩展市场商品目录 |
| 扩展 | [`ex quote`](commands/ex/quote.md) | 单商品当前报价 |
| 扩展 | [`ex kline`](commands/ex/kline.md) | 扩展市场 K 线 |
| 扩展 | [`ex tick`](commands/ex/tick.md) | 扩展市场分时 |
| 工具 | [`markets`](commands/shared/markets.md) | 本地 A 股市场枚举 |
| 工具 | [`server-info`](commands/shared/server-info.md) | 服务器交易时段配置 |
| 工具 | [`ping`](commands/shared/ping.md) | 服务器延迟 |
| 工具 | [`bestip`](commands/shared/bestip.md) | 验证并保存本机优选服务器排名 |
| 工具 | [`version`](commands/shared/version.md) | 安装版本 |

命令索引对应 `src/tdxman/cli/__init__.py` 注册的 27 个顶级入口（其中 `ex` 是命令组）及 `src/tdxman/cli/cmd_ex.py` 的 5 个子命令，共 31 个叶子命令。
