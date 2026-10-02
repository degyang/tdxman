# Fundwise 联调环境升级回执

2026-10-02；接续[1.1.3整改交付](concept_remediation_20261002.md)。用户明确选择“升级并重启当前服务”，已执行。此前“未部署”描述仅适用于升级前交付时点。

## 当前状态

- Fundwise `.venv` 原先 editable 加载 tdxman 主目录1.1.2，缺少 `read_board_daily` 和 `read_board_members`，确实阻挡新接口联调。
- 当前已安装提交 `9f93a0c` 构建的1.1.3 wheel，实际加载路径在 Fundwise `.venv/lib/python3.11/site-packages`，两个方法都存在。仅替换 tdxman 包，未升级其他依赖、修改 Fundwise 业务源码、依赖配置或锁文件。
- 用户指定8765服务以原参数正常停止并重启，新PID为982781，保留原环境与数据根。tdxman自己的日更 `.venv` 也安装同一wheel，后续日更使用已修复股本输入链；本次没有执行日更或写生产数据库。
- PR #3仍待合并；安装明确构建的SDK即可联调，合并不是本次联调前置条件。

## 实际验收

五个HTTP请求均200：健康、7日概念日轮动、概念目录、种业880710成员详情、600519.SH股票历史日线。目录269项，种业13个成员全部有效，日收益、金额和成员绑定均返回。

Fundwise现有成员详情/日轮动测试在新环境运行：**8项通过**。其现有 `read_curve` 直接调用新SDK读取600519.SH 2026-09-01历史分时，返回240条、已验证日期及当日昨收；只取源输入，没有发布消费端数据。

[HTTP结果摘要](../evidence/concept-integration-20261002/http-summary.json)、[环境及构建](../evidence/concept-integration-20261002/deployment.json)、[成员字段检查](../evidence/concept-integration-20261002/missing-inputs.json)和同目录测试/分钟证据可核对。完整运行回执及升级前SDK元数据在 `/home/ubuntu/tdxman-ca-113-integration/`。

## 历史缺值的具体影响

2026-09-30种业13股的总/流通股本、市值仍缺失；已有 `vol_ratio` 来源是 `tdxman:quote`，**并非** Fundwise要求的 `derived:five_valid_volumes` 五个有效交易量算法。Fundwise因此返回缺cap/volume因子，leaderScore、leader和heatScore为空。这是计算输入/口径尚未满足，不能把接口200说成这些评分已有效。

目录、轮动、成员详情、股票日线和分时接口可以继续联调；完整评分需要另行取得有有效日期的股本并按明确五日量算法提供输入。不能使用最新财务股本倒填历史，也不能把来源报价量比改标签冒充五日派生量比。SDK升级修复后续股本写入，不自动重写此前历史；本次没有伪造或补零。

## 启动与依赖注意

当前服务使用 `.venv/bin/python -m fundwise.cli web serve ...`，实际已经运行新SDK。Fundwise现有 `pyproject.toml` 仍指向旧主目录editable；普通 `uv sync`/会同步的 `uv run` 会恢复该旧绑定。在其依赖配置切换之前，沿用直接 `.venv/bin/python` 或 `uv run --no-sync`；若明确需要同步其他依赖，应随后重新安装本wheel并重启服务。

联调构建可显式运行（已验证其SDK与Fundwise导入）：

```sh
uv run --no-sync --with /home/ubuntu/tdxman-ca-113-build/tdxman-1.1.3-py3-none-any.whl fundwise --help
```

这条记录不修改 Fundwise负责的项目依赖契约；其同步整改时应将依赖绑定更新到本构建或正式合并后的对应源码，避免再次退回旧SDK。升级前安装记录和旧tdxman1.1.2wheel路径已保留，可按原来源回退；本次未触碰生产数据，无数据恢复操作。
