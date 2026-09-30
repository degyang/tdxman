# DG-02：Parquet 日线统一访问边界与验收

日期：2026-09-27。实施基点为 DG-01 已集成 main 的 `5b42c52`（包含 `bbb50b0` 恢复修复）；实现提交为 `6c85113`。本报告仅记录当前 feature 的实现及离线验收，独立审查、main 集成与 push 由协调者负责，不表示生产修复、迁移或后端选型已经完成。

## 实现与契约

新增具体的 `daily_access.DailyStorage`，集中证券日线的旧式/分年发现、指定键/年份候选、精确日期/字段读取、批读取、事件键金额关联、覆盖元数据、内部 DG-01 修订查询和既有前滚发布调用。没有后端注册表、插件框架、新数据库或自动拆分：不存在分年业务文件的证券仍写旧式单文件，已有分年布局只更新实际变化年份。`store.py` 的既有内部 helper 留作兼容委托。

公开 API 签名、两种符号格式、全部公开字段、原始价格与股/人民币元/百分数单位、NULL、股票/ETF/指数隔离、日期事实 overlay、批次/stale 与读取器版本检查保持。重复键在 lookback 隐藏旧行前检查；字段裁剪仍执行所选总体的原有 OHLCV/amount 校验。旧式与分年混存拒绝；分区 footer 的日期范围必须属于目录年份，不能用错误分区名剪掉有效行。

单证券正常读取直接定位该证券目录；没有先建立全池文件清单。空 symbols、缺失证券和不存在年份仍保留空结果与空池错误的区别。ETF 另保留旧池缺少 `asset_type` 时的 `ETF_NOT_FOUND`。为证明旧池全局 schema 是否存在该列，只有缺失/剪空/无标记候选才使用逐个 footer 的 schema 探测，遇到首个匹配即停止；正常已命中 ETF 不调用它。旧池没有 schema 索引，证明全局不存在时仍可能遍历全池元数据，这属于保留旧错误契约所必需的显式例外，协调者于本 dispatch 明确接受 metadata-only 回退；DG-03 应评估 metadata index，未宣称所有空选择都是常数成本。

增量 merge 读取最早候选起的完整依赖后缀，预热按五条实际历史 bar 确定；稀疏年与年末修订不能按固定自然日或只读当前年份截断。若当前文件已含足够前置 bar，不解码更早年份。enrichment 保留现有 30 自然日预热并补足五条实际前置 bar，写回仍保留所选文件中的其他行和扩展字段。限价递归计算与市场会话轴保持原有保守全历史策略，具体依赖收敛归 DG-05。

coverage 的实际 extent/count 来自各文件 footer，并随每次实际发布的插入键增量更新；no-op 元数据修复不再拼接全部年份的数据表。extent 缓存只在同一入口实例内复用，并逐次检查文件 dev/inode/size/mtime/ctime；业务键和值校验不跨版本缓存或删除。缺失 min/max 或含 NULL 的 footer 采用日期列读取回退并单列观测，不能据不完整统计静默跳过读取。正常有统计的冷年份只读元数据，不能将此描述为零 I/O。

发布继续使用 DG-01 的 `publish_file` 准备/前滚协议、池锁及同一 catalog 事务；临时 SQL 视图允许只读 catalog 上的金额关联。CLI 日线查询在同一共享锁中发现、绑定、执行，保留其原始诊断字段语义。显式 `compute_limit_events` 的强制重算语义未改变。

## 实际入口清单

| 已识别入口 | DG-02 处理 |
|---|---|
| DataPool `read_daily/read_research_daily/status` | 指定键/范围关系绑定；保留公开验证、字段与 overlay；status 包含分年股票/ETF，不计指数 |
| `etf_api._read/list_etfs` | 同一范围入口；ETF 开始日期、字段、单位及缺失 schema 错误兼容 |
| `limit_amount.EventAmountBatches` | 事件键/日期/amount 操作集中到边界；保留 NULL、重复/非法金额、资源与版本检查 |
| `limit_events.load_scope/_read_asset_types/_read_symbol_bars/compute_limit_events` | 范围身份与窄列批读；事实覆盖、日期轴和计算策略仍由业务层处理 |
| `daily_storage.merge_daily` | 依赖前置范围、读取、布局输出、覆盖/内部修订与发布经具体入口；滚动变更日志保持 |
| `enrichment._publish/audit_conflicts` | 前置范围表读取、发布、冲突日期读取经入口；不再自行遍历分年文件 |
| `baostock_source._rows` | 规划字段投影和精确范围读取；日期事实 catalog、来源冲突与失败处理不改 |
| `tdx_online._sync_daily_run/_publish_stock_rows/update_daily_offline` | 同一 merge；调度读取最近五条实际日期，跨稀疏年份；vipdoc 解析属于外部源格式 |
| `free_stockdb.import_daily/_write_daily/validate_daily` | 同一 merge 或完整替换操作；完整替换仍读完整单证券历史；诊断保留重复/非法计数返回 |
| `fundamentals._publish_quote_rows` | 存在性检查经边界，实际日线写入走 merge；当前 snapshot 文件不属历史日线 |
| `scripts/rebuild_limit_history.py` | 日线 manifest 文件集合经入口，不再自行递归选择布局；现有恢复/发布流程不扩展 |
| `scripts/supplement_limit_references.py` | 池内 OHLC 范围/字段读取经入口；其来源证据 Parquet 与历史事实发布流程不是日线布局操作，本轮未运行 |
| `store.bars_path/daily_path/daily_directory/daily_year_path/daily_paths/read_daily_table` | 兼容 helper 委托统一发现/读取；writer 不再靠最新单文件代表完整历史 |

下列路径使用有明确理由的范围外保留项，不作为新后端或生产迁移入口：

