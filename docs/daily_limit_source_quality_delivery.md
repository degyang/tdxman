# 日终涨跌停来源质量实施交付

**日期**：2026-09-18
**审核依据**：`Fundwise/docs/northstar/reviews/UP-daily-limit-events-pm-review.md` 的“来源准备度复审：来源定位采纳，合并安全结论需修正并进入实施”。
**状态**：本地实施包完成，定向验证通过；未接新来源、未联网、未写生产池、未改 Fundwise 业务代码、未提交 Git。

## 1. 本轮实施结果

本轮执行审核文档中的五项实施指令：

1. 修复日线合并入口对 `None`、`NaN`、`pandas.NA` 的缺失判定；源缺值不覆盖已有可靠值，`False` 仍是有效 `is_st`。
2. 对 `pre_close`/`is_st` 的非空非法值单独计为 `invalid`，不写入既有 parquet 的强类型字段；已有可靠值保持不变，新日期仍保持未知。
3. 在现有 `reports/maintenance/latest.json` 中增加按日期、范围和本次处理行统计的字段质量计数，不创建统计数据库，不让 Web 刷新扫描全池。
4. 在同一同步报告的 `limit_events.rule_quality` 中复用已发布派生结果，按板块汇总 `KNOWN`、`UNKNOWN`、`NO_LIMIT`、`INVALID` 及 `UNKNOWN` 原因；不复制规则引擎。
5. 用临时池 fixture 验证报价 DataFrame → 转换 → `merge_daily` 落库，以及主板缺 ST、注册制创业板缺 ST 的实际可计算差异。

## 2. 实际代码变更

| 文件与位置 | 变更 | 作用 |
|---|---|---|
| `src/aspool/daily_storage.py:14-88` | 增加标量缺失判定、字段状态分类和字段质量报告聚合 | 统一识别 `None/NaN/pandas.NA`；分开统计最终 `valid/missing/invalid` 与 `incoming_invalid` |
| `src/aspool/daily_storage.py:109-215` | `merge_daily` 在写入前清理非法可选字段，记录最终行状态及入站异常 | 缺值不覆盖；非法值不污染强类型 parquet；可靠旧值可保留且不丢入站异常 |
| `src/aspool/tdx_online.py:106-131` | `_merge_rows` 与量比缺失判断改用统一判定 | 在线日线及共享分钟合并路径不再把 NaN 当可靠值 |
| `src/aspool/tdx_online.py:310-359` | 日线同步报告写入 `field_quality` 和 `limit_events.rule_quality` | 报告按日期和实际更新子集提供覆盖质量与规则状态 |
| `src/aspool/fundamentals.py:182-218, 310-369` | 报价缺值转换安全化，并复用字段质量报告 | DataFrame 标量缺失可安全进入合并入口；报价同步也产出质量计数 |
| `src/aspool/limit_events.py:763-848` | 增加已发布结果的按板块/原因汇总 | 直接读取生产规则已生成的派生状态，不重写规则 |

### 合并和类型口径

- `None`、`NaN`、`pandas.NA`：按缺失处理，不覆盖同日已有值。
- `is_st=False`：按有效布尔值保留，不能用真假判断过滤。
- `pre_close`：仅有限、数值型且大于 0 才是有效值；非空字符串、非正数、无穷值等计 `invalid`。
- `is_st`：仅真正的 Python `bool` 是有效值；其他非空类型计 `invalid`。
- 非法可选值从待写行中移除，避免写入已有 parquet 强类型列；最终质量状态严格来自合并后实际保留的行，入站非法值另计在 `incoming_invalid`。已有可靠值不被非法值替换。
- 有效新值仍可覆盖旧值；同样输入重复合并返回 `changed=0`，不重复改写文件。

## 3. 现有同步报告格式

日线同步会在 `reports/maintenance/<run_id>.json` 及 `latest.json` 写入：

```json
{
  "field_quality": {
    "scope": "stock",
    "source": "tdxman:stock",
    "basis": "post_merge_incoming_rows",
    "start_date": "2026-09-11",
    "end_date": "2026-09-11",
    "rows": [
      {
        "trade_date": "2026-09-11",
        "processed_rows": 1,
        "symbol_count": 1,
        "pre_close": {"valid": 1, "missing": 0, "invalid": 0},
        "is_st": {"valid": 0, "missing": 1, "invalid": 0},
        "incoming_invalid": {"pre_close": 0, "is_st": 0},
        "joint_valid": 0
      }
    ]
  }
}
```

`basis=post_merge_incoming_rows` 表示最终字段计数来自本次进入 `merge_daily`、并完成合并处理的日期/标的行；每个日期独立统计，`symbol_count` 明确实际范围。因此该计数不是全市场覆盖声明，也不会把历史池总量冒充本次更新量。`incoming_invalid` 同样只统计本次处理子集中的入站异常，不覆盖最终可用状态。报价同步的 `source` 为 `tdxman:quote`。

当本次更新触发日终派生时，报告还包含：

```json
{
  "limit_events": {
    "dates": ["2026-09-11"],
    "status": "ok",
    "rule_quality": [
      {
        "trade_date": "2026-09-11",
        "scope": "stock",
        "boards": [
          {
            "board": "创业板",
            "processed_rows": 1,
            "KNOWN": 1,
            "UNKNOWN": 0,
            "NO_LIMIT": 0,
            "INVALID": 0,
            "unknown_reasons": {}
          }
        ]
      }
    ]
  }
}
```

