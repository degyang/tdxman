# Orca 配合 Aspool 整改的执行约定

日期：2026-09-27。适用范围：Windows Orca + Ubuntu-24.04 WSL 中的 tdxman 开发与隔离验证。整改依赖及验收以 [推进方案](data_remediation_execution_plan.md) 为准。

## 1. 已验证的工具链

- Windows Orca 1.4.212；WSL 入口为 `/home/ubuntu/.local/bin/orca-ide`，通过托管桥接连接桌面运行时。
- `orca-cli`、`computer-use`、`orchestration` 已通过 `npx skills` 安装到 WSL 用户的 `~/.agents/skills/`，目标 agent 为 Codex。新一轮对话可发现；不另装 Linux Orca 桌面应用。
- 三个版本匹配指南均能读取；`orchestration run-list` 只读查询通过。没有创建 Run、Task、Dispatch 或启动 worker。
- `computer capabilities` 返回 Windows provider 能力；未进行 GUI 点击、输入或截图，不能据此声称 GUI 操作已端到端验收。
- 临时 worktree 创建于 `/home/ubuntu/orca/workspaces/tdxman/orca-toolchain-smoke-20260927`；WSL Git 能读写其管理信息，`worktree current` 能识别同一工作区。
- 终端执行验证得到 `platform=linux`、`WSL_DISTRO_NAME=Ubuntu-24.04`、`ORCA_CLI_COMMAND=orca-ide`；Git、Python、Codex 来自 WSL，Codex CLI 为 0.157.1。终端指令的实际完成由输出及结果文件核验，未把 `accepted=true` 当作执行成功。
- 临时工作区没有 `.venv`，也没有继承主工作区尚未跟踪的整改方案。正式使用前必须保存代码基线并初始化环境。

详细结果及验证边界见 [verification.json](evidence/orca-toolchain/20260927/verification.json)。本轮未测试 agent 登录/模型调用、多 agent 调度、生产数据写入或候选后端。

## 2. 工作区与数据边界

| 对象 | 约定 |
|---|---|
| 当前主工作区 | 保存现有未提交成果、审查与集成；不把临时验证当成正式整改分支 |
| 整改工作区 | 由 Orca 创建，使用显式提交基点；一个工作包一条可回退分支，按阶段复用，不为每个小任务新建副本 |
| Python 环境 | 每个工作区独立 `.venv`，editable install 指向该工作区；不能链接主工作区 `.venv` |
| 已有恢复基线 | `/home/ubuntu/aspool-recovery/20260927-data-remediation/snapshot`；作为固定输入保留，不在上面运行写入实验 |
| 实验数据 | `/home/ubuntu/aspool-labs/<run_id>/<task>/pool`；每个写入者独立副本，显式传 `DataPool(root=...)` 或 CLI 子命令的 `--root` |
| 生产池 | `/home/ubuntu/.aspool`；Git worktree 不隔离这个默认路径，生产维护必须指定唯一写入者并遵守池锁 |
| 小型证据 | `docs/evidence/data-remediation/<run_id>/`；记录基点、差异指纹、数据水位、参数、依赖及结果 |
| 大型输出 | 独立实验目录，记录大小、来源和清理条件；不提交 Git，不跟随 worktree 删除自动清理 |

准备实验副本前核算剩余磁盘、源副本大小和临时空间。可以使用文件系统支持的写时复制或普通复制；不能硬链接可修改的数据文件。不同工作区不会隔离端口、环境变量、缓存、数据库或后台进程，实际用到的资源须单独分配。

不同 worktree 可同时做不互相影响的普通检查；耗时、RSS、I/O 和恢复性能基准单独运行，避免资源竞争污染比较。

## 3. 下一轮的具体顺序

| 阶段 | Orca 的配合 | 进入下一步的条件 |
|---|---|---|
| 保存推进基线 | 在当前工作区审查并提交原则、方案、恢复脚本、小型证据；记录 SHA | 当前未跟踪材料进入可复现基线，不依赖聊天或复制旧提示 |
| 补齐 DG-00 | 一个正式整改 worktree 完成字段契约、消费者/写入入口清单和恢复目标 | 已有演练证据与剩余未知项对应；区分本机恢复和异机灾备 |
| DG-01 → DG-02 | 默认顺序实施，前项验收后更新下一项基点 | 真实变更契约和旧后端兼容通过；避免两位实施者同时改 `daily_storage.py` / `pool.py` |
| DG-03 与 DG-04 | 候选后端使用实验池；生产修复使用单独维护任务 | 分别满足原方案依赖；候选实验不独立修改生产事实 |
| DG-05 / DG-06 | 先固定依赖与版本接口，再做上下游适配 | Fundwise 在自己的仓库提交，记录两边兼容提交 |
| DG-07 / DG-08 | 集中集成、验收、切换和退役 | 正确性、五年 RSS、并发与恢复证据齐备；清理满足对象引用条件 |

