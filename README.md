# tdxman

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/tdxman.svg)](https://pypi.org/project/tdxman/)

通达信 TCP 行情协议客户端。支持 A 股、港股、美股、期货全市场；内置 `tdxman` CLI 工具，默认 JSON 输出，天然适配 Claude Code、OpenClaw、Hermes 等 AI Agent 工具链。提供同步 + asyncio 双接口；strict mypy 通过；每一层编解码都有离线 fixture 测试覆盖。

## 安装

```bash
pip install tdxman
```

安装后自动注册 `tdxman` CLI 命令：

```bash
tdxman --help
```

开发模式：

```bash
pip install -e ".[dev]"
```

## CLI 参考

CLI 帮助页面遵循 [CLI 帮助风格规范](docs/design/cli_help_style.md)。

`tdxman` 默认将 JSON 输出到标准输出。使用 `--format json|table|csv` 选择格式；`table`
在终端保持网格表格显示。使用 `--output` 将结果写入文件或目录：未指定 `--format` 时
按 JSON 写入；目录中的默认文件名为标的代码。`--format table` 写入 Markdown 表格（`.md`）。

```bash
tdxman kline SH 600519 --format csv --output data/600519.csv
tdxman finance SZ 006324 --format json --output data/006324.json
tdxman quote "SZ 000001,SH 600519" --format table --output data/quotes
```

多标的 `quote` 必须将 `--output` 指向目录，命令会为每个标的分别创建响应文件。
表格默认只显示常用字段；使用 `--fields all` 查看完整返回字段。JSON 和 CSV 始终保留完整字段。

本地通达信数据使用 `offline` 命令组。默认 `vipdoc` 目录写在 `settings/config.yaml` 的
`offline.vipdoc`；也可临时指定 `--vipdoc`：

```bash
tdxman offline SH 600519 --period DAILY --count 30
tdxman offline SZ 000001 --period 5MIN --format table
tdxman offline SH 600519 --vipdoc /mnt/d/Stock/new_tdx/vipdoc
```

### 基础

```bash
tdxman ping                    # 服务器测速
tdxman version                 # 版本号
tdxman markets                 # A 股市场代码与示例
```

### 行情

`quote-list CATEGORY` 的 `CATEGORY` 是市场分类，不是指数类别：

| 代码 | 含义 |
|------|------|
| `SH` / `SZ` / `A` | 上证 A 股 / 深证 A 股 / 全部 A 股 |
| `B` | B 股 |
| `KCB` / `CYB` / `BJ` | 科创板 / 创业板 / 北交所 |
| `ETF` / `LOF` | 交易型开放式基金 / 上市型开放式基金 |
| `HGT` / `SGT` | 沪股通标的 / 深股通标的 |
| `FXJS` | 风险警示证券 |
| `ZS` | 沪深系列指数目录与实时报价 |

`quote-list` 还可按 `CODE`、`PRICE`、`VOLUME`、`TOTAL_AMOUNT`、`TURNOVER_RATE`、`CHANGE_PCT` 等字段排序。板块目录请使用下方的 `board-list`，不要将 `BOARD_*` 内部分类代码作为日常命令参数。

```bash
# K 线
tdxman kline SZ 000001 --count 30 --format table
tdxman kline SH 600519 --period 5MIN --adjust QFQ

# 实时报价
tdxman quote "SZ 000001,SH 600519" --format table

# 市场分类报价（按涨幅排序）
tdxman quote-list A --count 20 --format table
tdxman quote-list KCB --sort TOTAL_AMOUNT --order ASC
tdxman quote-list CYB --count 50
```

### 分时 / 成交

```bash
tdxman tick SZ 000001 --format table
tdxman tick SH 600519 --days 5
tdxman tick SZ 000001 --date 20250115

tdxman transaction SZ 000001 --count 100 --format table
tdxman transaction SH 600519 --date 20250115
```

### 指数类别与发现方式

项目支持以下指数类别。先从相应目录取得代码，再用对应的 K 线命令读取历史行情。

| 类别 | 目录命令 | 历史 K 线 |
|------|----------|-----------|
| 通达信行业、概念、风格、地域等板块/研究指数（主要为 `881xxx`） | `tdxman board-list [--type HY/HY2/GN/FG/DQ/OTHER]` | `tdxman kline SH CODE ...` |
| 沪深交易所、国证等标准 A 股指数（如 `000300`、`000688`、`399001`、`399006`） | `tdxman board-list --type ZS` 或 `tdxman quote-list ZS` | `tdxman kline SH|SZ CODE ...` |
| 中证指数 | `tdxman ex quote-list CSI_INDEX` | `tdxman ex kline CSI_INDEX CODE ...` |
| 国证指数 | `tdxman ex quote-list SZSE_INDEX` | `tdxman ex kline SZSE_INDEX CODE ...` |
| 香港指数 | `tdxman ex quote-list HK_INDEX` | `tdxman ex kline HK_INDEX CODE ...` |
| 国际指数 | `tdxman ex quote-list INTL_INDEX` | `tdxman ex kline INTL_INDEX CODE ...` |
| 商品指数 | `tdxman ex quote-list FUTURES_INDEX` | `tdxman ex kline FUTURES_INDEX CODE ...` |
| 风控、华证及扩展板块指数 | `tdxman ex quote-list RISK_CONTROL_INDEX`、`HUAZHENG_INDEX`、`EXTENDED_SECTOR_INDEX` | 对应 `tdxman ex kline MARKET CODE ...` |

通达信板块体系不等于标准指数全集：`board-list` 主要返回 `881xxx` 行业、概念、风格等板块及研究指数；标准沪深指数使用 `ZS` 目录发现。`880xxx` 是通达信保留的旧行业或市场统计指数，是否可用及名称以目录和实时行情返回为准。

`board-list --type` 可收集的板块类别如下：

| 类型 | 含义 |
|------|------|
| `ALL` | 全部板块 |
| `HY` / `HY2` | 一级行业 / 二级行业 |
| `GN` / `FG` / `DQ` | 概念 / 风格 / 地域 |
| `OTHER` | 其他板块 |
| `YJ_LEVEL1/2/3` | 业绩一级 / 二级 / 三级 |
| `ZS` | 沪深标准指数目录，不走通达信板块协议 |

```bash
# 通达信板块/研究指数：全部、一级行业、二级行业、概念、风格、地域、其他、业绩分级
tdxman board-list --format table
tdxman board-list --type HY --format table
tdxman board-list --type HY2 --format table
tdxman board-list --type GN --format table
tdxman board-list --type FG --format table
tdxman board-list --type DQ --format table
tdxman board-list --type OTHER --format table
tdxman board-list --type YJ_LEVEL1 --format table
tdxman board-list --type YJ_LEVEL2 --format table
tdxman board-list --type YJ_LEVEL3 --format table

# 查询通达信板块或支持的标准指数的实时成分股，或查询个股归属
tdxman board-members 881001 --format table
tdxman board-members 000699 --count 20 --format table
tdxman belong-board SZ 000001 --format table

# 标准 A 股指数目录与实时行情
tdxman board-list --type ZS --count 600 --format table
tdxman quote-list ZS --count 600 --format table

# 标准指数的历史 K 线
tdxman kline SH 000300 --period DAILY --count 120 --format table
tdxman kline SZ 399001 --period DAILY --count 120 --format table
tdxman kline SH 000688 --period DAILY --count 120 --format table

# 扩展市场指数目录；选定代码后把 MARKET 和 CODE 传给 ex kline
tdxman ex quote-list CSI_INDEX --count 600 --format table
tdxman ex quote-list SZSE_INDEX --count 600 --format table
tdxman ex quote-list HK_INDEX --count 600 --format table
tdxman ex quote-list INTL_INDEX --count 600 --format table
tdxman ex quote-list FUTURES_INDEX --count 600 --format table
tdxman ex kline CSI_INDEX 000300 --period DAILY --count 120 --format table
```

标准 A 股指数通过 `kline` 返回的 K 线带有 `up_count`、`down_count`，即通达信随该指数记录给出的上涨、下跌家数；统计口径随指数而定。指数目录与实时行情不含这两个字段。

### 资金 / 监控

```bash
tdxman capital-flow SH 600519 --format table   # 当日资金流向快照
tdxman auction SZ 000001 --format table
tdxman unusual SH --count 100 --format table
tdxman market-stat --format table
tdxman server-info --format table
tdxman symbol-info SZ 000001 --format table
```

`market-stat` 输出涨跌家数、
成交额、成交量、总市值和涨跌停家数。`suspended_count` 是总家数减去涨、跌、平盘家数的
残差估算，并非独立停牌字段；`total_market_cap` 沿用原命令的 `880001.close × 1e10`
换算。服务器返回空数据或缺少必要字段时，命令会报错，不会用零填充。

### 财务

```bash
tdxman finance SH 600519                         # 最新财务摘要
tdxman fund-flow SH 600519 --closed-only     # 历史资金流向，排除当天未完整数据
```

### 扩展市场（港股/美股/期货）

```bash
tdxman ex markets                                       # 列出可用市场
tdxman ex kline HK_MAIN_BOARD 00700 --count 30 --format table  # 港股 K 线
tdxman ex kline US_STOCK AAPL --format table                    # 美股 K 线
tdxman ex quote US_STOCK TSLA --format table                    # 美股报价
tdxman ex quote-list HK_MAIN_BOARD --format table               # 港股商品列表
tdxman ex tick HK_MAIN_BOARD 00700 --format table               # 港股分时
```

## CLI 命令汇总

| 命令 | 说明 |
|------|------|
| `ping` | 服务器延迟测速 |
| `version` | 版本号 |
| `kline` | K 线（日/周/月/分钟，支持复权） |
| `quote` | 实时报价（单只/批量） |
| `quote-list` | 市场分类排序报价（A/SH/SZ/KCB/CYB） |
| `tick` | 分时图（单日/多日/历史） |
| `transaction` | 逐笔成交 |
| `board-list` | 板块列表（行业/概念/风格） |
| `board-members` | 板块成分股报价 |
| `belong-board` | 个股所属板块 |
| `capital-flow` | 当日资金流向快照 |
| `auction` | 集合竞价 |
| `unusual` | 市场异动 |
| `market-stat` | 全市场涨跌统计 |
| `server-info` | 服务器交易时段 |
| `symbol-info` | 个股特征快照 |
| `finance` / `finance` | 最新财务摘要 |
| `fund-flow` | 按交易日统计的历史资金流向 |
| `markets` | 列出 A 股市场代码 |
| `ex kline` | 扩展市场 K 线 |
| `ex quote` | 扩展市场报价 |
| `ex quote-list` | 扩展市场商品列表 |
| `ex tick` | 扩展市场分时 |
| `ex markets` | 列出可用扩展市场 |

## Python API

### 连接管理

所有客户端支持 `from_best_host()` 自动选最低延迟服务器：

```python
from tdxman import MacClient

with MacClient.from_best_host() as c:
    df = c.get_stock_kline(...)
```

| 客户端 | 端口 | 覆盖范围 |
|--------|------|----------|
| `MacClient` / `AsyncMacClient` | 7709 | A 股行情（MAC 协议，推荐） |
| `MacExClient` / `AsyncMacExClient` | 7727 | 港股/美股/期货（MAC 协议） |
| `UnifiedTdxClient` / `AsyncUnifiedTdxClient` | 自动 | A 股 + 扩展市场统一入口 |
| `TdxClient` / `AsyncTdxClient` | 7709 | A 股行情（标准协议） |

### MAC 协议（推荐）

#### 报价

```python
from tdxman import MacClient, Market, Category, SortType, SortOrder

with MacClient.from_best_host() as c:
    # 批量报价（最多 80 只/次）
    df = c.get_stock_quotes([(Market.SH, "600519"), (Market.SZ, "000858")])

    # 市场分类排序报价
    df = c.get_stock_quotes_list(
        Category.A, count=20,
        sort_type=SortType.CHANGE_PCT,
        sort_order=SortOrder.DESC,
    )
```

返回列：`market, code, name` + 动态字段（`pre_close, open, high, low, close, vol, amount, turnover, vol_ratio` 等）。

#### K 线（支持复权）

```python
from tdxman import MacClient, Market, Period, Adjust

with MacClient.from_best_host() as c:
    # 日K前复权
    df = c.get_stock_kline(Market.SH, "600519", Period.DAILY, count=10, adjust=Adjust.QFQ)
    # 5分钟线
    df = c.get_stock_kline(Market.SZ, "000001", Period.MIN_5, count=100)
```

返回列：`datetime, open, close, high, low, vol, amount`。

#### 分时

```python
with MacClient.from_best_host() as c:
    df = c.get_tick_chart(Market.SH, "600519")          # 单日分时
    df = c.get_tick_charts(Market.SH, "600519", days=3)  # 多日分时（最多5天）
    df = c.get_chart_sampling(Market.SH, "600519")       # 240点缩略采样
```

#### 逐笔成交

```python
with MacClient.from_best_host() as c:
    df = c.get_transactions(Market.SH, "600519", count=100)
    df = c.get_transactions(Market.SH, "600519", count=100, date=20250115)
```

#### 板块

```python
from tdxman import BoardType

with MacClient.from_best_host() as c:
    df = c.get_board_list(BoardType.GN)                       # 概念板块
    df = c.get_board_members("881001", sort_type=SortType.CHANGE_PCT)
    df = c.get_belong_board(Market.SZ, "000001")              # 个股所属板块
```

#### 资金流向

```python
with MacClient.from_best_host() as c:
    df = c.get_capital_flow(Market.SH, "600519")
```

返回列：`date, main_in, main_out, main_net, small_in/out/net, mid_in/out/net, large_in/out/net`。

#### 监控

```python
with MacClient.from_best_host() as c:
    df = c.get_auction(Market.SH, "600519")     # 集合竞价
    df = c.get_unusual(Market.SH)               # 市场异动
    df = c.get_symbol_info(Market.SZ, "000001") # 个股特征快照
    df = c.get_server_info()                     # 服务器交易时段
```

### 扩展市场

```python
from tdxman import MacExClient, ExMarket, Period

with MacExClient.from_best_host() as c:
    count = c.goods_count(ExMarket.HK_MAIN_BOARD)
    df = c.goods_list(ExMarket.HK_MAIN_BOARD, start=0, count=50)
    df = c.goods_kline(ExMarket.US_STOCK, "AAPL", Period.DAILY, count=10)
    df = c.goods_quotes([(ExMarket.HK_MAIN_BOARD, "00700")])
    df = c.goods_tick_chart(ExMarket.HK_MAIN_BOARD, "00700")
    df = c.goods_transaction(ExMarket.HK_MAIN_BOARD, "00700", count=100)
```

### 统一客户端

```python
from tdxman import UnifiedTdxClient, ExMarket, Market, Period

with UnifiedTdxClient() as client:
    # A 股 -- 自动路由到 MacClient
    df = client.get_stock_kline(Market.SH, "600519", Period.DAILY, count=5)
    df = client.get_stock_quotes([(Market.SH, "600519")])
    df = client.get_board_list()

    # 扩展市场 -- 自动路由到 MacExClient
    df = client.goods_kline(ExMarket.HK_MAIN_BOARD, "00700", Period.DAILY, count=5)
```

### 标准协议

```python
from tdxman import TdxClient, Market, KlineCategory

with TdxClient.from_best_host() as c:
    count = c.get_security_count(Market.SH)
    stocks = c.get_security_list(Market.SH, start=0)
    quotes = c.get_security_quotes([(Market.SH, "600000"), (Market.SZ, "000001")])
    bars = c.get_security_bars(Market.SZ, "002176", KlineCategory.DAY, 0, 100)
    minute = c.get_minute_time_data(Market.SH, "600000")
    trades = c.get_transaction_data(Market.SH, "600000", 0, 20)
    flow = c.get_fund_flow(Market.SH, "600519")
    blocks = c.get_block_info("block_gn.dat")
    xdxr = c.get_xdxr_info(Market.SH, "600519")
    stat = c.get_market_stat()
```

`AsyncTdxClient` 提供对应的 `async def` 方法，接口一一对应。

### 离线数据读取

无需网络，从本地通达信安装目录直接读取：

```python
from tdxman.offline import detect_tdx_home, read_daily_bars, find_daily_bar_file
from tdxman import Market

home = detect_tdx_home()
filepath = find_daily_bar_file(Market.SH, "600000")
bars = read_daily_bars(filepath)
```

支持：日线、分钟线、扩展市场日线、板块、股本变迁、历史财务数据。

## 枚举参考

### Period（K 线周期）

| 值 | 名称 | 说明 |
|----|------|------|
| 7 | `MIN_1` | 1 分钟 |
| 0 | `MIN_5` | 5 分钟 |
| 1 | `MIN_15` | 15 分钟 |
| 2 | `MIN_30` | 30 分钟 |
| 3 | `MIN_60` | 60 分钟 |
| 4 | `DAILY` | 日线 |
| 5 | `WEEKLY` | 周线 |
| 6 | `MONTHLY` | 月线 |
| 10 | `QUARTERLY` | 季线 |
| 11 | `YEARLY` | 年线 |

### Adjust（复权类型）

| 值 | 名称 | 说明 |
|----|------|------|
| 0 | `NONE` | 不复权 |
| 1 | `QFQ` | 前复权 |
| 2 | `HFQ` | 后复权 |

### Category（市场分类）

| 值 | 名称 | 说明 |
|----|------|------|
| 0 | `SH` | 上证 A 股 |
| 2 | `SZ` | 深证 A 股 |
| 6 | `A` | 全部 A 股 |
| 7 | `B` | B 股 |
| 8 | `KCB` | 科创板 |
| 12 | `BJ` | 北证 A 股 |
| 14 | `CYB` | 创业板 |

### BoardType（板块类型）

| 值 | 名称 | 说明 |
|----|------|------|
| 0 | `HY` | 行业一级 |
| 1 | `HY2` | 行业二级 |
| 3 | `GN` | 概念 |
| 4 | `FG` | 风格 |
| 5 | `DQ` | 地区 |
| 255 | `ALL` | 全部 |

### SortType（排序字段）

| 名称 | 说明 |
|------|------|
| `CODE` | 代码 |
| `PRICE` | 现价 |
| `CHANGE_PCT` | 涨幅% |
| `VOLUME` | 成交量 |
| `TOTAL_AMOUNT` | 成交额 |
| `TURNOVER_RATE` | 换手% |
| `MAIN_NET_AMOUNT` | 主力净额 |

### ExMarket（扩展市场）

| 值 | 名称 | 说明 |
|----|------|------|
| 28 | `ZZ_FUTURES` | 郑州商品 |
| 29 | `DL_FUTURES` | 大连商品 |
| 30 | `SH_FUTURES` | 上海期货 |
| 31 | `HK_MAIN_BOARD` | 香港主板 |
| 47 | `CFFEX_FUTURES` | 中金所期货 |
| 48 | `HK_GEM` | 香港创业板 |
| 74 | `US_STOCK` | 美国股票 |

### Market（市场）

| 值 | 名称 | 说明 |
|----|------|------|
| 0 | `SZ` | 深圳 |
| 1 | `SH` | 上海 |
| 2 | `BJ` | 北京 |

## 完整 API 列表

### MacClient / AsyncMacClient

| 方法 | 说明 |
|------|------|
| `get_stock_quotes(stocks, fields)` | 批量实时报价 |
| `get_stock_quotes_list(category, ...)` | 市场分类排序报价 |
| `get_stock_kline(market, code, period, ...)` | K 线（支持复权） |
| `get_tick_chart(market, code, date)` | 单日分时图 |
| `get_tick_charts(market, code, days)` | 多日分时图 |
| `get_chart_sampling(market, code)` | 分时缩略采样 |
| `get_transactions(market, code, ...)` | 逐笔成交 |
| `get_symbol_info(market, code)` | 个股特征快照 |
| `get_board_list(board_type, ...)` | 板块列表 |
| `get_board_members(board_symbol, ...)` | 板块成分股报价 |
| `get_belong_board(market, code)` | 个股所属板块 |
| `get_capital_flow(market, code)` | 资金流向 |
| `get_auction(market, code)` | 集合竞价 |
| `get_unusual(market, ...)` | 市场异动 |
| `get_server_info()` | 服务器交易时段 |
| `get_kline_offset(offset, count)` | K 线偏移信息 |
| `get_goods_list(market, ...)` | 扩展市场商品列表 |

### MacExClient / AsyncMacExClient

| 方法 | 说明 |
|------|------|
| `goods_count(market)` | 商品总数 |
| `goods_list(market, start, count)` | 商品列表 |
| `goods_quotes(stocks, fields)` | 批量报价 |
| `goods_quotes_list(market, ...)` | 市场分类报价列表 |
| `goods_kline(market, code, period, ...)` | K 线（支持复权） |
| `goods_tick_chart(market, code, ...)` | 分时图 |
| `goods_chart_sampling(market, code)` | 分时缩略采样 |
| `goods_transaction(market, code, ...)` | 逐笔成交 |

### TdxClient / AsyncTdxClient

| 方法 | 说明 |
|------|------|
| `get_security_count(market)` | 市场证券总数 |
| `get_security_list(market, start)` | 证券列表（分页） |
| `get_security_list_all()` | 沪深 A 股完整列表（含行业） |
| `get_security_quotes(stocks)` | 批量五档行情 |
| `get_security_bars(market, code, ...)` | 个股 K 线 |
| `get_index_bars(market, code, ...)` | 指数 K 线 |
| `get_minute_time_data(market, code)` | 今日分时 |
| `get_history_minute_time_data(market, code, date)` | 历史分时 |
| `get_transaction_data(market, code, ...)` | 当日逐笔成交 |
| `get_history_transaction_data(...)` | 历史逐笔成交 |
| `get_fund_flow(market, code)` | 当日资金流向 |
| `get_history_fund_flow(market, code, ...)` | 历史资金流向 |
| `get_xdxr_info(market, code)` | 除权除息历史 |
| `get_finance_info(market, code)` | 最新财务数据 |
| `get_company_info_category(market, code)` | 公司信息目录 |
| `get_company_info_content(...)` | 公司信息文本 |
| `get_block_info(filename)` | 板块信息 |
| `get_report_file(filename)` | 下载服务器文件 |
| `get_market_stat()` | 全市场涨跌统计 |
| `get_price_limits(market, code, name, pre_close)` | 涨跌停价 |

## 架构

```
src/tdxman/
├── client.py          # TdxClient / AsyncTdxClient（标准协议）
├── unified.py         # UnifiedTdxClient（统一入口）
├── config.py          # 服务器地址、端口、超时配置
├── mac/
│   ├── client.py      # MacClient / AsyncMacClient（MAC 协议）
│   ├── enums.py       # Period, Adjust, Category, ExMarket, SortType, ...
│   ├── models.py      # MacBar, MacQuoteField, MacTick, BoardInfo, ...
│   └── commands/      # MAC 命令（build_request + parse_response，无 IO）
├── ex/
│   ├── client.py      # ExTdxClient / AsyncExTdxClient（标准协议扩展市场）
│   ├── mac_client.py  # MacExClient / AsyncMacExClient（MAC 协议扩展市场）
│   └── transport/     # ExTdxConnection（端口 7727）
├── transport/
│   ├── sync.py        # TdxConnection + ping_host / ping_all
│   └── async_.py      # AsyncTdxConnection（asyncio）
├── commands/          # 标准协议命令（无 IO）
├── codec/             # price / volume / datetime / frame / bitmap 编解码
├── models/            # 纯 dataclass，无业务逻辑
├── offline/           # 离线数据读取模块
└── cli/               # tdxman CLI（click）
```

commands 层不依赖 transport，可独立单测。

## aspool：股票与指数数据池

`aspool` 与 `tdxman` 一起安装，代码位于 `src/aspool`。默认数据池是 `~/.aspool`，
可在各命令中使用 `--root PATH` 指向其他池；同一工作流的所有命令应使用同一个 root。
`settings/config.yaml` 的 `aspool.free_stockdb.root` 是 **free-stockdb 导入源目录**，
`offline.vipdoc` 是通达信本地行情目录，都不是数据池输出路径。

Fundwise 通过公开 `DataPool` API 读取股票和指数，见 [aspool API](docs/design/aspool_api.md)。
数据域分层需求与物理设计见
[数据域分层需求](docs/design/data_platform_v2_requirements.md)和
[数据域分层设计](docs/design/data_platform_v2_design.md)。当前已提供可恢复的影子迁移、核验和显式激活，
执行状态见 [数据域分层实施记录](docs/achievements/platform_v2_execution.md)；生产运行方式仍以
[生产数据流契约](docs/design/production_data_flow_contract.md)为准。
新版项目数据统一位于 `data/`：股票使用 `stocks.sqlite`，指数使用独立的
`indices.sqlite`，ETF 使用 `etfs.sqlite`；分层布局另使用 `fundamentals.sqlite`、
`adjustments.sqlite`、`features.sqlite` 和 `snapshots.sqlite`。公开读取只通过 `DataPool`，
由 `catalog.duckdb:pool_metadata.layout_version` 选择完整布局。
当前项目生产池已全部退役 `lake/` 分区，见 [ETF 与辅助数据迁移验收](docs/ops/etf_reference_sqlite_migration.md)。
指数保留 `aspool update|sync --type index` 和 `read_index_daily` /
`list_indices` 接口；迁移步骤、增量边界与验收见 [指数 SQLite 迁移](docs/ops/index_sqlite_migration.md)。
五年涨停事件及当日成交额使用 `DataPool.iter_limit_events_with_amount()` 分批读取；
接口、缓存示例和资源边界见 [aspool API](docs/design/aspool_api.md#9-已发布事件与当日成交额的有界读取)。

### 首次准备（已有数据池可跳过）

```bash
source .venv/bin/activate

# 一次性导入未复权股票日线并检查完整性
aspool import --source free-stockdb --period daily

# 按需单独导入共享复权因子，不导入分钟线
aspool import --source free-stockdb --factor

# 检查指数名单差异；--write 用于手动更新正式清单
python scripts/maintain_board_lists.py
python scripts/maintain_board_lists.py --write

# 指数可从空池直接建立，获取全部可用日线历史
aspool sync --type index --source tdx --tdx-mode online --period daily
```

`init` 只创建存储结构，不能代替股票历史导入。股票在线 `sync` 每七天刷新一次
沪深北 A 股目录；新增代码自动获取最长可用日线，服务器尚未提供 K 线的代码保留为待初始化并在后续同步重试。
日常流程无需重复从 free-stockdb 导入，也不处理分钟历史。

### Daily 工作流：每日收盘后同步股票、ETF 和指数

建议在交易日 **北京时间 15:30 之后** 执行，例如16:00。`update` 拒绝周一至周五
09:00～15:30（含端点）运行；更新限制在内部按上海时区判断，以下同时设置命令日志时区。
该限制按星期判断，并非节假日交易日历。`sync` 没有相同时间限制，盘中调用可能保存未收盘日线。

在已有股票日线池的项目根目录执行统一命令：

```bash
source .venv/bin/activate
export TZ=Asia/Shanghai

aspool update --type all --source tdx &&
aspool status
```

顺序含义：

1. 完整读取股票和 ETF 当日目录，发布到 `catalog.duckdb:securities`。
2. 指数 `update` 使用 MAC 日 K 线增量补齐，并从上证指数实际日期维护交易日历；广度用 `breadth_status` 区分可用和不可用，不用 `0/0` 冒充。
3. 股票 `update` 使用 quote 保存当日量比、换手率，原子计算涨跌停、连板和日级市场汇总；只对昨收变化的疑似除权股票查询事件。周/月 schema 与接口已预留，writer 尚未投产。
4. ETF 增量同步到独立 `etfs.sqlite`；K 线为空时用带日期报价确认无交易，不制造零值日线。
5. 输出统一报告。基本面已从每日 `all` 流程移除，只由 `aspool fundamentals update` 手动维护；
   BaoStock 仅由 `--source baostock` 显式选择，不自动切源。详见
   [完整数据与 CLI 契约](docs/design/production_data_flow_contract.md)。

股票漏更或需要修补时，再先执行：

```bash
aspool sync --root data --type all --source tdx
aspool update
```

不传日期时缺省检查最近 10 个已完成交易日；可用 `--count 30` 扩大到最多 60 个交易日，
也可继续使用互斥的 `--start/--end` 明确窗口。股票 sync 补算缺失量比和换手率，再更新同一套
逐股派生与日汇总。

基本面和分层布局使用独立入口：

```bash
aspool fundamentals update --root data
aspool fundamentals status --root data

# prepare 只建立影子库；verify 通过后才允许显式 activate
aspool platform prepare --root data
aspool platform verify --root data
aspool platform activate --root data
# 激活后发现兼容问题时立即恢复旧读取，影子文件保留
aspool platform rollback --root data
```

`DataPool.read_daily(..., adjust="qfq"|"hfq")` 和 ETF 同名读取的复权参数是新增可选能力；
不传 `adjust` 时继续返回未复权价格。

股票日线 `sync` 同时补齐日期股本、参考价、收盘量比、换手率和市值，再重算涨跌停和连板。
BaoStock 用于冲突样本的只读算法校对；详见 [日线字段补齐](docs/design/daily_enrichment.md)。
旧文件池的 `--enrich-only` 不适用于新版 SQLite；较长历史由 ops 分窗维护。
可手动运行 `aspool directory --type stock|etf` 刷新目录；`aspool universe` 仅保留为隐藏兼容别名。

BaoStock 可单独查询或修复指定历史区间：

```bash
tdxman quote 600519.SH --count 30 --source baostock --format table
tdxman symbol-info 601091.SH --source baostock
aspool sync --root data --type stock --source baostock --status missing
aspool sync --root data --type stock --source baostock --status missing \
  --start 2026-09-23 --end 2026-09-24
```

新版 `aspool update --source baostock` 显式选择备用源，不再读取配置自动追加 BaoStock；
`--retries 2 --retry-delay 1` 控制单标的有限读取重试；连续三只重试耗尽后默认熔断，可用 `--max-consecutive-failures` 调整。建议16:00后运行，并核对源端实际数据日期。
BaoStock 只支持沪深 A 股，首次串行补齐可能较慢；缺失会话、源间冲突和连板未知都会保留在报告中。
详见 [BaoStock 数据源、合并规则和覆盖边界](docs/ops/baostock.md)。

新版 update 报价默认使用4个独立连接；`--workers 1` 为串行，最多8个连接。
SQLite 股票历史 sync 与 BaoStock 固定串行；两条路径的写事务串行执行。旧文件池 sync 的异步选项不适用于新版 SQLite 股票路径；指数 SQLite sync 支持异步读取，写事务仍串行执行。

```bash
aspool update --root data --workers 4
aspool sync --type index --source tdx --period daily --async --workers 4
```

`&&` 在失败时停止后续步骤。查看命令输出的未返回、拒绝和失败数量；无数据不代表已经更新。
新版 SQLite 的运行报告位于数据根目录同级的 `.local/reports/`；旧文件池股票报告位于
`ROOT/reports/maintenance/`，旧指数报告位于 `ROOT/reports/index-sync/`。
过期报价、无日期报价和非法日线被拒绝，保留原有记录；没有变化的文件跳过重写。
新版 SQLite 仅写入新增或变化的键；尚未迁移的旧文件池仍使用每证券一个 Parquet 文件，
变化时重写文件，较早历史在 Arrow 中保留。

### 同步后检查与 Fundwise 读取

```python
from aspool import DataPool

pool = DataPool('~/.aspool')  # 与同步命令的 --root 保持一致
print(pool.status())         # 股票覆盖；指数用 list_indices()
indices = pool.list_indices()
print(indices[['symbol', 'name', 'start', 'end', 'row_count']].to_string(index=False))

# 换成已确认收盘且已同步的交易日期；不能简单把自然日当作交易日
closed_day = '2026-09-16'
benchmark = pool.read_index_daily(
    symbols=['SH.000300', 'SZ.399001'], end=closed_day, lookback=120,
    fields=['symbol', 'date', 'close', 'volume', 'amount', 'up_count', 'down_count'],
)
print(benchmark.tail())
```

应检查指数清单覆盖数量及各标的 `end` 是否达到目标交易日，股票也可能因停牌或源缺失而落后。
消费端以明确的已收盘 `end` 读取，避免使用当日未完整数据。指数0/0家数可能表示缺失；
指数成交量保留源口径，不能当作股票股数。公开接口提供的是可变数据池，不保证不可变回测版本或历史时点成分。

### 按需维护

```bash
# 离线修补：先在通达信中下载最新日线；只读 vipdoc，不保证在线最新
aspool sync --type stock --source tdx --tdx-mode offline --period daily
aspool sync --type index --source tdx --tdx-mode offline --period daily

# 在线日线 sync 和 update 均支持 --async / --workers；update 仍只维护日线
aspool sync --type index --source tdx --period daily --async

# 按需刷新低频快照；日常行情 update 不自动刷新基本面
aspool fundamentals update --root data

# 只有需要重新校准股票完整历史时才使用 free-stockdb
aspool sync --type stock --source free-stockdb --period daily
```

指数仅支持 `--source tdx --period daily`。名单包含 HY、HY2、GN、FG 和 ZS 常用指数，
排除名称以“昨日”开头的记录；名单脚本默认预览新增、删除、变化，`--write` 才更新配置。
名单删除不删除已存历史，目录 API 返回实际保存的指数。

股票原始 K 线不复权；复权因子独立保存。新版指数日线存放于独立的 `indices.sqlite`；
`lake/indices/daily` 仅用于尚未迁移的旧文件池。
详见 [指数设计](docs/design/aspool_index_design.md)、[验收报告](docs/design/aspool_index_validation.md)
及 [Fundwise 指数接口](docs/design/aspool_api.md#6-指数读取接口已实现)。同步与异步的实测对比见 [性能验证报告](docs/audit/aspool_performance.md)。

## 开发

```bash
python -m pytest tests/unit/ -v                             # 单元测试（无需网络）
XMTDX_LIVE=1 python -m pytest tests/integration/ -v        # 集成测试
mypy src/                                                    # 类型检查
ruff check src/ tests/                                       # lint
ruff format --check src/ tests/                              # format check
```

## 致谢

- [pytdx](https://github.com/rainx/pytdx) -- 离线数据读取模块借鉴自 pytdx 项目，感谢 rainx 及所有贡献者
- [xmtdx](https://github.com/minionszyw/xmtdx) -- 本项目初始原型
- [mootdx](https://github.com/mootdx/mootdx) -- 工程化封装参考

详见 [NOTICE](NOTICE) 和 [LICENSE](LICENSE)。
