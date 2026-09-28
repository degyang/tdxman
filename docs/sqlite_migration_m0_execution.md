# M0：事实迁移与 JakartaVPS 开发数据交付

日期：2026-09-28。迁移实现提交：`0191721`。本记录对应 FW-02 的事实迁移及 M0 异机开发数据；生产仍使用旧池，没有切换 Fundwise。

## 本机迁移结果

冻结源为 `/home/ubuntu/.aspool` 的 catalog/lake，共 13,742 个文件、2,702,924,707 字节。源与快照逐文件 SHA-256 一致，源池未修改。清单指纹：

```text
37ed77ffe5bafd00b1ffe6a8b7263219b7e445c68568c209bfa49ecccb44abce
```

全部 5,586 只股票已迁入，实际日线区间 2000-01-04 至 2026-09-24；这是全池最早/最晚日期，不表示每只股票都有同样完整的历史。

| 表 | 行数 | 本阶段内容 |
| --- | ---: | --- |
| daily_bars | 16,350,085 | 原始日线规范化值 |
| daily_features | 16,350,599 | 来源日期事实、基础有效性；含 514 条无原始 K 线事实 |
| corporate_actions | 244,013 | 186,622 条事件、57,391 条来源因子锚点 |
| market_daily_summary | 0 | 等待 FW-03 日频计算 |

每只股票提交前回读比较全部迁入来源字段，最终 `integrity_check=ok`，四表和五个必要索引已创建。数据库 7,897,018,368 字节（7.35 GiB），SHA-256：

```text
484e71f455523c23c9a428d86795de8b0059783949d431d4bc980e3d554662b7
```

最终全量迁移用时 33 分 56.91 秒，峰值 RSS 714,140 KiB（0.68 GiB），无交换。此前维护全部二级索引的试跑已中止，最终导入改为末尾创建非唯一辅助索引；主键和唯一约束始终启用。上述资源数字仅对应成功的最终运行，**不代表五年事件金额 API 的 RSS 验收**。

完整机器可读摘要见[本机证据](evidence/sqlite-migration-assessment/20260928-local-facts-migration.json)。逐股进度、压缩字段差异与原始结果保留在数据根 `_reports/`；运行日志及 time 记录位于 `data/_reports/migration-bootstrap/`。

## 源差异的处理

- 6,125,240 条字段别名优先级差异、16,930 条按日参考昨收优先级差异均记录原候选；它们是字段差异条目，不能当作异常日线行数。
- 24 条 ST 冲突按日期来源优先级选入来源候选。原始标记均无来源/名称日期，另一侧为 Baostock 日期记录；这不是对真实历史 ST 的独立认证，最终 ST 仍待计算。[逐条复核](evidence/sqlite-migration-assessment/20260928-migration-st-review.json)保留依据。
- 旧因子中 295 个北交所代码的市场后缀，经公共目录确认后规范化，共 1,088 个锚点；原市场/代码保存在载荷和源键中。`920305.BJ` 缺当前目录条目，按现有 A 股识别规则保留并记入报告。
- 1,085 条 ETF 因子及 201 条其他基金/REIT 因子不进入股票库，原文件保留。事件与因子锚点并存不意味着二者重复累乘；选定累计因子尚待 FW-03。
- 原先 277,429 条参考价另存为计算证据，去掉批次身份，保留旧 stale 状态供复核，不伪装成权威来源事实。

必要公共域按原格式配套：8,156 个文件、261,841,805 字节，包括 ETF、指数、基本面、原始因子及公共 catalog。后者只有 universe、security_lifecycle、security_calendar、index_coverage 和 ETF coverage；未复制旧股票 Parquet、股票发布/批次表。

## JakartaVPS 交付结果

**M0 已通过**。本机和 JakartaVPS 均使用项目内 `data/_staging/fw02/`，后者完整路径 `/home/ubuntu/Services/tdxman/data/_staging/fw02`。这是已验证的开发数据根，尚未安装到生产 `data/stocks.sqlite`。

