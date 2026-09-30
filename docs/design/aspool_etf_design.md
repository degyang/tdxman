# aspool `ex`/ETF 日线数据设计

## 资产边界

ETF 是上市基金证券，返回的是证券行情：价格、成交量、成交额和换手率。它没有指数日线中的 `up_count`、`down_count`，成交量也按证券份额口径保存。因此 ETF 不写入 `lake/indices`，也不进入 `settings/board_index.json` 的指数白名单。

ETF 是 `ex` 扩展资产域的第一类资产。它使用股票日线的 OHLCV/成交额语义，但通过 `asset_type=etf` 标识，并由独立读取 API 过滤。股票和 ETF 共用日线文件格式，避免重复实现 K 线合并和报价更新；指数仍使用独立的指数格式和 API。

## 清单与同步

ETF 清单由 MAC `Category.ETF` 发现，人工维护脚本生成可审阅白名单；同步只使用已经写入的白名单：

```bash
python scripts/maintain_board_lists.py --target etf
python scripts/maintain_board_lists.py --target etf --write
```

配置文件为 `settings/etf_list.json`，每项包含 `market`、`code`、`name` 和 `source=["ETF"]`。新增 ETF 先预览差异并执行 `--write`，再进入同步队列；不会因为服务器目录变化而静默扩大数据范围。

```bash
aspool sync --type ex --category ETF --source tdx --period daily
aspool sync --type ex --category ETF --source tdx --period daily --async
aspool sync --type ex --category ETF --tdx-mode offline --period daily
aspool universe --type etf
```

首次初始化或新 ETF 建库时，只保留 `2010-01-01` 及以后数据；实际上市晚于该日期的 ETF 从上市首日开始。已有 ETF 只读取最近重叠窗口并增量更新，不重新下载全历史。日线同步支持在线与 vipdoc 离线模式，分钟线暂不在 ETF 扩展范围内。

## Fundwise 读取

```python
from aspool import DataPool

pool = DataPool("~/.aspool")
etfs = pool.list_etfs()
bars = pool.read_etf_daily(
    symbols="SZ.159366",
    start="2010-01-01",
    fields=["symbol", "date", "open", "high", "low", "close", "volume", "amount", "turnover_rate"],
)
```

`read_etf_daily` 返回股票式份额、元和百分比字段，`attrs.asset_type` 为 `etf`；不提供涨跌家数字段。ETF 不会被 `read_index_daily` 返回，指数也不会被 ETF API 返回。
