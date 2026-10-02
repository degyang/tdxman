# 日终稀疏涨跌停事件 v4 — v3 复审补正交付

> 术语说明：本文保留当时的实施/审计事实；当前统一采用[基础数据与 Enriched 数据两层定义](../design/data_layers.md)，下载的复权依据归基础数据，计算出的复权因子及其他衍生结果归 Enriched 数据。


**日期**：2026-09-18
**依赖树**：基线 `cb32022`；已纳入本地 checkpoint `a78fb49`
**状态**：v3 复审三项补正已完成，受限范围工程验收通过；已完成本地 Git 提交
**前版**：[`daily_limit_events_delivery_v3.md`](daily_limit_events_delivery_v3.md)

本轮严格按 Fundwise 审核文档的“v3复审”执行，仅处理早期失败陈旧覆盖、主板上市窗口日期依据、观测会话不完整三项阻塞。未改生产池、未联网补数据、未做全历史重建、未接实时、未改 Fundwise 业务代码。

## 1. 早期失败覆盖依赖后缀

**代码位置**：`src/aspool/limit_events.py`

`_published_dates_from()` 在读取范围和源数据之前查询已有 `daily_limit_publication`。`compute_limit_events()` 先将请求日期与“最早请求日之后已有发布日期”合并标记为 stale，再加载 scope、源数据和会话轴。因此：

- scope 加载失败、源读取失败、会话轴缩短或源日期被删除时，已有后续发布结果不会继续显示为新鲜；
- 不依赖当前源数据能否构造完整会话轴；
- 只有 `_publish_batch()` 成功发布某日后，才清除该日 stale；未成功处理的尾部保留 stale。

**定向反例**：先发布 `THU/FRI/MON`，只请求 `FRI`，分别注入 `load_scope` 和 `_read_symbol_bars` 早期失败。两种失败下，`FRI` 与已发布依赖日 `MON` 在 `read_limit_events`、`read_limit_summary`、`read_limit_coverage` 中均为 `stale=True`；重试 `FRI` 后 stale 全部清除。

## 2. 主板上市前五日窗口改为有依据的日期区间

**代码位置**：`src/tdxman/codec/price_rules.py`、`src/aspool/limit_api.py`

删除原先没有依据的 `2014-01-01` 起点，改为 `2023-04-10`：这是上交所《上海证券交易所交易规则（2023年修订）》关于全面注册制主板规则的首批主板注册制企业上市日。规则发布页明确该规则自首只按《首次公开发行股票注册管理办法》发行的主板股票上市首日起施行；上交所公告明确首批企业于 2023-04-10 上市。

依据：

- [上交所《交易规则（2023年修订）》及施行说明](https://www.sse.com.cn/lawandrules/sselawsrules2025/repeal/rules/c/c_20250612_10824490.shtml)；
- [上交所关于首批主板注册制企业上市安排的答记者问](https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20230404_5719112.shtml)；
- [深交所《交易规则（2023年修订）》及主板前五日说明](https://www.szse.cn/www/investor/institute/rules/t20230621_601279.html)；
- [上交所注册制改革说明](https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20230201_5715605.shtml)。

当前行为：

| 情形 | 结果 |
|---|---|
| 2023-04-09 及更早主板上市窗口 | 窗口依据未确认，`UNKNOWN` |
| 2023-04-10 起、可靠上市第 1～5 个交易日 | `NO_LIMIT` |
| 可靠上市第 6 个交易日以后 | 主板普通股按 10% 规则 `KNOWN`（仍需当日 ST 依据） |

`describe_limits()["rule_basis"]` 已同步说明该区间及更早时期的 UNKNOWN 口径。旧时期没有为提高覆盖率而套用当前五日窗口。

## 3. 上市日龄只接受可靠精确依据

**代码位置**：`src/aspool/limit_events.py::_listed_days`

池内观测日期并不是完整交易日历：可能缺整个市场会话，也可能仅某一证券缺行。因此不再把会话轴行数当作精确上市日龄：

- 有可靠 `listing_date` 且当前日就是上市日时，精确确认上市第 1 日；
- 其他日期不返回伪精确 `listed_days`；
- 个股观测行数只作为排除窗口的下界：观测数大于窗口时可排除窗口，观测数不超过窗口时返回 `UNKNOWN`，不误报 `NO_LIMIT`。

新增反例：

1. 整个池缺一个市场会话，观测到的第 5 根日线实际可能是上市后第 6 个会话；结果为 `UNKNOWN`；
2. 市场轴完整但个股缺一个会话行；个股观测数仍只是下界，结果为 `UNKNOWN`。

## 4. 验证

定向测试：

```bash
python -m pytest tests/unit/test_limit_events.py tests/unit/test_limit_sync_hook.py -q
# 69 passed
```

静态检查：

```bash
ruff check src/aspool/limit_events.py src/aspool/limit_api.py \
  src/tdxman/codec/price_rules.py tests/unit/test_limit_events.py
# All checks passed!
```

关联规则/派生回归：`test_daily_derived.py` + `test_protocol_fixes.py` 为 **52 passed / 12 subtests passed**。

本轮未重新泛跑全量单元测试；既存全量结果仍以 v3 记录为准，不将其重复描述为本轮验证结果。

## 5. 当前边界

- 主板 2023-04-10 前的上市初期窗口规则未在本包确认，相关记录为 `UNKNOWN`；
- ST 历史状态、`pre_close`、停牌连板口径等 v3 已登记的数据缺口继续保留；
- 无可靠交易日历时，不声称观测行数等于精确上市日龄；
- 生产池全量回填、长历史重算、自动调度、实时快照与策略评分不在本包；
- 工程补正完成不等于策略规则获得最终 PM 验收。

**实际工作区**：`/mnt/d/Workstation/Projects/tdxman`
**Fundwise 登记**：`/mnt/d/Workstation/Projects/Fundwise/docs/northstar/reviews/UP-daily-limit-events-done.md`

本交付文档及对应事件实现、测试随 `a78fb49` 提交；未执行 push。
