# UP-RG-FEATURE 上游实现回执

2026-09-28。对应 Fundwise `docs/northstar/tasks/UP-regime-public-features.md`。
上游实现已完成，生产库尚未升级或补算；Fundwise 可开始接口适配，不能据此将生产质量字段标为已就绪。

## 交付边界

- `16ad826`：1.1.2 wheel 打包修复，包含 EX 分类资源，修复安装后 `describe()` 找不到配置文件的问题。
- `021442b`：公共质量字段、元数据、事务更新、缺行后缀修订、显式维护入口与回归验证。
- 保持股票四张业务表，仅在 `market_daily_summary` 增加两个可空 JSON 文本列，不新增发布或版本平台。
- SQLite `user_version=1` 保留；这是兼容的可选列扩展。消费者须探测字段能力，不能仅依据数据库或包版本。
- 未修改 Fundwise 工作区、生产配置或真实数据；没有启动历史回填或新一轮 Jakarta 数据复制。

## 公共字段与口径

两个字段均随日期、scope 持久化，使用现有日汇总投影读取。scope 为 `all_stocks` 或 `exclude_known_st`；后者仅排除当日已确认 ST，未知仍保留。

### `limit_reason_counts_json`

示例：

```json
{"UNKNOWN":{"missing_st":3097,"missing_reference":95},"INVALID":{}}
```

直接按逐股 `limit_reason` 的主要原因分组。一个证券在一个日期只进入一个原因，UNKNOWN 与 INVALID 互斥；这不是所有缺失事实的分别计数。具体主要原因沿用既有逐股计算的判定次序，不重新解释明细。

- UNKNOWN 原因数之和等于 `limit_unknown_count`。
- INVALID 原因数之和等于 `limit_invalid_count`。
- 没有原因文本的异常明细归 `unclassified`，不能丢失计数。
- NO_TRADE 不进入这两个组。`missing_st`、`missing_reference` 在已计算的 UNKNOWN 对象中始终有键；其他实际原因动态列出。
- `st_unknown_count` 是独立的 ST 事实缺失计数，不等于 `missing_st`：部分板块即使缺 ST 也可以确定涨跌停规则。
- 整列 NULL 表示尚未计算；已计算的空 INVALID 对象才表示没有 INVALID。

### `promotion_quality_json`

```json
{
  "candidate_count": 7,
  "excluded_no_trade_count": 2,
  "excluded_invalid_count": 1,
  "excluded_limit_unknown_count": 1,
  "excluded_predecessor_unknown_count": 1,
  "unresolved_predecessor_count": 1
}
```

这是示意值：若既有 `promotion_eligible_count=2`，已确认候选总数为 7。

候选身份由之前最近一条非 NO_TRADE 观察确定：其收盘涨停则为候选。候选跨停牌、缺行延续，直到新的非 NO_TRADE 观察改变身份。已存储的已知前序连板高度可直接判定候选，避免重复读取前驱。

已确认候选按以下优先级分配，互斥且可对账：

1. 当日缺行或 NO_TRADE → `excluded_no_trade_count`，两者统计处理相同。
2. 当日 INVALID → `excluded_invalid_count`。
3. 当日限价不能判定 → `excluded_limit_unknown_count`。
4. 前序连板高度未知 → `excluded_predecessor_unknown_count`。
5. 其余进入既有 `promotion_eligible_count`。

`candidate_count = promotion_eligible_count + 四项 excluded_*_count 之和`。

当前有交易但前序身份仍无法确认的股票列入 `unresolved_predecessor_count`，它在已确认候选池外，不混入上述等式或晋级分母。没有当前行时不前填 ST；因此该候选在 `exclude_known_st` 中也保留。这是现有数据的统计口径，不声称提供完整的历史上市/退市证券池。

既有晋级成功数、可判定分母和比例保持不变：`promotion_ratio = promotion_success_count / promotion_eligible_count`；无可判定样本时比例为 NULL。

## 接口签名与调用

```python
DataPool.describe_market_fields(*, fields=None, frequency="D", scope="all_stocks")
DataPool.read_market_daily(*, start, end, scope="all_stocks", fields=None)
DataPool.read_market_summary(
    *, start, end, frequency="D", scope="all_stocks", fields=None, closed_only=True
)
```