目前不需要多 agent 才能开始。若后续明确授权并行，可优先分配只读消费者审查、独立测试或候选评估；写同一模块、写同一数据池、存在未完成依赖的任务不能靠 worktree 自动解耦。

## 4. 常用操作

以下准备命令在 WSL 中执行；首次使用或 Orca 升级后读取当前版本指南，不凭记忆拼接参数。

```bash
orca-ide status --json
orca-ide skills get orca-cli
orca-ide worktree current --json
orca-ide worktree list --repo id:22c2da29-bb15-4753-a964-6f46a150441c --json
npx skills list --global --agent codex
```

保存基线后，从主工作区创建有关联的正式整改分支。将占位符替换为实际提交；不要依赖默认 `origin/main`，它可能不包含刚保存的本地成果。

```bash
orca-ide worktree create \
  --repo id:22c2da29-bb15-4753-a964-6f46a150441c \
  --name aspool-dg00-contract \
  --base-branch <BASELINE_COMMIT_SHA> \
  --parent-worktree active \
  --setup skip --json
```

保留返回的完整 `worktree.id`（包含仓库 ID 与路径）。本次环境在 Windows 侧显示 UNC 路径，在 WSL 侧对应 `/home/ubuntu/orca/workspaces/...`；不手工改 `.git` 指针，不截断 ID。`--setup skip` 跳过仓库 setup hooks，但创建仍可能打开配置的默认终端；不把它等同于完全无进程启动。

进入新工作区后准备环境，并核验导入位置：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -c 'from pathlib import Path; import aspool; p = Path(aspool.__file__).resolve(); print(p); assert Path.cwd().resolve() in p.parents'
```

安装时记录依赖版本；性能对比须与基线使用一致版本或明确记录差异。当前仓库没有因本次工具链准备新增依赖锁文件。按工作包选择必要测试，不为验证 Orca 重跑全部行情数据。

查看终端使用 `terminal list/read`。发送命令后检查退出码、输出和产物；agent 启动还须确认 TUI 就绪和实际开始处理，不能只看输入回执。需要多 agent 时，先读取 `skills get orchestration` 及其中相关 placement / recovery 引用，使用真实 Run/Task/Dispatch 追踪；本轮没有启动此流程。

## 5. 收尾与证据

实施与审查交接必须给出实际已测源码指纹/提交、精确命令、退出码、结果日志和验证限制。协调者复用实施者完成的验证证据，不因所有权交接、fast-forward 合并或更新文档重复执行未改变的成功检查；仅新的代码变更、失败或具体证据缺口触发受影响范围的复验。耗时五年基准和完整单元套件顺序运行，使用各自明确的隔离根目录，并记录验证时是否还有其他资源竞争任务。

- 工作区状态注释用于展示进展，验收结论必须对应推进方案与证据。
- 合并前核对基点、工作区差异、实际导入代码路径和数据根目录；Git 提交成功不代表数据修复已完成。
- 删除工作区前检查未提交/未合并成果及其终端和后台任务；不得用 `--force` 掩盖状态不明。
- 数据副本单独按恢复/引用规则退役，保留恢复点不能因为代码工作区结束就删除。
- CLI 输出先筛选字段再保存；不将原始 `repo list`、凭据、运行时认证字段或完整其他会话内容写入证据。

工具链可用于下一步开发；DG-00 尚未验收，生产修复、存储切换和多 agent 调度仍未执行。

## 6. Codex 模型与 effort 约定

模型属于 **Codex 会话/worker**，不属于 Git worktree 本身。同一 worktree 可以有多个不同配置的会话；下列规则按任务角色选择，启动时记录，不能仅用工作区名称判断。

**用户规定：未经明确确认，effort 最高为 `high`。**使用 `xhigh`、`max`、`ultra` 或任何高于 `high` 的级别前，必须说明具体任务、拟用级别和升级原因，并获得用户明确确认；未回复不视为同意。确认仅覆盖用户指定的范围，不自动扩展到其他任务或 worker。此约束同样适用于新建、恢复、复用及会话内调整；它是执行约束，不能把下面的默认 TOML 配置当作技术上的强制上限。

| 任务角色 | model | effort | 适用范围 |
|---|---|---|---|
| 默认实施 | `gpt-6-sol` | `high` | DG-00 契约补齐，DG-01/02 实现，DG-04 修复工具，DG-06 已确定接口的适配和测试 |
| 复杂设计与诊断 | `gpt-6-astra` | `high` | DG-03 完整存储选型，DG-05 递归依赖/状态收敛，跨模块一致性或难以定位的问题 |
| 关键验收与审查 | `gpt-6-astra` | `high` | DG-07 恢复/切换/回退方案与结果审查，DG-08 关键恢复点退役判断 |
| 单独指定的资料整理 | `gpt-6-luna` | `medium` | 清单排版、已验证证据归档、无业务判断的文档整理；不承担字段契约、事实冲突裁决或关键验收 |

这是按本项目任务风险制定的执行选择，不是已完成的模型效果对比。官方将 Astra 定位为最高能力、Sol 用于要求较高的推理、Luna 用于高效重复工作；实际成本和账号可用性未做调用验证，不能承诺提速或费用比例。[官方模型说明](https://developers.openai.com/api/docs/guides/latest-model)

项目默认规则由 `CLAUDE.md` 和本节维护。`.codex/` 已加入 Git 忽略，仅作本地配置；新 worktree 不会通过 Git 继承其中的 `config.toml`，启动时必须按下面的命令显式指定 model 和 effort。若使用本地项目配置，它仅在受信任目录加载，CLI 参数优先于项目配置。用户级 WSL 配置在本次检查时是 `gpt-6-luna / medium`；该全局默认未修改，已有会话也不会因新增文件自动切换。[官方配置优先级](https://learn.chatgpt.com/docs/config-file/config-basic#configuration-precedence)

### 普通单会话启动

在目标 worktree 中启动默认实施会话：

```bash
codex --model gpt-6-sol -c 'model_reasoning_effort="high"'
```

需要由 Orca 管理终端时，使用完整工作区 ID：

```bash
orca-ide terminal create --worktree 'id:<FULL_WORKTREE_ID>' \
  --title 'DG implementation · sol/high' \
  --command 'codex --model gpt-6-sol -c model_reasoning_effort=high' --json
