# 生产涨跌停更新与派生交付

执行时间：2026-09-18 23:55 起（北京时间，跨至 09-19）。UP 研发自验；PM 尚未审核冻结。

生产池：`/home/ubuntu/.aspool`，来自 Fundwise `settings/config.yaml` 的 `aspool.root: ~/.aspool`。

## 执行登记

已完成隔离样例、全股票范围单日验证、真实报价更新与自动单日派生、最近五会话发布及生产幂等核验。2026-09-14—18 每日 5581 只，公开事件/汇总/coverage/exceptions/scope 均可读取，窗口内 stale 为空。有限覆盖已实际落库，不代表历史缺口全部补齐。

持久证据目录：`/home/ubuntu/aspool-recovery/20260918T2355/`。每个生产阶段均保留 stdout、stderr、GNU time 峰值 RSS/耗时和包含退出码的 status JSON；后台 supervisor 独立于终端运行。

- 隔离验证：10 passed / 56 deselected，9.19 秒，峰值 RSS 209772 KiB。
- 全范围单日：5581 只，41.44 秒，峰值 RSS 243908 KiB，退出 0，公开发布成功。
- 报价更新：请求 5581，成功 5565，变化 5563，未变化 2，缺失 16，拒绝 0，失败 0。派生成功；整体因缺失返回 partial/退出 1，非 OOM 或派生失败。84.58 秒，峰值 RSS 366432 KiB。
- 修复提交：`45ed9f2687bbbcf262aa7503a7b5f9c17890fc41`；干净暂存树针对性验证 7 passed / 3 deselected，ruff 通过。

## 备份与中断恢复

证据目录中 `backup/` 是首次生产派生前 catalog 与 WAL 副本，`before_update/` 是真实股票报价更新前快照。后者包含股票原日线硬链接、catalog 独立副本、fundamentals 与 reports 副本及 `manifest.json`。已核对日线写入采用临时文件后原子替换，快照不会随目标覆盖而改变；没有复制全市场历史内容，没有删除原始数据。

如仅派生中断，原始成功写入保留，使用 `DataPool.compute_limit_events` 独立重试，不重新联网覆盖。需回滚时先停止并排除其他写入者，持有 `pool_lock(root, write=True)`；将当前 catalog/WAL 另行移存留证，再恢复所选快照 catalog 与匹配 WAL。回滚报价更新还应按 manifest 将备份日线复制到临时文件再原子替换，恢复 fundamentals 与维护报告；不要原地修改硬链接备份，不混用不同时点 catalog 与日线，不自动回滚此后其他任务的更新。恢复操作尚未在生产执行。

## 代码范围

承接 PM 的 fetch.py 连接重试关闭、fundamentals.py 成功日期/持久 deriving/partial 恢复、limit_events.py 单股历史有界读取及相关测试。没有恢复全市场历史字典。

同日名称 ST 已在 `101fcf4`；既有已验收成果提交为 `a78fb49`，证券规范输出补正为 `fc5da06`，普通 settings 为 `ecf9eec`，此前已完成本地整理。本轮保留其他 ETF、指数、扩展域、实时统计、文档和性能改动；test_aspool_performance.py 只逐块纳入日期拒绝测试，不混入其余测试。未 push。

## 最终验证与资源结果

| 阶段 | 范围 | 耗时 | 峰值 RSS | 退出码/结果 |
|---|---|---:|---:|---|
| 隔离样例 | 更正传播、失败重试、汇总、幂等 | 9.19 秒 | 209772 KiB | 0；10 passed |
| 全范围单日 | 5581 只，09-18 | 41.44 秒 | 243908 KiB | 0；已发布 |
| 报价更新+自动派生 | 请求 5581 只 | 84.58 秒 | 366432 KiB | 1；缺报价导致 partial，派生 ok |
| 五会话 | 5581 × 5 | 134.17 秒 | 280352 KiB | 0；五日均 published |
| 干净提交树重复最新日 | 5581 只，09-18 | 36.34 秒 | 253292 KiB | 0；生产幂等通过 |

单日成功且内存稳定后才扩大日期，整个过程没有并行派生作业或 OOM。五日逐日计算阶段峰值约 238032→262976→262976→280352→280352 KiB，没有恢复全市场历史缓存。多日仍重复读取单股历史。

PM 的既有 77 项及后续恢复测试记录按 [PM 修复说明](../achievements/limit_memory_recovery_fix.md)采信，不重复汇总为本轮独立测试数。本轮隔离确认命令：

