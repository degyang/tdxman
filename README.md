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

CLI 帮助页面遵循 [CLI 帮助风格规范](docs/cli_help_style.md)。

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

## aspool：A 股长期数据池

Fundwise 通过公开 `DataPool` API 直接读取数据池，见 [aspool API](docs/aspool_api.md)。

`aspool` 与 `tdxman` 一起安装，代码位于 `src/aspool`。默认路径由 `settings/config.yaml` 的 `aspool.free_stockdb.root` 设置。

```bash
# 一次性导入未复权历史数据；分钟线仅在需要时导入
aspool import --period daily
aspool import --period minutes

# 单独导入日线和分钟线共用的复权因子
aspool import --factor

# 日常更新已导入的全表；tdx 同步只修补已导入标的
aspool update
aspool sync --source tdx --tdx-mode online --period daily
aspool sync --source tdx --tdx-mode offline --period daily

# 手动全量维护财报类基本面快照；建议在财报披露后按周或按月执行
aspool fundamentals

# 首次建立数据池或以 free-stockdb 重新校准历史后补齐在线行情
aspool sync --source free-stockdb --period daily
aspool status
```

原始 K 线不复权；前复权和后复权由独立复权因子计算。

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
