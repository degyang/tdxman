# 类型、字段与单位

[API 总入口](README.md) · [方法签名](signatures.md) · [完整字段索引](schema-fields.md) · [全部枚举值](enums.md)

## 市场、周期与排序

| 类型 | 常用成员 | 适用范围 |
| --- | --- | --- |
| `Market` | `SZ=0`、`SH=1`、`BJ=2` | 标准 A 股与多数 MAC A 股方法；本地 vipdoc 路径只支持沪深 |
| `KlineCategory` | `MIN_5=0`、`MIN_15=1`、`MIN_30=2`、`MIN_60=3`、`DAY=4`、`WEEK=5`、`MONTH=6`、`MIN_1=7` | 标准 7709 K 线 |
| `Period` | `MIN_5=0`、`DAILY=4`、`WEEKLY=5`、`MONTHLY=6`、`MIN_1=7`、`MINS=8`、`DAYS=9`、`SECONDS=13` | MAC K 线；`times` 用于组合周期 |
| `Adjust` | `NONE=0`、`QFQ=1`、`HFQ=2` | MAC K 线复权 |
| `Category` | `SH=0`、`SZ=2`、`A=6`、`BJ=12`、`CYB=14`、`ETF=0x2AFD` 等 | MAC 报价列表分类；与 `Market` 不可互换 |
| `BoardType` | `HY=0`、`GN=3`、`FG=4`、`DQ=5`、`ALL=255` | MAC 板块目录 |
| `SortType` / `SortOrder` | `CHANGE_PCT` / `DESC`、`ASC`、`NONE` | MAC 列表和板块成员排序 |
| `FilterType` | `NEW`、`KC`、`ST`、`CY`、`BJ` 等位标志 | MAC 报价/板块过滤 |
| `ExMarket` | `ZZ_FUTURES=28`、`DL_FUTURES=29`、`SH_FUTURES=30`、`HK_MAIN_BOARD=31`、`US_STOCK=74` | MAC 扩展市场；实际可用商品以服务器目录为准 |

完整枚举定义在 `src/tdxman/models/enums.py` 和 `src/tdxman/mac/enums.py`。标准 EX 的市场码是整数，`ExMarket` 可作为参考，但不要假设每台服务器均提供全部市场。

## 主要返回模型

| 模型 | 关键字段/单位 | 来源 |
| --- | --- | --- |
| `SecurityBar` | `open/high/low/close` 价格，`vol` 在股票记录通常为股、指数等品种按解码系数解释，`amount` 通常为元；拆分 `year/month/day/hour/minute` | 离线日线/分钟线；在线标准协议会转成 DataFrame |
| `IndexBar` | `SecurityBar` 加上涨/下跌家数 | 标准指数解析 |
| `SecurityQuote` | 市场、代码、价量与盘口字段 | 标准批量报价 |
| `XdxrRecord` | 事件日期、`category`、每股分红/送配比例等；股本变动为万股 | 除权事件 |
| `FinanceInfo` | 股本以万股，资产/负债/利润以元，`updated_date` 为 `YYYYMMDD` | 最新财务快照 |
| `CompanyInfoCategory` | `name`、`filename`、`start`、`length` | 公司资料目录 |
| `FinancialRecord` | `market`、`code`、`report_date`、`fields` | 历史财务文件 |
| `ExInstrumentInfo` / `ExInstrumentQuote` / `ExInstrumentBar` | 合约身份、报价、K 线；字段依具体模型 | 标准扩展行情 |
| `ExDailyBar` | OHLC、量额、`settlement`、日期和 `hk_stock_amount` | 本地扩展 `.day` |
| `F10Response` / `F10Table` | `entry`、`tables`、原始 `raw`；`to_frame()` 可展平 | F10 HTTP 页面 |

对于 DataFrame，字段来自对应模型或 MAC 协议请求字段，列集可能不同。先查看 `frame.columns`，对 MAC 数据再检查 `frame.attrs` 中的完整性和单位；不要把展示格式当成原始数值的单位。扩展市场的 `trade`、`position` 等字段应按品种解释。源码：`src/tdxman/models/`、`src/tdxman/ex/models.py`、`src/tdxman/offline/`。
