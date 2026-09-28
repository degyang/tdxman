# FW-03 日线局部更新与历史派生

2026-09-28。本次只推进迁移、日线更新、日线缺补后的派生计算。

## 实现与边界

- 原始迁移及 Jakarta 初始副本验收沿用 M0 证据，不重复全量核对。
- `sqlite_daily_update.apply_daily_changes` 在一个 `BEGIN IMMEDIATE` 中比较源字段、写入实际差异、更新参考价、计算逐股派生、更新双口径日汇总。失败整笔回滚。
- 缺行和已确认停牌均不贡献交易统计、不造平价 K 线，并冻结连板状态；无效 OHLC 与真实交易但限价未知会中断连板已知性。
- 历史补入/修订会处理下一有效交易参考价、20 根有效交易 MA20 窗口、连板实际后缀。测试含超过 MA20 窗口的 45 日连板；超限不会留下部分修复。
- 仅成交额等汇总字段变化时，不扫描重算该股历史派生。重复数据不写时间戳、不调用派生或汇总。
- `sqlite_daily_sync.sync_baostock_daily` 使用现有日期区间接口，在事务外抓取，默认最多收集 500 只股票的有限日期输入后统一事务提交；每个影响日期在该事务中只汇总一次。失败和空响应保留旧数据并返回清单。SH/SZ 支持；BJ 明确报告 unsupported。
- `scripts/ops/derive_stocks_sqlite.py` 是首次迁移后的全历史维护脚本；逐股有界读取、提交，进度在外部 JSONL 文件中。全部逐股计算成功后才生成全部日汇总。中断使用 `--resume`，失败股票会重试。
- 股票库仍只有四张业务表。没有引入批次、publication、coverage、任务队列表。

Tick 对照：`Projects/tick-stock-panel/backend/app/services/kline_sync.py` 的显式日期范围、失败保留旧值；`backend/app/tickflow/repository.py` 的 `(symbol,date)` keep-last 合并。本项目使用 SQLite 局部 UPSERT，不复制其整文件替换、全市场因子快照重写或股票全历史 enriched 重算。

## 调用

```python
def daily_window(market_sessions, *, as_of, start=None, end=None): ...

def apply_daily_changes(
    conn, *, bars=(), dated_facts=(), factor_extensions=(), market_sessions, listed_days=None,
) -> dict: ...

def sync_baostock_daily(
    root, *, client, symbols, market_sessions, as_of,
    start=None, end=None, listed_days=None, max_symbols_per_write=500, action_fetcher=None,
) -> dict: ...
```

日期为 ISO 字符串，股票为 `000001.SZ`，价格不复权、单位元，成交量为股，成交额为元。来源接口保持此契约。源字段省略表示保留，明确 NULL 表示撤销。同来源可以修订；不同可靠来源冲突则拒绝。

```python
from pathlib import Path
from aspool.sqlite_stock_store import stock_connection
from aspool.sqlite_daily_update import apply_daily_changes

with stock_connection(Path("data/_staging/fw02"), read_only=False) as conn:
    result = apply_daily_changes(
        conn,
        bars=[{"symbol": "000001.SZ", "trade_date": "2026-09-24", "amount": 12345678.0}],
        market_sessions=confirmed_open_dates,  # Includes possible successor dates.
    )
```

上述写入接口要求连接没有未提交事务。普通调用固定最多 10 个输入交易日、100,000 条源变更、250,000 行逐股读取预算、60 个影响日期、30 秒写入截止（含 SQLite progress handler）。`read_rows` 是逐股派生和汇总的显式行计数，不包含所有索引探测，不能当作完整磁盘扫描量。

默认最近 5 个已确认交易日；历史补数明确指定 start/end，不自动倒推全历史：

```sh
.venv/bin/python scripts/ops/update_stocks_sqlite.py \
  --root data/_staging/fw02 --baostock --symbol 000001.SZ --as-of 2026-09-24

.venv/bin/python scripts/ops/update_stocks_sqlite.py \
  --root data/_staging/fw02 --baostock --symbol 000001.SZ \
  --as-of 2026-09-24 --start 2026-09-23 --end 2026-09-24

# An already fetched canonical payload; no network work inside the transaction.
.venv/bin/python scripts/ops/update_stocks_sqlite.py \
  --root data/_staging/fw02 --input changes.json --as-of 2026-09-24
```

Payload 顶层为 `bars`、`dated_facts`、可选 `factor_extensions`。后者包含 `source_pre_close/source_pre_close_source`、`source_is_st/source_is_st_source`、`trading_status/trading_status_source`。业务 writer 不接受任意 SQL、隐式删除、因子全量替换或扩大守卫。`factor_extensions` 只接收已核实的尾部事件区间（symbol、verified_start、verified_end、events、source），调用 `advance_factor_coverage`，与日线/派生/汇总原子提交；既有历史事件修订仍走显式维护。

首次历史派生在开发库停写期间执行，不能与普通更新同时运行；这是维护入口，不是每日日线业务动作：

```sh
.venv/bin/python scripts/ops/derive_stocks_sqlite.py \
  --root data/_staging/fw02 --report data/_reports/fw03-main/full-derived.jsonl
# After interruption, same root/report and unchanged source snapshot:
# append --resume
```

全库执行由 tmux 会话 `tdxman-fw03-backfill` 托管；日志、退出码和 `/usr/bin/time -v` 位于 `data/_reports/fw03-main/full-derived.*`。没有退出成功与最终验收前，不切换生产、不将进行中的数据库复制为可用成品。

## 验证证据