```bash
.venv/bin/python -m pytest tests/unit/test_limit_events.py -q \
  -k 'correction or retry or sealed_ratio or rerun_is_idempotent or publish_pointer'
```

本轮没有新增生产算法。为确认逐块 Git 整理不依赖未提交功能，从暂存树导出干净代码，运行 memory_recovery 与 performance 中连接回收、失败写入、持久恢复和日期拒绝共 7 项，全部通过；干净树公开读取与工作区六类结果逐项相等，最新日复算亦使用该干净代码。两次生产快照比较仅排除 coverage.computed_at，summary/events/exceptions/scope/coverage/staleness 完全一致；无重复事件或发布批次。事件标志汇总、触及未封计数、封板率、状态数量守恒、范围数和批次一致性逐日断言通过。

Fundwise 自身虚拟环境调用 `read_environment_limits` 已真实只读读取五天数据，退出 0；该联调仅证明消费者数据适配可读，不代表页面视觉或 NS-R2 评分验收。首次误用 tdxman 虚拟环境缺少 requests 的导入失败单独留存，改用 Fundwise 环境后成功，未安装依赖或修改消费者代码。

## 生产更新及时间边界

原始范围更新前后均为股票 5581 只，其中 5565 只尾日为 2026-09-18，16 只仍停留在更早日期；本次主要补充同日 ST 输入，不虚报日期推进。更新前 publication 为 0、stale 为 9 个日期；更新后 publication 为 5、窗口内 stale 为 0。范围由现有股票目录及资产类型确定，未扩 ETF 或其他资产。

真实报价抽查于 09-18 23:56:49 完成，服务器交易日期均为 20260918，服务器时间为 15:33:09（贵州茅台 15:30:52）。全范围报价任务实际启动 23:57:31，完成于 23:58:52，均在同一北京时间日且收盘后；未传入伪造 now。跨至 09-19 后只进行本地五会话派生和复算，没有跨日抓取名称回填。

维护报告：`~/.aspool/reports/maintenance/7c40e06412c647baa6fbe658a78f4105.json`，证据目录保留 `update.report.json` 副本。报告最终 `status=partial`、`limit_events.status=ok`，5565 行 pre_close/is_st 联合有效。16 个未返回报价：000004、000022、000043、002808、002898、300029、300114、600193、600421、600599、600608、600636、600696、600849、605081、920305；这些不自动解释为停牌或退市。

## 逐日真实结果

下表均为已确认子集计数；0 不表示未知部分没有事件。每行范围均为 5581，NO_LIMIT 均为 0。封板率为已确认收盘涨停/触及涨停，不能称全市场封板率。

| 日期 | KNOWN | UNKNOWN | INVALID | 收盘涨/跌停 | 触及涨/跌停 | 触及未封涨/跌 | 封板率 | 已知最高连板 |
|---|---:|---:|---:|---|---|---|---|---|
| 2026-09-14 | 0 | 5581 | 0 | 0/0 | 0/0 | 0/0 | null | null |
| 2026-09-15 | 0 | 5581 | 0 | 0/0 | 0/0 | 0/0 | null | null |
| 2026-09-16 | 1997 | 3570 | 14 | 5/0 | 6/0 | 1/0 | 83.33% | 0 |
| 2026-09-17 | 2308 | 3273 | 0 | 2/0 | 4/0 | 2/0 | 50.00% | 1 |
| 2026-09-18 | 5551 | 18 | 12 | 52/4 | 75/7 | 23/3 | 69.33% | 1 |

09-14/15 全 UNKNOWN，因此事件计数为 0、封板率和最高连板为 null。09-16 最高连板 0 仅来自已知非涨停证券，不将未知连板归零。09-18 的 52 个收盘涨停中仅 1 个连板数确定为 1，另 51 个为 null；已知最高 1 不代表市场最高连板。五日都在收盘后计算，但五日数据覆盖均不完整，不标为“全市场日终完整”。

批次为 `daily-limit-YYYYMMDD-stock`，规则版本 `cn-a-share-limit-v2`，每日 publication、summary、coverage、events/exceptions/scope 指向同一批次，status=published，stale=false。catalog 时间戳为 UTC，执行日志的带 +0800 时间为北京时间。

## 未知与无效原因

