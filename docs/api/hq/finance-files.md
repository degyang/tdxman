# 标准行情：公司资料与文件

[标准行情](README.md) · [精确签名](../signatures.md)

| 方法 | 请求 | 返回 |
| --- | --- | --- |
| `get_finance_info(market, code)` | 单只证券财务快照 | DataFrame，财务和股本指标 |
| `get_company_info_category(market, code)` | 公司资料目录 | DataFrame，含文件名、偏移和长度等目录信息 |
| `get_company_info_content(market, code, filename, offset, length)` | 按目录字段读取一段正文 | 字符串；不是结构化报表 |
| `get_block_info(filename)` | 服务端板块文件名 | 板块成员 DataFrame |
| `get_report_file(filename)` | 服务端文件名 | 原始 `bytes` |
| `get_financial_file_list(host=None)` | 下载 `tdxfin/gpcw.txt` 清单；`host` 可指定财务主机 | DataFrame 列含 `filename`、`hash`、`filesize` |
| `get_financial_file(filename, host=None)` | 清单中的财务文件名 | 原始 ZIP `bytes` |
| `get_financial_records(filename, host=None)` | 下载并解析历史财务 ZIP 中首个 `.dat`；文件名中的八位日期用于报告期 | DataFrame 列含 `code`、`market`、`report_date`、`fields`；无数据时为空表 |

```python
from tdxman import Market, TdxClient

with TdxClient.from_best_host() as client:
    categories = client.get_company_info_category(Market.SH, "600519")
    if not categories.empty:
        row = categories.iloc[0]
        text = client.get_company_info_content(
            Market.SH, "600519", row["filename"], int(row["start"]), int(row["length"])
        )
```

目录列名应先在本机结果中检查，再用于正文请求；服务器差异可能影响返回字段。历史财务文件查询使用财务下载主机，并非当前行情连接直接提供的 `.day` 数据；已下载文件也可用[离线读取](../reader/README.md)中的 `read_history_financial` 解析。对二进制下载请检查长度与清单中的大小/校验信息。此处对应 [tdxpy Crawler](https://tdxpy.readthedocs.io/zh-cn/latest/api/crawler/)所介绍的文件类别，但 API 以 tdxman 为准。

`FinancialRecord.fields` 是财报原始浮点数组，不是已经命名的资产负债表列；使用时需要另外取得字段位置定义。`get_financial_records` 会从 ZIP 的首个 `.dat` 解析，未找到时返回空表；文件名不含八位日期时报告期回退为 `0`。

源码：`src/tdxman/client.py`、`src/tdxman/commands/company_info.py`、`report_file.py`、`finance_info.py` 和 `src/tdxman/codec/financial.py`。
