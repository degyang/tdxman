# BaoStock 补充数据源

BaoStock 已接入 `tdxman` 单股查询与 `aspool` 日常更新，用于补齐沪深 A 股历史日线、
当日 ST、参考前收价、停牌状态和上市资料。本接口不覆盖北交所、ETF、指数、分钟线和复权行情。

## CLI 查询

```bash
# 最近 30 条不复权日线，包含源端明确报告的停牌会话
tdxman quote 600519.SH --count 30 --source baostock --format table

# 区间内最多 30 条；需要更多记录时增大 count
tdxman quote 600519.SH --source baostock --count 30 \
  --start-date 2026-09-01 --end-date 2026-09-18

tdxman kline 600519.SH --source baostock --period DAILY --count 30
tdxman symbol-info 601091.SH --source baostock
```

`quote --source tdx` 返回实时报价；`quote --source baostock` 返回历史日线。
两者时间含义和字段不完全相同。BaoStock 输出 `date`、OHLC、`pre_close`、`volume`（股）、
`amount`（元）、`is_st`、`trading_status`、`source`、`fetched_at`。空字段保持为空；
ST 来源为该日 `isST`，参考价来源为该日 `preclose`。盘中单股查询可能返回未收盘数据。

多股使用 `"600519.SH,000001.SZ"`；`--output` 指定目录时每股一个文件。
`symbol-info` 的名称是查询时的基本资料，不能推断历史 ST。

## 日常更新与历史补齐

新版 SQLite 日线命令以 [update/sync 契约](sqlite_update_sync_cli.md) 为准：默认
`aspool update --root data` 只使用通达信报价；备用源通过
`aspool update --root data --source baostock` 显式选择。历史 K 线使用
`aspool sync --root data --source baostock --start 2026-09-23 --end 2026-09-24`。
不会按旧配置自动追加 BaoStock。两条路径均执行必要的逐股派生和日市场汇总。

以下配置和合并说明记录旧文件池的补齐流程，不代表新版 SQLite 会自动执行第二轮补源：

```bash
# 已有股票池：通达信报价后自动执行 BaoStock 补齐
aspool update

# 指定历史区间，包含连板追溯所需前史
aspool sync --source baostock --start 2026-09-01 --end 2026-09-18

aspool update --lookback 60
aspool update --no-baostock
```

`settings/config.yaml` 默认配置：

```yaml
aspool:
  baostock:
    enabled: true
    lookback: 30
```

补源导入入口为 `aspool update` 和 `aspool sync --source baostock`。
默认股票 `aspool sync` 先执行[日期股本和量价指标计算](../design/daily_enrichment.md)，
以 BaoStock 只读校对冲突样本，再统一重算涨跌停及连板。没有新增常驻调度器。
建议北京时间 16:00 后执行。当天 16:00 前补齐阶段只取至前一天，并用源端交易日历选择
最近 N 个交易日。这不保证源端在 16:00 已全部更新，未返回的数据仍记录为缺口。
`--start/--end` 包含边界；省略起点时使用 `lookback`。

BaoStock 按已有股票池范围补齐，不负责发现完整历史上市/退市证券全集。
补齐使用一个串行连接，`update --workers/--async` 只控制通达信阶段。
完整且资料未过期的证券跳过；基本资料每七天刷新。首次全市场补齐和派生重算可能耗时较长。
`--limit N` 只处理前 N 个待补证券，报告 `planned/not_selected` 显示剩余数量。

## 合并与质量规则

1. 有效主源 OHLC 保留，与补源不一致时记录冲突；缺失或非法行情可由有效补源行情修复。
2. `pre_close/is_st` 只填缺失或非法值，记录来源和对应日期；有效值冲突不静默覆盖。
   除权日不使用上一条收盘价替代参考前收价。
3. 停牌须有 `tradestatus=0`。缺行不等于停牌；没有原行情行的停牌日期只写状态事实，
   不人为补造可交易的平价 OHLCV。已有停牌行情行保留并附状态。
4. `ipoDate/outDate` 排除上市前和旧代码退出后的日期。`outDate` 可能表示代码退出，
   不能一概解释为发行人退市。北交所退出日期不由此来源判断。