```

复杂设计/关键审查相应使用 `--model gpt-6-astra -c model_reasoning_effort=high`。这类自定义命令只指定模型和推理强度；它不保证继承 Orca 内置启动器的所有额外参数、账号封装或权限选项，须核对实际账号/运行环境，不借模型设置改变安全策略。

`worktree create --agent codex` 本身没有逐次 `--model` / `--effort` 参数，不能把 worker-start 的参数套到它上面。需要精确指定时，先创建 worktree，再使用显式 Codex 启动命令；确认已有默认 shell 的身份后，可复用该空闲 shell，避免留下重复 agent。

### 明确启用监督协作时

Orca 1.4.212 的 `orchestration worker-start` 支持逐 worker 指定；在 Run/Task 已建立、目标工作区及任务授权明确后使用：

```bash
orca-ide orchestration worker-start \
  --task '<TASK_ID>' --run '<RUN_ID>' \
  --worktree 'id:<FULL_WORKTREE_ID>' \
  --agent codex --model gpt-6-sol --effort high --json
```

审查任务改用 `--model gpt-6-astra --effort high`。`--effort` 必须与 `--model` 一起使用；两者均不能与 `--terminal` 复用参数组合。旧终端继续沿用其会话状态，不能宣称复用时自动换模；若实际 effort 高于 `high` 且无适用的用户确认，先调整到允许级别并核验，再派发任务。先读当前 CLI 的 orchestration 指南及 coordinator-loop / placement 引用；本节示例不意味着已启动多 agent。

### 生效核验与例外

1. 为每次任务记录 `task_id`、工作区、会话/Dispatch、请求与实际 model/effort、启动参数及验收证据路径。Orca worker 比较 `launch.requested` 与 `launch.effective`；缺字段或不一致时不猜测已生效。
2. 普通终端在发送工作任务前，核对 Codex 会话显示或会话元数据中的模型和推理强度。仅有命令行参数、工作区标题或配置文件，不能证明某个已运行会话使用了它们。
3. 一个任务阶段内保持配置稳定；变更时注明原因和发生位置。模型不可用时先报告事实，不静默回退到全局默认。任务复杂、失败重试或切换模型不构成提高 effort 上限的授权；高于 `high` 必须记录用户事先明确确认的任务范围与级别。不得为绕过此上限自动加开 worker。
4. 当前选择不替代资源预算、数据隔离或测试验收。明确的用户指定优先于此默认，并同步更新任务记录。

本次只验证 TOML、已安装 CLI 参数和本地模型目录所列支持项；没有启动新的 Codex 会话或进行模型调用。模型/effort 的运行时生效核验将在正式任务启动时完成。
