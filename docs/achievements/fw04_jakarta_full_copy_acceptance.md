# Jakarta 全量副本与 Fundwise 消费验收

2026-09-28 13:12（UTC+8）完成。新版全量数据已安装到 `/home/ubuntu/Services/tdxman/data`，Fundwise 已参与真实 SDK 和 HTTP 验收。没有启用增量同步，没有更改默认生产池配置。

## 数据与安装

- 8,157 个唯一业务文件，共 9,480,127,373 字节；包含股票库、catalog 与 lake 公共域。所有实际文件大小与 SHA-256 均匹配源清单。
- 股票库 9,218,285,568 字节，SHA-256 为 `6eb13bafe07ce0fcc1a24047761dd91b26ba31302dedcc6369276993487b2808`，与源端已验收的全历史派生成品一致。
- 校验耗时 91.53 秒。检查 schema 与样本；根据全文件哈希匹配复用源端完整性与行数结果，没有重跑全库 integrity_check 或 COUNT，也未重复辅助 agent 的测试。
- 接收目录位于 `.local/receive/data`；校验通过、确认无数据库使用者后，旧 data 移至 `.local/recovery/m0-before-fw04-20260928`，新目录重命名安装。新股票副本保持只读，避免开发过程中意外形成第二个写入源。
- 运行 data 仅有 `stocks.sqlite`、`catalog.duckdb`、`lake/`；正常读取可以生成 WAL/SHM。报告与恢复材料都在 data 之外。旧 M0 保留为本次安装恢复点，未冒充活动数据。

本机 tmux 执行完整文件传输（rsync whole-file + zstd），远端 Fundwise 环境安装和验收也使用 tmux。复制清单原先重复列出一次 catalog.duckdb；校验器拒绝安装。确认重复条目大小和哈希完全相同后去重，再完成真实字节校验，未重传股票库。复制脚本现会在发起传输前拒绝重复路径或不一致的总字节数；重复清单拒绝、修正清单接受的定向检查通过。此前文档 8,158 文件/9,483,547,533 字节是未去重清单统计，以本记录为准。

证据：[副本与安装](../evidence/sqlite-migration-assessment/20260928-jakarta-complete-replica.json)。完整逐文件报告保存在两端 `.local/reports/`；摘要包含清单哈希，可追溯完整报告。

## Fundwise 已介入的工作

Jakarta `/home/ubuntu/Projects/Fundwise` 已更新代码，使用 `uv sync --frozen --extra web` 建立环境；实际导入的 aspool 来自 `/home/ubuntu/Services/tdxman/src/aspool`。

消费验收显式指定新根，输出位于 Fundwise `outputs/sqlite-acceptance/`：

1. 股票及指数最新两日日线读取成功，公开契约为 v3。
2. 2026-09-01 至 09-24 共 18 日市场评分计算成功；最新日四维分数、综合分及涨跌停数与本机全历史结果一致。
3. 同口径最新日综合分 19，收盘涨停 53、跌停 12，评分质量明确为 known_subset。
4. 使用已获取的 614 个板块成分缓存，生成 578 条近期主线排名，状态 partial；没有重复网络拉取相同成分。
5. 临时回环 HTTP 服务的 `/api/regime/latest`、`/api/regime/coverage`、`/api/regime/mainline` 均通过，检查后服务退出，没有留下临时监听进程。

证据：[Fundwise 消费验收](../evidence/sqlite-migration-assessment/20260928-jakarta-fundwise-acceptance.json)。没有重算完整 6,479 日作为重复测试；已有全历史计算与五年 RSS 验收继续复用。

开发启动示例（仅本机回环，不是公网发布）：

```bash
cd /home/ubuntu/Projects/Fundwise
.venv/bin/fundwise web serve \
  --aspool-root /home/ubuntu/Services/tdxman/data \
  --regime-cache outputs/sqlite-acceptance
```

这份验收缓存只有近期 18 日，不能误称远端已有完整多年模型缓存。完整行情与日派生历史已经在远端，后续可按所需窗口计算模型结果。

## 保留边界

历史限价、ST、连续性缺口仍导致周期阶段不可用，不能把副本一致当成输入完整。源端全历史模型分数、周期和主线详细状态见 [FW-04](fw04_sqlite_public_api_execution.md)。周/月、多模型执行、默认生产配置切换与后续增量同步尚未在本次验收中完成。
