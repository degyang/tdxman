# 六维日线公共输入维护

四维与六维复用原市场日汇总表和 `DataPool.read_market_summary`。新增 `upper_median_return`、`avg_vol_ratio_5d`、`vol_ratio_5d_valid_count`、`high_vol_ratio_5d_count`，普通中位数和原四维字段保持原口径。内部存储布局与修订号不构成消费模型版本或全历史重算依据。

Fundwise 最终四维/六维结果必须共用现有日级结果表，追加六维字段，分别保存各自公式的结果；不得另建六维结果表或独立六维缓存。这里的板块表属于不同粒度的底层公共事实，不是六维结果表。

## 显式初始化

部署配套 SDK 后，先在明确的数据根做原有备份，再显式升级可选结构。只升级结构不回算数据：

```python
from aspool.sqlite_six_dimension import upgrade_six_dimension_schema
upgrade_six_dimension_schema(DATA_ROOT)
```

现有 aspool 日更 source session 内采集 HY2/GN 完整目录与成分，不新增调度。首次采集也可在既有 source session 调用 `refresh_board_snapshots(root, session)`。采集失败保留最后完整快照及错误状态。

```sh
PYTHONPATH=src python scripts/ops/backfill_six_dimension_inputs.py \
  --root "$DATA_ROOT" --start 2026-09-01 --end 2026-09-30 \
  --report /tmp/six-initialize.json
```

每次限定 1–60 个市场交易日，从现有日历保存前五个交易日前驱；行情缺口不会触发自动补数或扩大窗口。首次可以显式加 `--upgrade-schema`。默认保留已有历史成分绑定；只有明确要求替换历史映射时才用 `--replace-snapshot`。当前成分回看标记 `current_snapshot`，不宣称历史时点可得。

## 公开接口

```python
summary = pool.read_market_summary(start="2026-09-01", end="2026-09-30", scope="all_stocks")
boards = pool.read_board_daily(start="2026-09-01", end="2026-09-30", kind=None,
                              scope="all_stocks", fields=None, limit=100000, offset=0)
```

板块接口仅离线读取发布结果，每行 `(date, scope, classification, kind, board_id)`；kind 为 `industry` 或 `concept`，分类 `tdx_hy2_gn`、行业层级 2。scope 支持 `all_stocks/exclude_known_st`。收益是小数，覆盖人数为同日交易样本交集。字段含 `board_name/member_count/trading_member_count/valid_return_count/avg_return/membership_as_of/membership_basis/snapshot_id/updated_at`。`date` 返回 pandas Timestamp。

`DataFrame.attrs.category_status` 提供日级类别状态、已处理/预期板块数、去重已映射交易人数和快照依据，区分 `ready/computed_empty/failed/not_provided`；无该日记录为 `not_computed`。`attrs.categories` 表示采集状态，采集失败时旧快照仍可读，不能据此清空已发布历史。分页到 `next_offset=None` 或空页结束，单页最多十万行。

Fundwise 分别取行业/概念收益 Top3，主线最大收益与最大覆盖可来自不同候选。固定四指数继续读取已有指数接口。部分股票或指数缺失不阻止其他输入出值；只有维度全部缺失时为空。

## 更新范围与发布

成交量修订影响当日及后五个市场交易日的量比；收益/交易样本/ST 修订仅影响实际日期及相关板块。重复相同写入不改时间戳；单纯量比变化不回算板块。新的成分快照只绑定新日，历史修订继续使用原绑定。读操作不计算、补数或联网。

消费缓存按日期、scope 和实际评分输入更新，禁止依赖 SDK 文件哈希、全池修订号或元数据变化重算全历史。结构升级只修改 features 所在单一 SQLite 文件；日级事实继续使用既有原子发布/恢复机制。回退代码前保留备份；旧 SDK 可读取原字段，不在回退时自动删除新增事实。

## 验证边界

本轮真实来源验证窗口为 2026-09-01 至 09-30；在隔离池执行初始化及 09-30 日更重放，没有修改生产池。10-08 的未来真实日更、生产安装和生产初始化尚未验证。
