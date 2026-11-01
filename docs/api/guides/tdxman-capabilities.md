# tdxman 能力矩阵

[矩阵总览](README.md) · [aspool](aspool-capabilities.md) · [完整 API 签名](../signatures.md)

## 已实现 CLI：31 个叶子命令

以下是本仓库原生命令，不是 ageman 拟议的新命令名称。完整参数、默认值、输出列和源调用以链接的命令页及当前 `--help` 为准。

| 能力 | CLI 入口/主要参数 | Python 来源 | 主要维度 | 边界 |
| --- | --- | --- | --- | --- |
| 当前异常股票 | [`tdxman abnormal-list --type all/st/suspended`](../cli/commands/market/abnormal-list.md) | 股票目录简称；`TdxClient.get_security_quotes` 的状态位 0x20 | 证券×本次观测；ST 类型、停牌标记 | 当前快照；来源缺失整体报错；停牌在线可用性见命令页 |
| 当前股票目录 | [`tdxman stock-codes [MARKET] --scope ... --code ... --name ...`](../cli/commands/market/stock-codes.md) | 沪深 `TdxClient.get_security_count/get_security_list`；北交所 `MacClient.get_stock_quotes_list` | 市场×证券；代码、名称、板别 | 含 CDR；代码前缀分类；当前来源目录，不提供历史生命周期 |
| 当前报价 | [`tdxman quote SYMBOL`](../cli/commands/market/quote.md) | `MacClient.get_stock_quotes` | 证券×观测时刻；含报价/盘口字段 | 快照；不是历史逐笔；BaoStock 分支为历史日线 |
| 分类报价 | [`tdxman quote-list CATEGORY`](../cli/commands/market/quote-list.md) | `MacClient.get_stock_quotes_list` | 分类×证券×当前时刻 | 分页/排序；不能当历史证券目录 |
| 证券/指数 K 线 | [`tdxman kline SYMBOL --period ... --count ... --start ... --adjust ...`](../cli/commands/market/kline.md) | `MacClient.get_stock_kline` | 证券×周期×时间；OHLCVA | start 为距最新记录偏移；服务端复权不是 PIT 因子 |
| 单日/多日分时 | [`tdxman tick SYMBOL --date ... / --days ...`](../cli/commands/market/tick.md) | `MacClient.get_tick_chart / get_tick_charts` | 证券×交易日×分时点 | 分时曲线不是完整 OHLC 分钟线 |
| 逐笔成交 | [`tdxman transaction SYMBOL --date ... --count ... --start ...`](../cli/commands/market/transaction.md) | `MacClient.get_transactions` | 证券×交易日×成交序列 | 上游窗口/分页限制，不承诺全历史 |
| 交易日观察 | [`tdxman workdays --date ... / --start ... --end ...`](../cli/commands/market/workdays.md) | `WorkdayService` | 市场×日期 | 已观测开市日，不是未来法定休市安排 |
| 财务摘要 | [`tdxman finance SYMBOL`](../cli/commands/financials/finance.md) | `TdxClient.get_finance_info` | 证券×来源报告日期 | 摘要；历史财务文件另有 API |
| 公司行为 | [`tdxman xdxr SYMBOL`](../cli/commands/financials/xdxr.md) | `TdxClient.get_xdxr_info` | 证券×生效日×事件类别 | 事件单位/类别见详细接口 |
| 历史资金流 | [`tdxman fund-flow SYMBOL --closed-only`](../cli/commands/financials/fund-flow.md) | `TdxClient.get_history_fund_flow` | 证券×日期×资金分类 | 当天是否完整需显式处理 |
| F10 页面 | [`tdxman f10 SYMBOL --section ...`](../cli/commands/financials/f10.md) | `F10Client.query` | 证券×栏目/查询 | 独立 TQLEX 来源 |
| 板块/指数目录 | [`tdxman board-list --type ...`](../cli/commands/board/board-list.md) | `MacClient.get_board_list；ZS 为报价目录路由` | 板块类型×板块 | 当前目录；ZS 与行业/概念目录区分 |
| 板块成分 | [`tdxman board-members BOARD`](../cli/commands/board/board-members.md) | `MacClient.get_board_members` | 板块×当前成员证券 | 当前成员报价，不提供历史权重 |
| 所属板块 | [`tdxman belong-board SYMBOL`](../cli/commands/board/belong-board.md) | `MacClient.get_belong_board` | 证券×板块 | 当前关系 |
| 竞价 | [`tdxman auction SYMBOL`](../cli/commands/monitor/auction.md) | `MacClient.get_auction` | 证券×竞价点 | 来源窗口限制 |
| 当日资金流 | [`tdxman capital-flow SYMBOL`](../cli/commands/monitor/capital-flow.md) | `MacClient.get_capital_flow` | 证券×当前资金分类 | 不能替代历史资金流 |
| 市场统计 | [`tdxman market-stat`](../cli/commands/monitor/market-stat.md) | `TdxClient.get_market_stat` | 市场×统计类别×快照 | 原生统计口径不等于 aspool 逐股汇总 |
| 证券资料 | [`tdxman symbol-info SYMBOL`](../cli/commands/monitor/symbol-info.md) | `MacClient.get_symbol_info` | 证券×当前字段 | 来源字段含义见详情 |
| 异动 | [`tdxman unusual`](../cli/commands/monitor/unusual.md) | `MacClient.get_unusual` | 证券×异动类型×时刻 | 来源提供的窗口 |
| 本地行情 | [`tdxman offline SYMBOL --period ...`](../cli/commands/offline/offline.md) | `read_daily_bars / read_lc_min` | 证券×日/1分钟/5分钟 | 需本机 vipdoc；CLI 与全部离线 reader 范围不同 |
| 扩展市场枚举 | [`tdxman ex markets`](../cli/commands/ex/markets.md) | `ExMarket 枚举` | 扩展市场标识 | 本地枚举，不是在线目录 |
| 扩展标的目录 | [`tdxman ex quote-list`](../cli/commands/ex/quote-list.md) | `MacExClient.get_goods_list` | 扩展市场×商品 | 名称虽为 quote-list，实际为商品目录 |
| 扩展报价 | [`tdxman ex quote`](../cli/commands/ex/quote.md) | `MacExClient.get_goods_quotes` | 市场×商品×快照 | MAC EX 与标准 EX 市场 ID 不保证互换 |
| 扩展 K 线 | [`tdxman ex kline`](../cli/commands/ex/kline.md) | `MacExClient.get_goods_kline` | 市场×商品×周期×时间 | 单请求；没有股票 MAC 的自动翻页保证 |
| 扩展分时 | [`tdxman ex tick`](../cli/commands/ex/tick.md) | `MacExClient.get_goods_tick_chart` | 市场×商品×分时点 | --date 历史分支存在日期类型问题，见命令页 |
| A 股市场枚举 | [`tdxman markets`](../cli/commands/shared/markets.md) | `Market 枚举` | 市场 | 本地枚举 |
| 服务器信息 | [`tdxman server-info`](../cli/commands/shared/server-info.md) | `MacClient.get_server_info` | 服务器×配置 | 来源配置而非交易所官方日历 |
| 延迟检测 | [`tdxman ping`](../cli/commands/shared/ping.md) | `各客户端 ping_all` | 协议×服务器 | 当前环境观测 |
| 优选服务器 | [`tdxman bestip`](../cli/commands/shared/bestip.md) | `各客户端优选与配置保存` | 协议×服务器排名 | 会更新本机配置 |
| 版本 | [`tdxman version`](../cli/commands/shared/version.md) | `包版本` | 安装包 | 不证明连接/数据可用 |

