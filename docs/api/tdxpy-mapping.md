# tdxpy 示例到 tdxman 的对应关系

[API 总入口](README.md) · [精确签名](signatures.md)

用户提供的 [tdxpy 标准行情](https://tdxpy.readthedocs.io/zh-cn/latest/api/hq/)、[扩展行情](https://tdxpy.readthedocs.io/zh-cn/latest/api/exhq/)、[Reader](https://tdxpy.readthedocs.io/zh-cn/latest/api/reader/) 与 [Crawler](https://tdxpy.readthedocs.io/zh-cn/latest/api/crawler/)用于辨认协议与文件种类。下表只做入口对照；实际调用参数、模型和异常以本仓库源码及本目录业务页为准。

| tdxpy 示例入口 | tdxman 入口 | 迁移时核对 |
| --- | --- | --- |
| `TdxHq_API` 标准 7709 | [`TdxClient` / `AsyncTdxClient`](hq/README.md) | tdxman 多数查询直接返回 DataFrame，无需 `to_df()`；连接用客户端上下文 |
| `get_security_bars(category, market, code, start, count)` | `get_security_bars(market, code, category, start, count)` | 前三个位置参数顺序不同，周期用 `KlineCategory` |
| `get_history_transaction_data(market, code, start, count, date)` | `get_history_transaction_data(market, code, date, start, count)` | 日期在偏移之前；`date` 为 `YYYYMMDD` 整数 |
| `get_and_parse_block_info(filename)` | `get_block_info(filename)` | 返回已解析 DataFrame；要原始文件可用 `get_report_file` |
| `TdxExHq_API` 标准 7727 | [`ExTdxClient` / `AsyncExTdxClient`](exhq/standard.md) | 标准 EX 仍主要返回 dataclass 列表、字典列表或单个模型；无隐式 DataFrame 转换 |
| `TdxDailyBarReader().get_df(path)` | [`read_daily_bars(path)`](reader/README.md) | 返回 `list[SecurityBar]`，需自行转表；目录可用 `find_daily_bar_file` 定位 |
| `HistoryFinancialListCrawler.fetch_and_parse()` | `TdxClient.get_financial_file_list()` | tdxman 通过财务下载主机读 `tdxfin/gpcw.txt`，返回 `filename/hash/filesize` 表 |
| `HistoryFinancialCrawler.fetch_and_parse(...)` | `get_financial_file(filename)` / `get_financial_records(filename)` | 前者给 ZIP 原始字节，后者给已解析 DataFrame；参数不是 tdxpy 的 `reporthook`/`path_to_download` |
| `HistoryFinancialReader().get_df(path)` | [`read_history_financial(path)`](reader/README.md) | 本地 `.zip` / `.dat` 返回 `list[FinancialRecord]` |

tdxman 另有 [MAC 行情](mac/README.md) 与 [MAC 扩展行情](exhq/mac.md)，提供复权 K 线、字段位图报价和 CLI 的主要数据路径。不要把 tdxpy 的 `raise_exception`、`auto_retry`、`heartbeat`、`multithread` 参数直接传入 tdxman；连接、自动重连与心跳参数见[连接契约](connections.md)。
