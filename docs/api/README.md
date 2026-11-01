# tdxman API 与 CLI 文档

本目录按业务入口组织 **tdxman 1.1.6 的 Python API 与 CLI**。核心行情使用 TCP，本地读取器直接读文件，F10 补充服务使用 TQLEX HTTP；项目本身不提供 HTTP REST 服务，因此没有 ageman REST 文档中的统一 Base URL、API Key 或响应信封。参数、返回类型及运行限制以当前 `src/tdxman` 源码为准。

## 选择入口

| 需求 | 文档 | 客户端或命令 |
| --- | --- | --- |
| 连接、服务器、异常、同步与异步 | [连接与通用契约](connections.md) | 所有在线客户端 |
| 7709 标准 A 股行情、公司资料和财务文件 | [标准行情](hq/README.md) | `TdxClient` / `AsyncTdxClient` |
| MAC A 股报价、复权 K 线、板块、竞价和远程文件 | [MAC 行情](mac/README.md) | `MacClient` / `AsyncMacClient` |
| MAC 报价字段选择与列名 | [字段位图](mac/fields.md) | `FieldBit` / `PresetField` |
| 7727 标准扩展行情 | [扩展行情](exhq/README.md) | `ExTdxClient` / `AsyncExTdxClient` |
| 7727 MAC 扩展市场 | [扩展行情](exhq/README.md) | `MacExClient` / `AsyncMacExClient` |
| 通达信 vipdoc 本地文件 | [离线读取](reader/README.md) | `tdxman.offline` |
| 统一路由、交易日、F10、BaoStock | [辅助服务](services/README.md) | 服务对象与补充源 |
| Tick Stock Panel 集成 | [适配器](services/tick-stock-panel.md) | `TdxmanProvider` 与 `ProviderConfig` |
| 枚举、模型字段和单位 | [类型与字段](models.md) | `tdxman` 导出模型 |
| 全部枚举值 | [枚举取值表](enums.md) | 市场、周期、分类、排序与过滤 |
| 完整模型字段名 | [字段索引](schema-fields.md) | 标准、MAC、扩展与离线 dataclass |
| 标识、配置与底层工具 | [实用工具](utilities.md) | `symbol`、`config`、`bestip`、涨跌幅规则 |
| 能力审理与数据来源 | [能力矩阵](guides/README.md) | tdxman、aspool 与数据库逐表来源 |
| 终端操作 | [CLI 总入口](cli/README.md) | `tdxman` / `tdxman ex` |
| 全部公开方法签名 | [签名总表](signatures.md) | 同步、异步及代理方法 |
| 上游示例迁移 | [tdxpy 对照](tdxpy-mapping.md) | 参数顺序、返回类型与文件接口 |

Python 快速开始：

```python
from tdxman import MacClient, Market, Period, Adjust

with MacClient.from_best_host() as client:
    bars = client.get_stock_kline(Market.SH, "600519", Period.DAILY,
                                  count=30, adjust=Adjust.QFQ)
    print(bars.tail(3))
```

CLI 快速开始：

```bash
tdxman kline 600519.SH --period DAILY --count 30 --adjust QFQ --format table
```

## 与 tdxpy 文档的关系

上游 [标准行情](https://tdxpy.readthedocs.io/zh-cn/latest/api/hq/)、[扩展行情](https://tdxpy.readthedocs.io/zh-cn/latest/api/exhq/)、[Reader](https://tdxpy.readthedocs.io/zh-cn/latest/api/reader/) 和 [历史财务 Crawler](https://tdxpy.readthedocs.io/zh-cn/latest/api/crawler/)可帮助理解通达信协议和文件种类。**tdxpy 的类名、位置参数顺序、list/`to_df` 返回方式及重试开关不能直接用于 tdxman**。例如 tdxpy 的 K 线示例以 `category` 开头，而 `TdxClient.get_security_bars` 以 `market` 开头；tdxman 标准/MAC 客户端的大多数查询直接返回 `pandas.DataFrame`。具体差异见各域页面。

`aspool` 是本仓库另一个本地数据池 CLI/Python 包，不属于这些 `tdxman` 接口；其当前能力与存储来源见 [aspool 能力矩阵](guides/aspool-capabilities.md) 和 [数据库逐表矩阵](guides/aspool-database-capabilities.md)。

## 使用边界

- 对单只证券明确给出市场和代码。`Market.SH/SZ/BJ` 分别为 1/0/2；`600519.SH` 是 CLI 标识，Python API 通常传 `(Market.SH, "600519")`。
- `start` 多数是从最新记录起的**偏移**，不是日期；确切含义与单页上限见目标方法。历史日期有 `YYYYMMDD` 整数、`date` 对象等多种契约，按方法签名传入。
- DataFrame 可能为空，动态报价列与 `attrs` 可能随请求字段/服务端结果变化。空表不等于停牌、无事件或交易日关闭。
- 研究闭市数据时核对最后时间和交易状态。在线数据可包含当天未完成记录；本地 vipdoc 只反映最后一次下载。

源码依据：`src/tdxman/__init__.py`、`src/tdxman/cli/`、各业务页面注明的具体模块。本文档整理的是当前实现，不声称已通过实时服务器逐接口验收。