## CLI 外 Python 能力

| 能力 | 入口 | 数据维度/范围 | 状态与边界 |
| --- | --- | --- | --- |
| 标准行情 | [TdxClient / AsyncTdxClient](../hq/README.md) | count/list/quotes、证券和指数 K 线、分时、逐笔、价格限制 | 已实现；标准协议与 MAC 字段/参数不同 |
| 公司资料与财务文件 | [标准文件 API](../hq/finance-files.md) | 公司资料目录/正文、财务文件目录/下载/解析、block/report/file | 已实现；历史财务 `fields` 数组不是已映射三张报表 |
| 完整 OHLC 分钟线 | [MacClient.get_minute_bars](../mac/market.md) | 证券×分钟周期×日期时间 | 已实现 Python API；没有独立 CLI 叶子命令 |
| MAC 辅助与文件 | [MAC 文件](../mac/files.md)、[行情](../mac/market.md) | chart sampling、kline offset、文件元信息/分块下载 | 已实现 Python API；以实际返回及完整性 attrs 为准 |
| 标准扩展协议 | [ExTdxClient / AsyncExTdxClient](../exhq/standard.md) | 在线市场、标的目录、报价、K 线、历史区间、分时、逐笔 | 已实现；返回列表/模型，不套用标准行情 DataFrame 合同 |
| MAC 扩展协议 | [MacExClient / AsyncMacExClient](../exhq/mac.md) | goods count/list/quotes/kline/tick/sampling/transaction | 已实现；CLI 只覆盖其中一部分 |
| 全部离线读取 | [reader](../reader/README.md) | .day/.lc1/.lc5、扩展日线、板块、gbbq、历史财务 | 已实现；文件存在与覆盖由本机数据决定 |
| 统一代理与异步 | [UnifiedTdxClient](../services/unified.md)、[连接](../connections.md) | MAC/MAC EX 路由、连接与心跳 | 已实现；不计为额外业务数据源 |
| BaoStock 补充源 | [BaostockClient](../services/f10-workdays-baostock.md) | 沪深日线、股票资料、交易日历 | 显式选择；不支持 BJ，不自动替代 TDX |
| Tick Stock Panel | [provider](../services/tick-stock-panel.md) | 历史日线/分钟线、实时快照/盘口/当日分时 | 可选集成；`get_intraday_latest` 尚无实现 |

签名附录是公开方法定义清单，包含同步/异步与代理重复项，不作为业务能力分母。证券代码保持字符串及前导零；默认规范代码 `000001.SZ`，扩展标的必须同时给出市场与代码。此次核对源码和文档覆盖，没有执行在线可用性或行为等价验证。