```python
import json
from aspool import DataPool

pool = DataPool("/path/to/tdxman/data")
metadata = pool.describe_market_fields()
required = {"limit_reason_counts_json", "promotion_quality_json"}
if not required <= metadata.keys():
    raise RuntimeError("Database quality fields are not installed")

daily = pool.read_market_daily(
    start="2026-07-03", end="2026-07-06", scope="all_stocks",
    fields=["period_key", "limit_unknown_count", "limit_reason_counts_json",
            "promotion_eligible_count", "promotion_success_count",
            "promotion_ratio", "promotion_quality_json", "updated_at"],
)
for row in daily.to_dict("records"):
    reasons = row["limit_reason_counts_json"]
    # NULL means not computed; do not convert it into an empty object.
    if reasons is not None:
        reasons = json.loads(reasons)
```

元数据返回每个字段的类型、单位、nullable、scope、frequency、readable 和 NULL 含义。`describe_limits()` 的 v3 描述也包含 `market_fields`。未知或数据库尚未安装的字段报 `FIELD_UNSUPPORTED`；W/M 报 `FREQUENCY_NOT_READY`，不会暴露占位周/月字段冒充已实现能力。

单位保持既有口径：金额为 CNY；`up_pct/strong_up_pct/strong_down_pct/avg_turnover` 为百分数数值；`avg_return/median_return/sealed_ratio/promotion_ratio/above_ma20_pct` 为比值；时间戳为 Unix 微秒。`above_ma20_pct` 虽以 pct 命名，实际仍是比值，消费者须按元数据解释。

元数据只检查结构，日汇总只读取指定窗口；读取不触发维护、网络或全历史重算。旧库仍能读取旧字段。新列装好但未补算的行返回 NULL，区别于未安装字段。

## 日更与维护

新增交易日随原有事务更新源事实、逐股派生和两种 scope 的汇总。质量原因组成改变，即使总数相同，也更新汇总时间戳。完全无变化则不重写、不改变时间戳；故障回滚不留下半成品。

历史修订若改变前序候选，还会刷新缺行日期的候选排除数，直到下次非 NO_TRADE 观察。沿用最多 60 个受影响交易日、调用方提供的日历和事务时间预算；超过边界明确失败，不自动扩大为全历史任务。

汇总成本记录 `read_rows`：当日明细行、枚举证券键和实际访问的前驱观察行。证券键使用索引跳转，前驱按证券索引读取，受硬预算约束；该值是逻辑读取计数，不是磁盘页扫描量。当前实现逐日枚举证券键，未来规模扩大仍需观察此成本，不能仅以有日期条件证明性能。

旧库使用显式 ops 入口升级并补算指定窗口；以下是维护命令模板，本次未对生产执行：

```sh
.venv/bin/python scripts/ops/backfill_summary_quality.py \
  --root /path/to/tdxman/data --upgrade-schema \
  --start 2026-07-03 --end 2026-07-06 \
  --report /path/to/tdxman/.local/reports/summary-quality.json
```

执行前按项目规则建立并验证一致恢复点。脚本要求已有数据根、明确日期范围、报告位于 data 外；只处理窗口内已有日汇总日期。列升级单独提交，随后每天两种 scope 一起提交；中途失败时先前完成日期保留，未完成行保持 NULL，可重跑。没有自动全历史默认值，不修补源事实。正式历史初始化需自行分窗、评估时间与恢复空间。

## 验收结果

复用 Jakarta 打包工作者已完成的验证，没有重复跑其 8 项独立安装测试。

| 验证 | 结果及边界 |
| --- | --- |
| 受影响日汇总、日更、读接口回归 | 39 个用例通过；首次 2 个读取量断言因新增计数失败，修正后在定向重跑中通过 |
| 新质量契约测试 | 6 个通过；原因对账、分母排除、ST/参考价修复、缺行后缀、无样本、无变化、预算和事务故障回滚 |
| 干净环境基线 wheel | Jakarta 工作者 8 个通过，1.865 秒；资源、describe、日汇总、并发快照、分页和 CLI |
| 最终集成 wheel | 新增独立安装测试 1 个通过，0.355 秒；脱离源码从 site-packages 导入，计算及读取新字段和元数据 |
| 显式维护 CLI | 旧结构夹具首次升级/补算改动 2 行，重跑 0 行；0.046 / 0.028 秒 |
| 真实两日隔离样本 | 两种 scope 原因计数均与独立 SQL 明细聚合相符；总耗时 9.574 秒，峰值 RSS 200,720 KiB（约 196 MiB） |
| 静态检查 | 修改模块 Ruff 与 `git diff --check` 通过 |