依赖安装、首次复制及校验均由 tmux 托管。股票库先关闭并记录指纹，使用 rsync 进入独立 `fw02.incoming`；仅在校验退出码为 0、样例与源一致后改名为正式开发目录。源端旧池、冻结快照仍保留；本轮中断试跑和样本数据库已清理，报告归档至 `data/_reports/migration-bootstrap/superseded-runs/`。

- 完整股票数据库 SHA-256/大小一致；源端完整性通过结果据此复用，远端结构、五个必要索引、四表计数和 5 条实际日线样例通过。最终目录使用 `stock_connection` 读取成功，数据库权限设为 `0444`。
- 公共域 8,156 个文件逐个大小/哈希通过；原参考价证据一致；指数 5 行、ETF 5 行、证券目录 1 行、日历 7 行经现有 SDK 读取通过。
- 远端 Python 3.11.15、SQLite 3.50.4；实际股票验收用时 88.75 秒，峰值 RSS 54,460 KiB。源端 Python 3.12.14、SQLite 3.53.1；异机读取已验证，不要求版本数字完全相同。
- 完成后剩余 2,419,507,200 字节（2.25 GiB）。适合只读开发与隔离小样本写入，不足以再复制完整股票库；整库派生回填先核算数据增长/WAL/临时空间，不在这台 VPS 直接启动。

证据：[远端股票](evidence/sqlite-migration-assessment/20260928-jakarta-stock-verify.json)、[远端公共域](evidence/sqlite-migration-assessment/20260928-jakarta-public-verify.json)。运行报告另在远端 `data/_reports/migration-bootstrap/`。后续接收其他设备时复用已封闭工件和源端验收，执行同样的哈希、结构与读取验证，不重跑来源迁移测试。

## 验证与能力边界

新增快照用例 6 项、存储/迁移用例 4 项通过；代码变更后只重跑受影响用例，Ruff 和差异格式检查通过。样本迁移覆盖主板、创业板、北交所；公共域逐值比较和参考价 Parquet 回读比较通过。复用已有验证，没有重复跑旧全套测试。

异机完整性证据复用实现为 `2eb136b`：只有完整数据库哈希/大小吻合，且源报告明确完整性通过时，显式 `--reuse-source-integrity` 才允许跳过重复索引遍历。仍在接收端检查 schema、索引、计数和实际读取；报告标记 `source_reused_after_sha256_match`。扩展受影响用例验证了成功复用、缺失源证据拒绝、数据库被修改后拒绝，仅重跑该用例并通过。Jakarta 最初重复全量检查的慢速尝试已停止，日志保留，不计为通过。

当前股票读取入口是 `stock_connection(root)` 中的只读 SQL；指数、ETF 和公共目录使用现有 DataPool 接口。[开发说明](vps_development_data.md)提供样例。旧股票 `DataPool.read_daily` 尚未接入 SQLite。

接下来按依赖推进：FW-03 先完成因子/参考价/ST、限价/连板/MA20、日汇总和局部 writer，再完成 FW-04 公开接口与 FW-05 Fundwise 适配。市场汇总空表、派生列未就绪均是当前明确边界，不计作 Fundwise 已可运行。首次封闭工件复制也不计作 FW-09 活库增量同步已通过。

## 2026-09-28 派生成品更新

上述哈希和空汇总状态属于初始 M0 工件。随后本机同一路径已完成 FW-03 历史派生，最终为 9,218,285,568 字节，SHA-256 `6eb13bafe07ce0fcc1a24047761dd91b26ba31302dedcc6369276993487b2808`；原始日线和迁入事实数量保持不变。见 [派生验收](evidence/sqlite-migration-assessment/20260928-derived-result.json)。Jakarta 当前仍是已验收的初始 M0 副本，不能套用本机的新哈希。新工件使用 `verify_stock_derivation.py` 验收，不再使用要求初始空汇总状态的旧校验入口。