5. 上市日期与覆盖区间全部自然日的交易日历共同确定上市窗口；证据不足继续保留未知。
6. 补齐后顺序重算涨跌停及后续已发布连板，可靠停牌暂停计数。缺失会话不跳过；
   未追溯到可靠非涨停边界时，连板数仍为空，可向前扩大 `--start`。

规则版本更新为 `cn-a-share-limit-v5`，包括主板风险警示股票自 2026-07-06 起限幅 10%、
302 开头创业板证券、价格按分比较，以及上述停牌/上市窗口处理。此前主板 ST 使用 5%。

同时支持 2014-06-13 至 2023-04-09 的主板普通交易日：有两个以上可观察会话，
或可靠上市交易日数超过一天，即可排除上市首日并使用当时的 10% / ST 5% 规则。
首日发行价特殊限幅仍需独立依据，缺失时保留 UNKNOWN。

v5 增加混合复权参考价的冲突识别、保守精度下的唯一分价恢复与逐条依据。
定向补齐及五年重算说明见 [限价历史修复](../achievements/limit_history_rebuild.md)。

近五年已有行情的派生重算入口为：

```bash
python scripts/rebuild_limit_history.py --root ~/.aspool --start 2021-09-24 --end 2026-09-24
```

此入口备份目录库、顺序重算涨跌停和连板、核对发布一致性，并导出公开接口结果到
`~/.aspool/reports/limit-history/<UTC时间>/`。默认向前多算 120 个自然日作为连板前史，
不联网抓取行情；未知状态按现有证据保留。
依据见[上交所修订说明](https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20260424_10816474.shtml)与
[深交所施行通知](https://www.szse.cn/lawrules/service/member/t20260630_621404.html)。
旧版本派生批次不能作为新版本连板前史，历史结果须重算后更新。

## 报告和读取

报告位于 `ROOT/reports/maintenance/baostock-<run_id>.json`；最近报告索引为
`ROOT/reports/maintenance/baostock-latest.json`。包括范围、缺日、冲突、拒绝行、失败、
不支持的证券及派生结果。连续三个证券失败时结束本轮，记录尚未尝试的数量。
部分失败使命令非零退出，成功写入的数据保留；通达信阶段的失败也不会因启动补齐而消失。

`limit_events.consecutive_coverage` 分日记录涨停股数、连板已知数和未知数，
与 `limit_events.price_limit_coverage` 的限价状态覆盖分别记录。
`status=ok` 只代表本轮所选沪深补齐任务完成，
不代表北交所、全历史连板或整个 regime 页面已完整。

```python
from aspool import DataPool

pool = DataPool("~/.aspool")
facts = pool.read_security_daily(
    symbols=["600519.SH"], start="2026-09-01", end="2026-09-18"
)
info = pool.read_security_info(symbols=["601091.SH", "688837.SH"])
calendar = pool.read_trading_calendar(start="2026-09-01", end="2026-09-18")
```

API 只读本地数据，不隐式联网。事实表 `symbol` 为 `代码.市场`、日期为 `trade_date`，
基本资料名称是当前名称。缺行是未知。返回值 `point_in_time=False`，不承诺当时可得性或不可变版本。
完整按市场日历、历史证券全集和统一回测交易状态接口仍待实现。

## 对 regime 缺口的覆盖范围

| 原缺口 | 本次补齐路径 | 仍需确认 |
|---|---|---|
| 历史 ST | 沪深逐日 `isST` 与字段来源 | 北交所及源端未返回日期 |
| 参考前收价 | 沪深逐日 `preclose` | 北交所、源间冲突 |
| 行情/交易状态 | 修复行情、明确停牌事实、代码退出日期 | 不支持证券和未解释缺行 |
| 上市窗口 | 上市日期与沪深交易日历 | 北交所及新股资料延迟 |
| 连板 | 修复后顺序重算，单列连板覆盖 | 回溯边界以外的未知前史 |

源端探查已确认可返回相关历史字段。新接入代码尚未执行端到端测试或生产全量回填。
regime 消费端仍需读取这些字段和派生结果，不能仅凭数据源接入认定页面指标全部可用。