| 保留项 | 理由与后续 |
|---|---|
| `index_api.py/index_pool.py/index_lists.py` | 指数独立 namespace、单位与 API；本期验证隔离，不统一成股票事实表，DG-03 必须保留全部指数要求 |
| `pool._read_fundamentals/fundamentals` snapshots | 当前低频基本面快照，不是证券历史日线物理布局；不删除或迁移 |
| `free_stockdb` 分钟/复权、CLI 分钟路径 | 明确非日线域，既有格式、原单位与诊断保留 |
| `scripts/aspool_recovery_baseline.py/aspool_verify_baseline.py` | 恢复工具必须枚举全部恢复对象并按物理相对路径/hash 校验；指定证券恢复案例固定指向已冻结旧式快照，不能改写历史证据为分年迁移能力 |
| `scripts/aspool_dg01_lab.py/aspool_delta_probe.py` | 已冻结旧式快照上的 DG-01 实验/物理差异诊断；路径是实验对象标识，不是运行时权威读写入口，本轮不运行或改写旧证据 |
| `scripts/audit_daily_enrichment.py` | 对 `daily.before` 与当前文件逐一比较物理轴/原始字段，检测文件消失是其目的；此旧物理快照审计不支持跨布局迁移，DG-07 使用逻辑全字段比对另验收 |
| `scripts/benchmark_aspool.py` | 历史 quote/legacy 比较 harness，复制及注入固定旧式文件；未将它作为分年性能验收，本期不运行；增长/完整后端基准归 DG-03 |
| `scripts/benchmark_regime_bounded_read.py` | 使用公开 API；临时输出/缓存 Parquet 属验收产物，不是业务日线 |
| Fundwise freshness 文件统计及外部脚本 | 消费方仓库不授权修改；公开依赖修订与部署入口复核归 DG-06/DG-07，不宣称发现机器上所有外部脚本 |
| DG-01 `change_protocol.py` 恢复对象/Parquet 序列化 | 通用持久发布与恢复层必须按 manifest 物理对象操作；由日线边界调用，不能用读入口替代其恢复协议 |

## 验证证据

最终源码 `6c851137e514c3e0795d3217385fcdbec945ec45`、源码 SHA-256 `7b07e04b05299e9b3f70b887124d0c6f073169d1b5c49e81febdff7a59f090a8` 下，完整单元套件 **666 passed、18 subtests passed、2 skipped**，180.19 秒；两个跳过项要求外部 `TICK_STOCK_PANEL_ROOT`，与本次日线域无关。scoped Ruff check 与新增文件 format check 通过，完整套件仅出现两条 CLI `fetch_arrow_table` 弃用提示。

五年窗口 2021-09-24～2026-09-24，1213 个已发布会话、261 个检查窗口、258 批，82,772 条事件全部逐批精确比对，30 条金额独立样本一致，NULL 金额 0，最大批 1004 行。耗时 **71.178 秒**，进程 highwater **298,508,288 字节（约 285 MiB）**，小于 2 GiB；无 swap/spill，结束 fd=4/线程=21，与开始相同。发布来源摘要及源码指纹前后不变。金额的独立精确比对是 30 个样本，不称为全部金额逐行审计。

最终完整套件与五年 P0 结果见 [validation.json](../evidence/data-remediation/20260927-dg02/validation.json)、[单元日志](../evidence/data-remediation/20260927-dg02/unit.log) 和 [五年基准](../evidence/data-remediation/20260927-dg02/p0-five-year.json)。该摘要记录源码指纹、实际提交、独立 `.venv`、明确数据根、命令、结果及限制；成功证据在交接时复用，不因合并或所有权转移重跑。

新增 26 个参数化/专项测试覆盖 legacy 与 multi-year 的公开日线/research、ETF、指数隔离、稀疏事件金额、事实覆盖、缺失选择、导入重跑、repair/enrichment、CLI、混存拒绝、字段裁剪后的值校验、lookback 前重复校验、稀疏跨年滚动依赖、冷年份解码禁令、footer 替换/无统计回退和诊断计数。已有 `test_daily_remediation` 的跨年等值与第二分区失败回归一起通过 47 项。初始小范围回归中的 11 个金额失败揭示普通 relation view 不适用于只读 catalog，修复后受影响金额测试通过；新 fixture 曾漏填 batches 必填计数，补齐后通过，不把试验失败隐去。

## 限制与后续 DG-03+

- 没有生产写入、在线 fetch、migration、cleanup、Fundwise 改动、main merge/push 或新的全池实验复制；五年读取仅使用协调者指定恢复快照。
- 单行组旧式文件仍可能整文件读取/原子重写；分年读取仍检查该证券各年份的 footer。元数据不完整时需要日期列回退，schema 不存在证明可能遍历旧池元数据。
- 完整替换入口读取完整单证券历史，以证明没有静默删除；限价会话轴与递归后缀未在本阶段缩短。没有以减少读取替代完整前置状态。
- 内部 revision 查询仅返回 DG-01 文件逻辑修订，公开 `dataset_version=None` 与 Fundwise 文件统计兜底保持；不是 DG-06 的发布/依赖修订契约。
- 五年基准是一次本机、未清冷缓存、既有快照的正确性/RSS 验收；不提供冷启动/P95，也不代替 1×/2×/5× 增长、并发或完整候选后端结果。
- DG-03 仍需完整 schema/键/扩展来源与 ETF/指数的候选后端和增长/并发决策；DG-04 需生产缺口/冲突核验与重新发布；DG-05 需字段依赖/递归收敛；DG-06 需公开修订/Fundwise 配套；DG-07 需影子、恢复、切换与回退；DG-08 需引用驱动保留/退役。当前恢复目标未知项仍按 DG-00 保留。