板块状态来自已发布的事件、异常和 scope；`UNKNOWN` 原因直接沿用生产规则解析生成的异常文本。字段联合有效不等于规则可计算：例如注册制生效后的创业板规则不依赖 ST，可在 `is_st` 缺失时为 `KNOWN`；主板缺少 ST 依据仍为 `UNKNOWN`。

## 4. 隔离样例与结果

所有样例均使用 pytest 临时目录，最多覆盖必要的三次新日期，不访问 `/home/ubuntu/.aspool` 生产池。

### 4.1 字段保存样例

`tests/unit/test_aspool_performance.py:test_quote_missing_scalars_reach_merge_entry_without_erasing_reliable_fields`：

- 既有同日可靠值：`pre_close=10.0`、`is_st=False`。
- mock DataFrame 经 `_quote_bar` 转换后提供 `pre_close=pandas.NA`、`is_st=NaN`。
- 真实 `merge_daily` 入口落库后仍为 `10.0`、`False`；质量为 `pre_close=valid`、`is_st=valid`、`joint_valid=true`。

同文件的质量测试还覆盖：

- 新日期缺值：两字段都为 `missing`，不凭空补值；
- 非空非法值：两字段都为 `invalid`，不写入强类型 parquet；
- 有效更正：新 `pre_close`/`is_st` 覆盖旧值；
- 重复合并：相同更正再次执行返回 `0`。

### 4.2 规则可计算样例

`tests/unit/test_limit_events.py:test_missing_st_only_blocks_main_board_not_registered_gem` 使用 14 个 warm-up 会话加目标日，避免把上市窗口不确定性混入结果：

| 板块 | `pre_close` | `is_st` | 规则结果 | UNKNOWN 原因 |
|---|---|---|---|---|
| 主板 | 有效 | 缺失 | `UNKNOWN` | 主板涨跌幅取决于风险警示状态，无历史 ST 依据 |
| 注册制创业板 | 有效 | 缺失 | `KNOWN` | 无 |

该 fixture 证明联合字段有效为 0 时，不能直接推出全部规则不可计算；实际状态仍由现有 `resolve_limit_rule` 决定。

## 5. 复跑命令与结果

以下为上一轮来源质量实施包的验证记录，仅作历史基线，不作为本轮局部收口的新增结果。

定向实施包验证：

```text
python -m pytest tests/unit/test_aspool.py \
  tests/unit/test_aspool_performance.py \
  tests/unit/test_limit_sync_hook.py \
  tests/unit/test_limit_events.py -q
96 passed, 6 subtests passed in 19.77s
```

静态检查：

```text
ruff check src/aspool/daily_storage.py src/aspool/tdx_online.py \
  src/aspool/fundamentals.py src/aspool/limit_events.py \
  tests/unit/test_aspool.py tests/unit/test_aspool_performance.py \
  tests/unit/test_limit_sync_hook.py tests/unit/test_limit_events.py
All checks passed!
```

上一轮测试包含网络屏蔽 fixture；没有调用行情网络。未机械重跑全量测试，未写入生产池。

## 6. 来源质量实施复审局部收口

本轮只修正 post-merge 质量与入站非法计数的口径，其他已通过部分不返工：

- 同日已有 `pre_close=10.0`、`is_st=False`，收到 `pre_close=-1.0`、`is_st="False"`：最终行仍为 `valid/valid`，`joint_valid=true`，`incoming_invalid` 两字段各为 1；即使文件 `changed=0`，入站异常仍进入报告。
- 新日期收到同样非法值：最终行两字段为 `missing`，`joint_valid=false`，`incoming_invalid` 两字段各为 1；不把非法输入冒充最终可用值。
- 源缺失但旧值可靠：最终状态仍为 `valid/valid`，联合有效仍正确；重复输入不改文件但仍生成质量记录。

本轮定向验证：

```text
python -m pytest tests/unit/test_aspool_performance.py \
  tests/unit/test_limit_events.py -q \
  -k 'quote_missing_scalars or daily_merge_quality or missing_st_only'
3 passed, 81 deselected in 1.87s

python -m pytest tests/unit/test_limit_sync_hook.py -q \
  -k 'invalid_input_quality_is_separate_in_final_report'
1 passed, 5 deselected in 1.57s
```

本轮没有调用行情网络、没有写生产池；本轮验证不复制上一轮 `96 passed` 数量。

## 7. 已完成与未验证范围

已完成：

- 日线实际合并入口的缺失安全修复；
- 非空非法字段与缺失字段分开统计；
- post-merge 最终状态与 `incoming_invalid` 入站异常分离；
- 现有维护报告的逐日、逐范围质量计数；
- 现有派生报告的板块状态和 UNKNOWN 原因汇总；
- 临时池真实落库入口及规则分离 fixture。

未验证/不属于本包：

- 实时行情中 `STOCK_TAG_FLAGS` 的真实语义及 ST 标签映射；
- 真实行情会话下 `pre_close` 的除权除息口径；
- 历史 ST 生效区间、PIT 身份回填和全历史价格补齐；
- 生产池全市场性能、日终收盘完成性及全量质量报告；
- 停牌口径、交易日历完整性、旧时期整体规则覆盖。

本包仍不把当前名称当历史 ST，不用上一根 `close` 无条件替代 `pre_close`，不接新来源、不联网抓取、不回填历史、不新建统计数据库。

**交接结论**：来源质量实施包已完成，等待 PM 复审；外部 ST/除权取证及历史来源建设另行决策。