- 本机逐股 9 项、日汇总 11 项、局部更新/来源接入 20 项通过；远端参考因子 17 项通过，复用其证据，没有重复运行。
- 3 只真实股票 13,918 行完成历史派生；最近日参考价/ST/限价/MA20 可用。早期缺日期 ST 的记录保持 UNKNOWN。
- 真实在线获取 `000001.SZ` 的 2026-09-23—24：2 行源变更、2 行派生、4 行汇总，事务 15 ms。再次在线获取：源、派生、汇总均 0 写入，4 ms。
- 隔离样本删除 `300822.SZ` 的 2026-09-23 日线后回补：2 个影响日期、2 行派生、4 行汇总，6 ms；该股所有历史派生字段恢复到原值，比较排除更新时间。
- 数据见 [daily-update evidence](evidence/sqlite-migration-assessment/20260928-daily-update.json)。这些小样本耗时不是全市场性能承诺。

## 未完成验收与明确限制

全库历史派生与最终校验已完成：5,586 股、16,350,085 条原始日线、16,350,599 条逐股特征；6,479 日 × 2 口径，共 12,958 条汇总。数据库 9,218,285,568 字节（约 8.59 GiB）。完整 integrity、源记录数、独立 SQL 汇总对账、日期覆盖、分布计数、因子区间检查通过，校验 762.197 秒，峰值 RSS 295,792 KiB。见 [全库验收证据](evidence/sqlite-migration-assessment/20260928-derived-result.json)。Jakarta 现有原始迁移副本已验收；新派生成品尚待 SSH 密钥解锁后同步。

日期 ST 缺失不以当前名称倒填；没有因子覆盖不默认因子 1，因此相关历史限价或 MA20 可为未知。新交易日超出已有因子覆盖时，在线入口先取得 TDX 事件和身份确认，再推进已有选定因子尺度；无事件只延长尾段，有事件按日期新增稀疏段，绝不全历史累乘。没有可靠初始锚点时返回 `factor_unavailable`，保留未知；需要推进已有覆盖却无法核实事件时，该股不写入。日入口目前生成 D 汇总；若已经存在相交 W/M 汇总，整笔拒绝，避免留下过期周期数据。现有 DataPool/Fundwise 默认读取入口尚未切换 SQLite；本次不宣称 FW-04 或全部 Fundwise 联调完成。

补充集成回归：参考价模块重算时保持原记录时间戳下界，时钟回退不降低消费者缓存版本；若改动价格是已有事件推导因子的参考价，普通日入口整笔拒绝并交给显式因子维护，避免提交过期因子。

Jakarta 原始来源只读审计见 [source-readiness](evidence/sqlite-migration-assessment/20260928-source-readiness.json)：可靠日期昨收 412,066 行（2.52%），日期 ST 413,515 行（2.53%）；2000—2020 年均无可靠 dated 来源；5,424/5,586 只股票有来源因子锚点。审计复用已验收的 M0 副本，不重复 hash/integrity 或已有回归。这些原始来源比例不等于最终派生 UNKNOWN 比例。

补充实测前验证：多股源更新按有界分组合并，两个股票同日只写两条 scope 汇总；事务中推进至时间预算后撤销已写入源行。成功结果按 `success[].symbols` 返回参与该次事务的股票及合计写入成本；没有业务 batch_id。

全市场近期副本实测（与离线维护并发）：500 股 × 5 日、2,500 条价格修订，逐股派生 2,500 行、日汇总 10 行，显式计数读取 39,835 行，事务 10.122 秒；重复相同输入零写入、71 ms。含副本构建的进程峰值 RSS 418,304 KiB（约 409 MiB），正常 writer 仍为 32 MiB SQLite 缓存。使用 `scripts/ops/benchmark_sqlite_daily_update.py`，其目标含人工价格修订，禁止晋升为行情数据。该实测不是五年公共读 API 的 RSS 验收。

离线全历史汇总按日期跨股票读取，维护连接缓存上限调为 256 MiB，从已提交日期续跑，不重复已完成的股票/日期；普通业务缓存不变。另验证过旧的“停牌但有成交”无效记录可由同来源更正交易状态后恢复计算。

补充缺补边界：跨长期停牌传播按实际发生变化的日期计入预算，仍受读取行数与事务截止约束；缺失的上游可选字段不撤销已有可靠记录及来源。完整成品校验入口为 `scripts/ops/verify_stock_derivation.py`；字节完全相同的副本可用 `--reuse-source-integrity` 复用已通过的全库校验，不重复昂贵检查。

新交易日集成验证：空事件区间延长、除权后参考价与 MA20、新因子失败撤销日线、事件源失败不写数据、TDX 每股单位不会再次除 10 均通过。辅助提交 `61d7c40` 的 6 项新增测试在 Jakarta 执行并复用；主端只执行集成测试，没有重跑辅助回归。普通日线入口也补充了最多 31 个自然日的日历刷新，只写实际差异；日历冲突必须显式维护。日历元数据在独立 catalog 事务提交，不会因此生成任何行情或衍生结果。

历史回填恢复阶段 `/usr/bin/time`：8:32.95，峰值 RSS 436,144 KiB（此前逐股与部分日期已提交，不能把恢复时间当作全部回填时间）。首次迁移验收、回填验收与近期更新性能分别记录，不混称五年公共读 API 验收。

新日期在线探测：交易日历确认 2026-09-25 休市、09-28 开市，样本目录新增 3 个自然日日历记录；BaoStock 对 09-28 返回空行情，源/因子/派生均未写入。该次探测验证空响应保留旧数据，不计作真实新交易日全链路计算验收；新日/除权计算已由独立集成样例验证。