| 日期 | 无历史 ST 依据 | 无可靠 pre_close | 无当日日线 | 上市窗口无法确认 | INVALID 非正 open |
|---|---:|---:|---:|---:|---:|
| 2026-09-14 | 3189 | 2358 | 31 | 3 | 0 |
| 2026-09-15 | 3186 | 2359 | 33 | 3 | 0 |
| 2026-09-16 | 3186 | 363 | 17 | 4 | 14 |
| 2026-09-17 | 3198 | 57 | 16 | 2 | 0 |
| 2026-09-18 | 0 | 0 | 16 | 2 | 12 |

异常表按规则最先阻断原因记录，因此“无可靠 pre_close”数量不等于底层全部缺值数量。逐股读取真实原始数据另核对如下：

| 日期 | 存在日线 | pre_close 有值/缺失 | is_st 有值/缺失 |
|---|---:|---|---|
| 2026-09-14 | 5550 | 0/5550 | 0/5550 |
| 2026-09-15 | 5548 | 0/5548 | 0/5548 |
| 2026-09-16 | 5564 | 5186/378 | 0/5564 |
| 2026-09-17 | 5565 | 5494/71 | 0/5565 |
| 2026-09-18 | 5565 | 5565/0 | 5565/0 |

09-14—17 没有可靠同日 ST 字段；09-14/15 全部缺前收，09-16/17 尚有 378/71 行缺前收。当前名称、前一根 close 均未用于补齐。继续填补需可靠历史风险警示生效证据和当日参考价来源，本包不新建历史身份平台。

09-18 上市窗口未知为 601091.SH、688837.SH；INVALID 为 000016.SZ、002731.SZ、301139.SZ、600301.SH、600825.SH、601059.SH、601198.SH、601238.SH、601995.SH、603400.SH、605303.SH、688496.SH，原始 open 非正。保留源数据及异常，不自行猜测正常价格。

窗口之外 2026-07-09、07-13、07-16、07-29 的旧失败 stale 原样保留，未删除、未冒称全池无 stale，也未扩至历史重建。

## 真实标的抽查

以下均为 09-18 原始报价落库行；is_st_source 均为 `tdxman:quote_name`，is_st_name_date 均为 2026-09-18。价格展示到分，完整浮点原值及 OHLC 保留在 `raw_audit.json`、`quote_probe.stdout`。

| 标的 | 原始名称 | is_st | pre_close | close |
|---|---|---|---:|---:|
| 000001 | 平安银行 | False | 11.61 | 11.70 |
| 000010 | *ST美丽 | True | 1.56 | 1.58 |
| 000078 | ST海王 | True | 1.52 | 1.52 |
| 300001 | 特锐德 | False | 31.84 | 32.50 |
| 600519 | 贵州茅台 | False | 1266.98 | 1257.12 |

额外事件抽查（`event_samples.json`）：000410.SZ 沈阳机床 pre_close=5.06、close/high=5.57、涨停价=5.57，close_limit_up=true，consecutive_up=null；688292.SH 浩瀚深度 pre_close=19.18、close/high=23.02、涨停价=23.02，close_limit_up=true，consecutive_up=1。前者缺连板历史没有被错误当首板。

## 可复跑入口与证据

仅在真实报价日期等于执行北京时间日期且已收盘时更新：

```bash
.venv/bin/aspool update --root /home/ubuntu/.aspool --workers 4
```

非交易日/跨日旧报价仍会被 quote_name_date_unconfirmed 拒绝；无须联网重写原始数据即可独立重算：

```python
from datetime import date
from aspool.pool import DataPool
pool = DataPool('/home/ubuntu/.aspool')
pool.compute_limit_events([date(2026, 9, d) for d in range(14, 19)])
for name in ('summary', 'coverage', 'events', 'exceptions', 'scope', 'staleness'):
    print(getattr(pool, 'read_limit_' + name)(start='2026-09-14', end='2026-09-18'))
```

实际持久运行封装与参数保存在证据目录 `run_stage.py`、`derive.py` 及每阶段 status JSON；`single/update/five/repeat.{stdout,stderr,time,status.json}` 记录进度、错误、内存、退出码，`sample.*` 与 `clean.*` 记录测试。`final.json` 和 `final.audit.stdout` 包含完整公开读取及守恒验证，`idempotence.txt` 记录幂等比较，`consumer_fundwise.stdout` 是 Fundwise 环境真实读取。生产数据、备份、机器运行日志均未纳入 Git。

当前任务已完成生产可计算部分与交付；历史来源缺口、窗口外旧 stale 和 PM 复核仍保留。UP 自验不代替 PM 验收，不代表完整评分、页面视觉、实时行情语义或策略有效性通过。
