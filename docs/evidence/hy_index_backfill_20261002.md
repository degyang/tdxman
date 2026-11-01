# HY 行业指数入库与历史回填（2026-10-02）

## 范围与基线

- 在线目录：HY 128、HY2 345、GN 269；三类去重后 706 个代码。
- 变更前 `indices.sqlite` 中 HY2、GN 均有日线；HY 仅有与其他类别重叠的 36 个，缺少 92 个仅属 HY 的指数。
- 生产目录原先只采集 HY2/GN/FG/ZS。此次加入 HY，保留原有 FG/ZS 和 benchmark 范围。

## 发布与恢复点

- 变更前的 `catalog.duckdb` 和 `indices.sqlite` 备份位于 `.local/recovery/hy-index-before-20261002-005845/`，附 SHA-256 清单；备份 SQLite 的 `PRAGMA integrity_check` 返回 `ok`。
- `aspool directory --type index --root data` 发布 950 个活动指数，新增 92 个；分类计数为 HY 128、HY2 345、GN 269、FG 146、ZS 100。
- 修正了 `aspool sync --type index` 默认采用最近 10 个交易日窗口的问题。首次同步因该窗口给 92 个 HY 指数各写入 10 条；逐代码核对行数、日期和来源后，在一个事务中撤回这 920 条，再按完整可得历史回填。

## 同步与验收

- 完整回填报告：`.local/reports/index-sync/05c68c197e93426b88948c15a531f817.json`。92 个 HY 指数全部为 `bootstrap`，新增 349,664 条日线，失败、隔离异常、过期均为 0。
- 新增的 92 个指数中，90 个从 2011-01-04 开始，另两个分别从 2011-03-16、2019-11-20 开始；全部更新至 2026-09-30。按数据源实际可得历史保存，不补造早于来源起点的数据。
- 逐代码检查 `catalog.duckdb` 与 `indices.sqlite`：HY 128/128、HY2 345/345、GN 269/269 均有日线，三类共 706 个不同代码，最新交易日均为 2026-09-30。公开 `DataPool.read_index_daily()` 已实读 `881106.SH` 最近两条。
- 相关单元测试 47 项通过；`ruff check` 和 `git diff --check` 通过。

全目录的初次 10 日同步报告为 `.local/reports/index-sync/b0bfc46f5b994ad1bfcccd562b106745.json`：946 个成功、4 个 FG 指数因来源早于本地重叠日期失败，另有 1 个 FG 指数来源只到 2026-09-29。这些标的不属于本次 HY/HY2/GN 目标；其旧历史未被删除。
