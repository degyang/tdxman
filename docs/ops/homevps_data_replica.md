# homevps 完整数据副本

本机是当前完整数据镜像的来源；homevps 接收同一成品，用隔离副本验证同步。Jakarta 的约两年历史需求另行设计，本次不修改 Jakarta 数据。

## 完整复制

不能把活跃数据库直接 rsync 到消费者使用的目录。先停止外部数据库写入者，在 `aspool.pool.pool_lock(root, write=True)` 内检查所有 WAL 为空，将整个数据目录复制到私有快照（排除 SQLite WAL/SHM），释放源锁后计算快照的文件大小与 SHA-256 清单。快照包含 catalog、stocks、indices、etfs、fundamentals、adjustments、features 和 snapshots 八个运行库。目录中的既有零字节非数据库文件也保留，不能因此省略真实数据域。

2026-09-30 本次封闭快照为 `.local/transfer/homevps-20260930/data/`；快照制作、传输和验收脚本及日志在 `.local/reports/homevps-20260930/`。下列命令从仓库根运行，需要 SSH 已认证；仅复用本次已生成的封闭快照，不能用运行 `data/` 替换其源路径。

```bash
ssh homevps 'mkdir -p /home/ubuntu/Services/tdxman/.local/receive/homevps-20260930/data'
rsync -a --whole-file --partial --info=progress2 \
  -e 'ssh -o BatchMode=yes' \
  .local/transfer/homevps-20260930/data/ \
  homevps:/home/ubuntu/Services/tdxman/.local/receive/homevps-20260930/data/
rsync -a .local/reports/homevps-20260930/{manifest.json,SHA256SUMS} \
  homevps:/home/ubuntu/Services/tdxman/.local/reports/homevps-20260930/
ssh homevps 'cd /home/ubuntu/Services/tdxman/.local/receive/homevps-20260930/data && sha256sum -c ../../../reports/homevps-20260930/SHA256SUMS'
```

传输到暂存目录；核对文件集合、大小、全部哈希、SQLite 结构和公共读取后，才允许在没有消费者或写入者的情况下安装。本次远端原先没有 `data/`，因此使用目录重命名安装，没有覆盖旧运行池。后续替换已有运行池须先保留一致恢复点。

## 同步验收范围

远端代码固定到 Git SHA `8a461f21100e6da16928b58e0c4a1adbd21ee8c2`，使用仓库 Python 3.12 虚拟环境和 `uv sync --frozen`。原始接收镜像安装为 `/home/ubuntu/Services/tdxman/data`。写入测试仅在 `.local/labs/homevps-20260930/data` 进行。

测试窗口明确为 2026-09-29，使用公开 `aspool sync --start 2026-09-29 --end 2026-09-29 --limit 3`，覆盖股票、指数、ETF。股票测试先在实验副本删除两个当日原始行，再检查在线修补；汇总测试删除实验副本同日 `all_stocks` 汇总，再检查本地补算与镜像。各类重复执行后核对业务变更回执。源端和远端对股票、派生、市场、指数、ETF、财报及股东人数进行相同公共 API 读取。

本次复制 14,131,707,904 字节，九个文件哈希全部一致，七个 SQLite 库 `quick_check` 通过，七类公共 API 抽样结果两端相同。测试发现显式日期传入 `count=None` 会被股票 writer 拒绝，已在 `8a461f2` 修复，相关 27 项测试通过。

同步测试的条件与限制：

- homevps 直连三个 MAC 端点均握手失败；同一组端点在本机均通过。homevps 标准端点仅 1/77 可用。成功的在线测试使用临时回环 SSH 转发经过本机出口，完成后已关闭；不能作为远端自主日更验收。
- 冷态历史修补触发 30 秒写入预算并回滚。对指定日期读取预热后，股票写入耗时 393 ms，补回两行；重复股票同步 `requested=0`，一个缺失汇总在本地补算。三个指数无业务变化；三个 ETF 中一行发生变化，重复执行均无变化。写入预算没有调整或禁用。预热的物理扫描边界未验证，不能据此宣称冷态有界性能已通过。
- 内容核对发现原始镜像已有 292 只股票的 9 月 29 日 MA20、above_ma20、updated_at 与原始库不同。同步没有新增该差异。仅在实验副本按确认的证券与日期修复镜像后，同日 5,578 个键按列核对差异为零。原始接收镜像及本机权威池的既有差异仍待处理；浅层 READY 与版本匹配不证明内容相同。

本次未运行真实新日日更、全仓测试或全历史 deep 校验。远端自主运行须先解决 MAC 网络可达性，冷态预算与原始镜像差异也保留为待处理项。收盘后日更仍沿用既有入口：

```bash
ssh homevps 'cd /home/ubuntu/Services/tdxman && bash scripts/ops/run_daily_data_pipeline.sh --root data'
```

本次不配置自动调度，不将两台机器的本地锁解释为跨机互斥。homevps 实验结果不反向同步到本机权威数据池。

验收结果及限制见 [本次证据](../evidence/homevps/20260930-full-replica.json)。完整逐文件清单及操作回执保留在两端 `.local/reports/homevps-20260930/`。
