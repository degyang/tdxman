# ETF 日线同步验收（2026-09-17）

使用当前 MAC `Category.ETF` 目录生成 `settings/etf_list.json`，目录共 1,727 个沪深 ETF 代码。在线异步同步命令为：

```bash
aspool sync --type ex --category ETF --source tdx --period daily --async --workers 8
```

结果：1,688 个 ETF 成功写入 1,504,336 条日线，39 个目录代码未返回 K 线，0 个解析失败；未返回代码保留在目录中并会在后续同步重试。数据池实际范围为 `2010-01-04` 至 `2026-09-17`，没有 ETF 日线早于 `2010-01-01`。最近增量报告为 `reports/maintenance/c5b03808acb54b20a96292e6f47bfdac.json`。

默认数据池核验结果：

- `DataPool.list_etfs()` 返回 1,688 个已存 ETF；
- `DataPool.read_etf_daily()` 返回股票式 OHLCV、成交额和换手率字段，`asset_type=etf`；
- `DataPool.read_index_daily()` 不包含 ETF；
- `DataPool.read_daily()` 的股票读取不包含 ETF；
- `aspool status` 分别显示股票和 ETF 覆盖范围；
- 全量测试 `205 passed, 2 skipped, 6 subtests passed`，Ruff 检查通过。

39 个无 K 线目录代码不是用零值补造；它们会继续作为待初始化目录项保留，直到通达信提供有效历史数据。
