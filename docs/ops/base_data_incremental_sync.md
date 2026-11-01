# 基础数据增量同步

日期：2026-10-02。分层定义见[两层契约](../design/data_layers.md)。实现基于 PR #2 的最终历史修复版本，并保留当前 HY 指数目录更新。

## 已实现的链路

1. 在源端及接收端共享锁下比较明确证券/日期范围的基础数据，导出真实新增与修订。
2. 接收端在写锁下校验包校验和、目标根、表结构、主键、范围及所有旧值；冲突在应用前拒绝。
3. 持久化整轮恢复记录，再分别提交目录、行情、财务、来源锚点、历史成分及复权源证据。
4. 股票变化在现有共同发布机制内重算选定因子、逐股指标、量能、市场和原绑定板块聚合。ETF 变化复用其 writer 更新依赖量比。目录/日历/板块来源变化也触发相关股票计算。
5. 核对全部基础后像后解除整轮读取/普通写入阻塞。重放已完成的新值无操作；真实进程中断可恢复。

```bash
aspool replica export \
  --source-root /path/to/source/data --target-root /path/to/receiver/data \
  --dataset stock-bars --dataset stock-source-facts \
  --dataset actions --dataset factor-anchors --dataset event-coverage --dataset factor-source-evidence \
  --symbol 000001.SZ --start 2026-09-01 --end 2026-09-30 \
  --output /path/to/transfer/delta.json

aspool replica apply --root /path/to/receiver/data --package /path/to/transfer/delta.json
aspool replica recover --root /path/to/receiver/data
```

导出比较两个可访问的已有池；源端和接收端必须先具备相同 schema。包可作为普通封闭文件传输，应用不联网，也不复制运行数据库。目标根在包中绑定；不能改包内容后继续沿用旧校验和。`apply-stock` 是仅接受股票 K 线/来源事实的兼容入口，其他域使用 `apply`。

## 数据集与范围

| 数据集 | 层与含义 | 选择范围 |
|---|---|---|
| stock-bars / index-bars / etf-bars | 基础：未复权来源行情 | 证券、交易日期 |
| stock-source-facts | 基础：带日期的来源昨收、ST、交易状态及名称日期依据 | 证券、交易日期 |
| actions / factor-anchors | 基础：来源公司行为和外部累计锚点 | 证券、生效日期 |
| event-coverage | 基础：成功下载事件区间，包括确认无事件的空响应依据 | 证券、覆盖截止日期 |
| factor-source-evidence | 基础：下载的 NONE/QFQ/HFQ 验证行情和事件 | 证券、来源 as_of |
| financial-reports / shareholder-counts | 基础：财报和股东人数 | 证券、报告/统计日期 |
| securities / index-memberships | 基础：证券信息、指数分类 | 证券 |
| calendar | 基础：交易会话轴 | 交易日期 |
| board-snapshot-sets / board-snapshots / board-source-state | 基础：原成分快照和选择状态 | 完整来源快照集合，受总预算约束 |
| 本地股票因子、市场/板块聚合、计算缓存 | Enriched | 不进入基础包；接收端计算 |
| etf-source-factors | 基础外部参考：当前 legacy:free-stockdb 输入 | 证券、生效日期 |

财报的报告期不等于更新日期；旧报告修订必须根据源端更新回执选择其报告日期，不能只导出最近几天。日期窗口不自动补齐范围外的来源事件/锚点；因子重算利用接收端已经保留的完整依据。首次接收端须先初始化，不能用近期包代替完整基础基线。

最多128只证券、两端所有数据集合计100000行、包大小256MiB。股票计算单证券后缀最多10000交易日，共同发布仍受250000变更行和256MiB意图预算限制；超预算明确失败，不隐式放宽。基础范围不会从完整源文件无条件扫描全部股票历史。历史修订读取明确证券后缀，近期同步读取近期后缀；输入日期不能被误当成全部依赖的截止日期。

元数据时间变化不会成为业务差异。源端缺行记录为 source_absent_keys，缺返回不授权删除。历史成分快照 ID 不允许改写内容；新增成分使用新 ID，已发布历史继续绑定原快照。无变化重放保持业务值与版本。

## 来源证据与因子谱系

新的因子初始化保存完整下载依据到 `.local/base-evidence/<pool>/<sha256>.json`。这是一项基础数据资产，和 `.local/reports/` 的操作日志不同，须通过 factor-source-evidence 同步并保留。摘要校验和不能替代原始依据。旧初始化仅留摘要的历史记录不能恢复出未保存的下载内容，本次不会补造。

新计算或修订的股票因子记录实际事件、来源锚点、必要前收盘、下载证据身份和成功下载区间的 input_hash 及 selected-factor-v2 算法标识。未重新计算的旧因子继续保留 legacy-v1，避免伪造历史算法或输入认证。基础变化与计算因子变化分别推进相应发布状态；发布 revision 用于本地一致性，不作为跨设备增量游标。

ETF 现存因子仍是外部导入参考；它们不是已建成的本地 ETF 公司行为计算链。接收端复权读取可使用这些参考，不把 ETF 行情同步成功解释为复权参考已在线刷新。

## 恢复与验收

整轮恢复记录在池外 `.aspool-state/<pool>/pending-replica.json`，带校验和。恢复先完成可能遗留的股票共同发布意图，再幂等执行剩余阶段。整轮完成前，公开读取和普通池写入报 RECOVERY_REQUIRED；只有显式 replica recover 在排他写锁内可续接。

验收覆盖行情修订与汇总重算、旧值冲突、重复应用、元数据无变化、来源缺行、预算拒绝、来源锚点修订及因子谱系、财务/股东输入、ETF 量比、未知交易排除、下载证据传递，以及实际进程在跨 WAL 提交后退出的恢复。详细测试回执见[最终整改验收](../evidence/base-enriched-20261002/acceptance.json)。

事件区间下载依据保存在基础证据目录的 coverage.sqlite；原始价格验证证据有索引，按证券/日期读取，不默认解码全部冷文件。

这是已完成的本地逻辑同步能力；SSH/文件运输和自动调度由部署环境配置。本次不安装调度、不写远端生产池。指数/ETF 扩展指标、W/M、按需 snapshot 计算器仍沿用既有能力边界，不因两层整改声称已实现。
