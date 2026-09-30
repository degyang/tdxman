# FW-04：SQLite 公开接口与 Fundwise 日频接入

2026-09-28。实现日频接口及消费者接入；全历史日派生已算完。**新版成品已全量复制并安装到 Jakarta，Fundwise SDK/HTTP 消费验收通过；默认生产配置尚未切换。** 见[异机验收](fw04_jakarta_full_copy_acceptance.md)。 周/月和多模型执行仍属 FW-08。

## 运行数据与同步

本机 `tdxman/data/` 仅保留 `stocks.sqlite`、`catalog.duckdb`、`lake/`；连接期间允许 SQLite WAL/SHM。恢复源移到 `.local/recovery/legacy-source/`，迁移报告、日志、复制清单放 `.local/reports/`，小型验收证据在 `docs/evidence/`。已删除可重建样本数据库 18,124,997 字节，未删除唯一恢复源。

全量复制清单共 8,157 个唯一文件、9,480,127,373 字节（已剔除原清单重复 catalog 条目）。股票库 SHA-256：`6eb13bafe07ce0fcc1a24047761dd91b26ba31302dedcc6369276993487b2808`。没有新增写入，复用已完成的源端完整性与数据计数验收。

本次停止源端所有读写，在 tmux 中执行 `bash scripts/ops/copy_full_data.sh`。该脚本完整传输到 Jakarta `.local/receive/data/`，不做活库增量同步。远端 `verify_data_replica.py` 检查完整清单、实际字节哈希、额外文件、路径、侧车文件和只读样本；只在股票库哈希等于已验收源库时复用完整性结果。通过后停远端数据库使用者，将旧 data 移至恢复区，再安装接收目录。用户加载 wsler SSH agent 后已完成传输、哈希校验及目录安装。旧 M0 位于 `.local/recovery/m0-before-fw04-20260928`，不参与当前运行。

## 公开调用

显式根内存在 `stocks.sqlite` 时使用 contract v3；旧根保持旧接口。默认 `~/.aspool` 尚未切换。

```python
from aspool import DataPool

pool = DataPool(root="/mnt/d/Workstation/Services/tdxman/data")
with pool.stock_snapshot() as snapshot:
    market = snapshot.read_market_daily(
        start="2021-09-24", end="2026-09-24", scope="exclude_known_st"
    )
    bars = snapshot.read_daily(
        symbols=["600000.SH"], start="2026-09-01", end="2026-09-24",
        fields=["symbol", "date", "close", "amount"],
    )

with pool.iter_limit_events_with_amount(
    start="2021-09-24", end="2026-09-24",
    fields=["trade_date", "symbol", "close_limit_up", "amount"],
    batch_days=7, max_rows=1000, memory_limit="512MB",
) as batches:
    for frame in batches:
        consume(frame)  # Process and release each frame.
    assert batches.completed
```

主要签名：

```python
DataPool.stock_snapshot()
DataPool.read_market_summary(*, start, end, frequency="D", scope="all_stocks",
                             fields=None, closed_only=True)
DataPool.read_market_daily(*, start, end, scope="all_stocks", fields=None)
StockSnapshot.read_daily(*, symbols=None, start=None, end=None, lookback=None, fields=None)
StockSnapshot.read_daily_features(*, symbols=None, start=None, end=None, fields=None)
DataPool.iter_limit_events_with_amount(*, start, end, symbols=None, fields=None,
    close_limit_up=None, min_consecutive_up=None, batch_days=7, max_rows=25000,
    memory_limit="512MB", threads=2, temp_directory=None)
```

字段裁剪进入 SELECT；事件成交额按股票和日期在 SQL 中关联。流式读取固定同一快照，单日超过 max_rows 时继续分页，不截断；提前关闭时 completed 为 false。普通明细读取有 500,000 行上限。股票别名归一去重。nullable 整数保持整数，避免综合评分将连板数浮点序列化后判为无效。

memory_limit 为 SQLite 缓存配置提示：一半用于页缓存，并非整个 Python 进程的硬限额。threads 保留兼容校验，SQLite 流使用单连接；temp_directory 为兼容参数，此读取路径不创建外部排序文件。D 之外返回 FREQUENCY_NOT_READY，不隐式重算。旧 coverage/exceptions/publication/stale 接口在新根明确移除，不伪造批次。

## 验收及边界

- 已有全库派生：5,586 股票，16,350,085 原始日线、16,350,599 日期特征、12,958 日汇总（6,479 日 × 两种 ST 口径），完整性通过。本轮不重复全库检查。
- 新公开接口 8 项定向测试通过，包括快照隔离、同日跨页、字段/别名、内存提示、整数契约和窄字段异常校验。
- Jakarta 辅助任务 40 项副本校验测试通过（1.43 秒），已接收提交，不重复运行。
- 五年事件/金额：24,608 行、259 页，每页最多 1,000 行，52.154 秒，峰值 RSS 411,104 KiB，≤ 2 GiB。证据：[五年读取](../evidence/sqlite-migration-assessment/20260928-sqlite-read-five-year.json)。
- Fundwise 全历史日频：6,479 日；盈利/韧性各 6,479 日，趋势 6,460 日，投机和综合分 1,727 日，综合分均标记 known_subset。周期阶段 0 日，原因是历史限价/ST/连续性输入不足；没有将未知伪装成零。证据：[日频结果](../evidence/sqlite-migration-assessment/20260928-regime-result.json)。
- 真实主线计算通过：614 个板块成分，遍历 321 个历史月，21,617 条排名覆盖 1,216 日（概念 19,081、行业 2,536）。状态 partial，基于已知涨停及连板样本和当前板块成分，并非历史成分回测。最终运行 111.01 秒、RSS 418,544 KiB；复用前次中断形成的 19 个月缓存和成分缓存。证据：[主线结果](../evidence/sqlite-migration-assessment/20260928-regime-mainline-result.json)。
- Fundwise 新增 5 项与受影响的有界读取/旧 CLI 定向验证通过；CLI 保持严格评分，页面允许明确标注的已知子集评分。

公共市场特征落四表库；模型分数、周期/主线结果仍由 Fundwise 保存。索引缺日不能把跨交易日收益当作当日收益。ETF、指数读取保留公共域接口，已抽查实际两日日线。不能据上述工程验收宣称历史输入完整、主线输入完整、生产已切换；当前 Jakarta 同步状态以本次异机哈希验收为准。

## 本轮收尾与续接

2026-09-28 后续：公共质量接口已交付，Fundwise 正在接收；新增字段尚未在真实库启用。下一阶段按 [公共质量交付后执行计划](../implements/post_public_quality_execution_plan.md) 推进真实日更、输入修复、成品同步与消费验收，不能将下述旧副本验收视为新增质量字段已同步。

已推送 tdxman `77f4f86`（含辅助校验器 `bb78832`）、Fundwise `3d68000`。Jakarta 主仓库已拉取代码，Python 3.11 SQLite v3 导入检查通过；辅助 worktree 标记 completed，未重复其 40 项测试。新版 data 已安装，Jakarta Fundwise 18 日评分、578 条主线排名及三个 HTTP 端点验收通过。历史输入缺口和默认生产配置切换仍未关闭；增量同步后置。见[实际结果](fw04_jakarta_full_copy_acceptance.md)。
