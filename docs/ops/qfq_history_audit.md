# 五年基础行情与 Enriched 修补

下载的 qfq OHLC 属于基础数据，唯一生产表为 `stocks.sqlite.stock_qfq_bars`。
读取入口为 `DataPool.read_downloaded_qfq(start=..., end=..., symbols=...)`；
基础增量数据集为 `stock-qfq-bars`。采集暂存、日志与恢复点放池外的 `.local`。

## 执行

先建立并验证一致恢复点（已有同一修复任务的恢复点可继续使用）：

```bash
python -m aspool.sqlite_state_audit --root data \
  --start 2021-10-01 --end 2026-09-30 \
  --report-dir .local/reports/qfq-five-year/state-before \
  --prepare-recovery --recovery .local/recovery/qfq-five-year
```

采集、对缺失证券尝试备用来源、修补并复核：

```bash
python -m aspool.qfq_audit --root data \
  --start 2021-10-01 --end 2026-09-30 \
  --report-dir .local/reports/qfq-five-year \
  --phase all --workers 4 --unknown-threshold 5 \
  --recovery .local/recovery/qfq-five-year
```

阶段可单独选择 `collect`、`fallback`、`gaps`、`audit`、`repair`、`resolve`。
`fallback` 补取 MAC 不提供的证券并下载来源上市日期；`gaps` 对照基础表与 Enriched
找出缺行情日期，补取包括明确停牌状态在内的来源观测。采集重跑跳过已完成证券，
`--retry` 强制重新下载；修补重跑重新检查输入，仅写真实差异。中断后的相同窗口、
报告目录和恢复点可继续执行 `repair`。维护中公共读取暂时被一致性门禁阻止，
完成后恢复；不能手工删除维护标记来跳过未完成修复。

`all` 在初次修补后运行 `resolve`：对价格超出规则区间的异常，核对另一来源
无复权 OHLC，吻合后补入真实参考价与历史 ST，以基础增量写入并重算依赖，
最后再次检查整个窗口。不同来源价格不吻合时保留冲突，不覆盖事实。
`conflict-resolution.json` 记录处理结果；仍有冲突时最终状态为 `needs_analysis`。

## 计算和检查

- 有有效 OHLC、正成交量的来源事实，须同步维护交易状态。
- 明确来源停牌记录维护 `SUSPENDED`；来源零成交量记录维护 `NO_TRADE`。
  有上市日期证明尚未上市的占位行维护 `NOT_LISTED`，不补造 K 线。
- 优先采用来源提供的当日参考价和历史 ST；其次用同次下载的无复权与 qfq
  OHLC 推导参考价。现金分红有平移，不能只用收盘价比例代替完整映射。
- 无法可靠推导时允许前次实际收盘价估算，并标记 `estimated:`；没有 ST 依据
  时沿用现有非 ST 默认并标记 `assumed:`。这些记录参与统计，单独计数。
- 真正缺行情或无正参考价的记录保留缺失，不补零。无涨跌幅限制与无法判定分开。
- 原始价格纠错时，已有 MA20、量比、连板、市场与板块汇总按依赖更新；本任务不新增指标。
- 每天独立汇总收盘/触及涨跌停事件，与已发布市场汇总比较。
- `daily-quality.json` 给出每日未知数、估算数、状态冲突、价格超出规则区间和汇总差异；
  `anomalies.json` 列明证券和原因。当天无法判定超过 5 只进入分析清单，不能视为验收通过。
  价格超出规则区间也需核查参考价、ST、上市或特殊交易规则，不能简单计为涨跌停。

`collection-report.json`、`fallback-report.json`、`gap-source-report.json` 是来源覆盖证据；`report.json`
是最终核验结果。采集完成不代表生产修补完成。

注册制前主板上市首日还需要发行价，现有 `missing_listing_date` 原因码可能表示
缺少这一首日特殊规则的输入，不能一律解释为目录没有上市日期。北交所继续遵循
既有 Regime 口径，不计入涨跌停事件；它在该接口的 `NO_LIMIT` 标签不表示交易所
实际没有涨跌幅限制。

`689` 科创板存托凭证与 `688` 股票使用同一科创板涨跌幅规则；
依据为[上交所代码分类](https://www.sse.com.cn/aboutus/publication/factbook/documents/c/10170562/files/da4f403a0b7443fa9b2ba2e002d38f10.pdf)
及[科创板交易特别规定](https://www.sse.com.cn/lawandrules/sselawsrules2025/fund/trading/c/c_20260424_10817739.shtml)。

## 恢复及同步

新增表为布局 3 的可选基础表，老池先补建；旧版无此表时基础增量导出视为空集。
修补使用现有逐日多 WAL 持久发布机制，失败事务回滚，中断须先恢复未完成发布。
复制基础数据时包含 qfq 表及带日期的来源事实、目录、日历和历史成分，接收端
使用相同规则重算。仅传原始 K 线不等于复制完整基础数据。