真实样本仅复制到 `.local/up-rg-feature/sample`；源库只读。抽样源读取量为 33,196 行/键，两次汇总分别记录 14,267 / 14,271 次逻辑读取。真实样本的独立 SQL 对账覆盖原因汇总；候选排除通过合成逐股用例核对，并非对全历史候选做了独立重算。没有重复执行五年全量读取验收，不将此次小样本 RSS 当作五年新验收。

| 日期 | UNKNOWN 对账 | 晋级成功 / 可判定 | 已确认候选 | 候选身份未定 |
| --- | --- | --- | --- | --- |
| 2026-07-03 | 3,192 = missing_st 3,097 + missing_reference 95 | 1 / 9 | 9 | 3,190 |
| 2026-07-06 | 106 = missing_st 0 + missing_reference 106 | 0 / 20 | 20 | 3,193 |

两日 INVALID 均为 0；原因数在两种 scope 下相同。7 月 6 日的 `missing_st=0` 不代表 ST 事实全齐，应同时读取独立的 `st_unknown_count`。

## 尚未补齐的历史依据

7 月 3 日缺 ST 的 3,097 条虽有候选值（2,950 个非 ST、147 个 ST），但来源均为 `raw_fallback:legacy_unspecified`。两日抽样没有名称日期与交易日一致的有效输入证据；缺参考价的 95 / 106 条也没有可直接采用的正值源昨收。本包保留未知，不以当前名称或未经核实的旧值倒填。

当前公开日汇总中存在缺口的日期范围如下。起止是观测边界，不表示区间内每一天都存在该问题。

| 指标 > 0 | 首日 | 末日 | 日期数 |
| --- | --- | --- | --- |
| st_unknown_count | 2000-01-04 | 2026-09-23 | 6,476 |
| limit_unknown_count | 2000-01-04 | 2026-09-24 | 6,479 |
| limit_invalid_count | 2026-06-24 | 2026-09-24 | 2 |
| streak_unknown_count | 2020-08-24 | 2026-07-14 | 300 |

下一项数据修复应追溯旧导入事实的来源与生效日期，再通过既有局部更新接口触发后缀计算。质量列初始化本身不会消除这些基础事实缺口。

## 安装工件与 Fundwise 接入顺序

最终 wheel：`.local/up-rg-feature/dist/tdxman-1.1.2-py3-none-any.whl`，390,193 字节。
SHA-256：`b34d1709a852eafb6be21af8cde320b081602130772cabf0e67df317686afe1c`。
包内实现对应 `021442b`；[打包基线回执](up_rg_feature_wheel.md)中的较早 wheel 不包含这次新增字段，不应作为最终交付安装。

```sh
uv pip install --python /path/to/consumer/.venv/bin/python \
  /path/to/tdxman/.local/up-rg-feature/dist/tdxman-1.1.2-py3-none-any.whl
```

1. Fundwise 在独立联调环境安装最终 wheel，探测 API 和字段能力，按 NULL 语义接入质量解释。
2. 确定维护窗口后，对真实库显式升级并初始化所需日期；旧字段消费可继续运行。
3. 维护完成后再安排 Jakarta 的一致副本更新与校验。先前完成的全量复制仍是旧字段结构，不能声称它已包含本包质量列。
4. Fundwise 使用新汇总完成消费验收后，再切生产配置。权重、阶段标签和用户模型继续由 Fundwise 负责。

本次没有发布 PyPI、替换消费者环境或发送外部消息。可将本回执交给 Fundwise 维护者，无需其重复扫描明细构造质量原因或晋级分母。

机器可读证据见 [20260928-public-quality](evidence/20260928-public-quality/)。
