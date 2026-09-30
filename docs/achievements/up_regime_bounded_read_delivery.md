# UP-RG-MEM：有界事件成交额读取交付回执

日期：2026-09-25。承接仓库：tdxman。需求卡：Fundwise `docs/northstar/tasks/UP-regime-bounded-read.md`。

## 交付结论

P0 的字段裁剪前推、事件成交额关联、有界读取和指定资源验收已完成，可交给 Fundwise 做消费侧接入验收。
正式五年 1,213 会话、82,772 条目标事件全部对账一致，总进程峰值 RSS **289.54 MiB < 2 GiB**。
含预热的 1,296 会话共 89,096 条目标事件，峰值 288.61 MiB。

这是读取接口的专项验收通过，不代表全仓发布门禁全部通过：当前快照有 605 个 stale 会话；
全仓单测仍有 6 项失败，详见下文。没有重算生产数据，也没有安装到 Fundwise 或执行页面链路验收。

## 实现、接口与契约

`DataPool.read_daily/read_research_daily(*, symbols=None, start=None, end=None, lookback=None, fields=None)`
签名未变。字段在实际 SQL 物化前投影，日期、证券条件前推；显式证券范围还裁剪 Parquet 文件列表。
仅需要事实字段时才关联相应日期/证券的事实，金额三列读取不生成 ST、参考价或来源字符串。
扫描与必要事实关联分别采用 DuckDB `512MB`、2 线程及自动清理的临时目录。
重复键仍在 lookback 之前检查；价格、成交量和金额合法性保留 SQL 校验。
`code` 保持字符串，包括单证券文件裁剪后的场景。DataFrame 超过 500,000 行报 `DAILY_TOO_LARGE`，不会截断或返回空表。

新增接口（安装包反射确认）：

```python
DataPool.iter_limit_events_with_amount(
    *, start, end, symbols=None, fields=None,
    close_limit_up=None, min_consecutive_up=None,
    batch_days=7, max_rows=25_000,
    memory_limit="512MB", threads=2, temp_directory=None,
)
```

返回支持 `with`、迭代、`close()`、`completed` 的对象。默认按 7 个自然日查询，每批上限 25,000 条；
SQL 仅多取一行判断越界，过大报 `LIMIT_TOO_LARGE`，需缩短日期批量或证券范围。
`batch_days` 允许 1—31，`max_rows` 允许 1—100,000，`threads` 允许 1—8。
这些可配置范围不等于已经逐一资源验收；本次实测使用默认值。

`start/end` 必填且含边界。证券如 `000001.SZ`；默认保留未知连板和 stale 事件。
只有显式 `close_limit_up=True, min_consecutive_up=1` 才筛选已知且至少一板的收盘涨停事件。
`fields` 保留请求顺序；默认字段含涨跌停/触板标记、限价、连板信息及
`symbol/trade_date/amount/batch_id/rule_version/computed_at/published_at/stale/stale_reason`，
完整列表见 [API 文档第 9 节](../design/aspool_api.md#9-已发布事件与当日成交额的有界读取)。

一条已发布事件对应一条记录；`amount` 只取同证券同交易日已存日线，单位人民币元。
没有同日日线、没有 amount 列、amount 值缺失或跨文件 schema 不一致，均保留 null 及事件键；不补零、不跨日补值、不删除事件。
日线重复键报 `DAILY_INVALID`，事件重复键报 `LIMIT_INVALID`。
每批 `attrs` 为 `contract_version=1, amount_unit='CNY'`。

发布批次、发布时间、规则/计算版本及 stale 状态在开始、每批前后和结束复核，空事件的已发布日期也参与。
变化时报 `LIMIT_REVISION_CHANGED`，消费者应丢弃本轮缓存并重试。
每次 yield 前已关闭查询连接、注册表和锁；正常结束、提前 close 和异常均清理独立 spill 目录。
`temp_directory` 是该目录的可写父目录。DuckDB 内存配置不覆盖 Pandas/Arrow；总 RSS 另行测量。
普通日线接口没有跨调用版本一致性保证。

最小分批落盘示例：

```python
from pathlib import Path
import shutil
import tempfile
from aspool import DataPool

staging = Path(tempfile.mkdtemp(prefix="fundwise-regime-"))
try:
    with DataPool("~/.aspool").iter_limit_events_with_amount(
        start="2021-05-27", end="2026-09-24",
        close_limit_up=True, min_consecutive_up=1,
    ) as batches:
        for i, frame in enumerate(batches):
            frame.to_parquet(staging / f"part-{i:05d}.parquet", index=False)
            del frame
        assert batches.completed
except BaseException:
    shutil.rmtree(staging)
    raise
# Fundwise validates stale policy and publishes this completed cache directory.
```

## 当前生产池只读验收

读取 `/home/ubuntu/.aspool`，日期范围 2021-05-27—2026-09-24，确认为 1,296 个已发布会话，
规则均为 `cn-a-share-limit-v8`。本轮 **605 会话 stale**，原因是“日线字段变化，等待补齐和重算”；
一年、两年窗口分别有 243、487 个 stale 会话。保留并逐行对账了这些标记，未把 stale 当成无事件。
发布/失效清单前后指纹一致；这不替代对原始日线的不可变快照管理，也不宣称数据已重新发布。

各区间在独立子进程顺序运行：虚拟内存上限 5 GiB，20 ms 采样并在 RSS 超 2 GiB 时中止，
另记录 Linux `ru_maxrss` 最高水位。下表 RSS 取进程最高水位，包含 SQL、关联、Pandas/Arrow、逐批缓存写入和正确性对账。
DuckDB `512MB` 是十进制参数（约 488.2 MiB），不是整个进程上限；`OPENBLAS_NUM_THREADS=1, OMP_NUM_THREADS=1`。
查询线程设为 2，进程还存在元数据连接和底层库线程；未把进程总线程数记作 2。

| 区间（结束均为 2026-09-24） | 会话 | 对账/输出行 | 批数 / 最大批行 | 秒 | RSS MiB | 缓存 MiB |
|---|---:|---:|---:|---:|---:|---:|
| 一年：2025-09-24 起 | 243 | 19,041 | 52 / 628 | 13.07 | 234.52 | 1.00 |
| 两年：2024-09-24 起 | 487 | 41,413 | 102 / 1,309 | 25.86 | 288.13 | 2.04 |
| 正式五年：2021-09-24 起 | 1,213 | 82,772 | 258 / 1,004 | 62.68 | 289.54 | 4.61 |
| 含预热全段：2021-05-27 起 | 1,296 | 89,096 | 278 / 1,155 | 66.66 | 288.61 | 4.97 |
| 全段、不筛选事件 | 1,296 | 182,504 | 278 / 5,078 | 91.62 | 610.88 | 6.96 |

全部区间 swap、DuckDB spill 峰值均为 0；临时磁盘仅为表中缓存文件。
所有旧公开事件字段在每个日期窗口精确对账（按日期/证券/批次，忽略 Pandas dtype 表示差异），无增减行。
每个长窗口在首、中、尾批抽样核对 30 个成交额，与已有日线读取一致；生产事件的缺成交额计数为 0，
缺文件、缺列、缺值及混合 schema 由临时池测试覆盖。不加筛选的全段进一步核对了 182,504 条事件。
一年至五年输出增长约 4.35 倍，RSS 从 234.52 到 289.54 MiB，没有按整段历史行数线性累积。

预热后固定读取 2026-09-01—09-14 共 10 轮，每轮 574 行、2 批、每批最多 314 行，
均逐批写缓存、释放并对账。下面为每轮 20 ms 采样 RSS（MiB）：

| 轮次 | 起点 | 峰值 | 结束 | 秒 |
|---:|---:|---:|---:|---:|
| 1 | 160.39 | 202.16 | 174.22 | 1.039 |
| 2 | 174.22 | 210.78 | 178.38 | 1.065 |
| 3 | 178.38 | 212.86 | 180.60 | 1.031 |
| 4 | 180.60 | 214.80 | 180.39 | 1.066 |
| 5 | 180.39 | 214.52 | 181.02 | 1.050 |
| 6 | 181.02 | 216.29 | 181.36 | 1.029 |
| 7 | 181.36 | 213.93 | 181.77 | 1.026 |
| 8 | 181.77 | 215.75 | 182.15 | 1.038 |
| 9 | 182.15 | 215.96 | 181.88 | 1.020 |
| 10 | 181.88 | 216.59 | 181.82 | 1.061 |

每轮结束文件描述符均回到 4、线程回到 21；swap/spill 均为 0。
结束 RSS 从第一轮 174.22 MiB 上升后，在后五轮约 181.36—182.15 MiB 波动，末轮 181.82 MiB；
表现为分配器/库缓存保留后的平台，未观察到持续线性增长。十轮观察不能证明任意运行时长绝无泄漏。
异常、提前退出、超行数、空发布日期重发和最后一批后的重发检测均由定向测试覆盖，并检查临时目录释放及数据库可再次打开。

## 复现与证据

全部正式证据为 [v8-*.json 与对应来源清单](../evidence/up-regime-bounded-read)，旧无 v8 前缀的文件仅为历史记录。
脚本：[benchmark_regime_bounded_read.py](../../scripts/benchmark_regime_bounded_read.py)。复现五年验收：

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONPATH=src \
  python scripts/benchmark_regime_bounded_read.py \
  --start 2021-09-24 --end 2026-09-24 --verify --output /tmp/five-year.json
```

一年/两年起点分别改为 2025-09-24、2024-09-24；含预热全段改为 2021-05-27。
加 `--all-events` 核对不筛选事件；连续检查使用 `--start 2026-09-01 --end 2026-09-14 --warmup --repeat 10 --verify`。
每次保存来源清单、清单 SHA256、代码 SHA256、Git 提交、运行库版本、各轮 RSS/FD/线程/磁盘数据。
代码 SHA256 按 `pyproject.toml`，再按路径排序的 `src/**/*.py`，逐个拼接相对路径、NUL、文件内容计算。

本次代码提交：`016e84183bed195bc2e178df6ffad271eba89811`。
代码 SHA256：`7d6d201987a6609453760d63f5c2a36eae456f32ea10235a24582ab52a265f0c`。
全段来源指纹：`8374b52e66a85c06f41a73e33ecf86ba10e5eb5e36f9b2185be54c8e8c6764b3`。
环境：Python 3.12.14 / DuckDB 1.5.5 / Pandas 3.0.5 / PyArrow 25.0.1。

## 测试与交付边界

定向回归 **163 passed**，覆盖新批读、旧日线、限价事件、参考价和证券编码兼容性；无排除项。
相关修改通过 Ruff 与 `git diff --check`。原始日志见 [targeted-tests.log](../evidence/up-regime-bounded-read/targeted-tests.log)。

全仓单测为 **529 passed、6 failed、2 skipped、17 subtests passed**，失败明细保存在
[full-unit-tests.log](../evidence/up-regime-bounded-read/full-unit-tests.log)：

- 合并逻辑 2 项：已有字段失效处理新增 null/source 字段，旧预期未包含这些字段。
- 同步流程 1 项：测试仅模拟在线同步，未提供新增补齐流程要求的交易日历。
- ST 限价相关 3 项（含 1 个子测试）：当前规则返回 10% 限幅，旧测试预期仍为 5%。本次未改限价算法。

这些失败所在的合并、同步和限价规则行为来自工作区已有改动，未在本 P0 内重新裁定其业务契约。
故本回执结论限于读取专项交付，不能据此宣称整个 tdxman 版本已通过全仓发布验收。

## 安装包与提交

版本 `tdxman==1.1.1`，独立分支 `delivery/up-rg-mem-v1.1.1`。
分支保留了工作区已有 v8 及相关上游源码依赖，属于当前包的源码快照，不能将整个分支差异解释为纯 P0 补丁。
未把本机 `settings/config.yaml` 和 `SKILL.md` 改动纳入包快照；主工作区及其索引保持原有工作方式，未提交到主分支或推送。

安装包：[tdxman-1.1.1-py3-none-any.whl](../../dist/up-regime-bounded-read/tdxman-1.1.1-py3-none-any.whl)。
wheel SHA256：`ba2be74fdd53712d7f6db51f42a043b65458f46b41a7b07543504a67ac157a56`。
独立环境安装后反射确认接口、版本，逐个核对包内 153 个 Python 源文件与代码提交内容一致，并只读消费末日数据。
安装验证记录：[wheel-smoke.json](../evidence/up-regime-bounded-read/wheel-smoke.json)。

同目录提供源码归档、验收依赖版本、`SHA256SUMS` 与 `release-manifest.json`；后者关联最终回执提交、代码提交及各工件校验值。
源码归档包含最终回执和证据。可以使用：

```sh
python -m pip install -r dist/up-regime-bounded-read/acceptance-requirements.txt \
  dist/up-regime-bounded-read/tdxman-1.1.1-py3-none-any.whl
```

Fundwise 仍需将主线改为逐批消费、只缓存必要事件、按批次及 stale 失效、按日计算，并限制市场/主线重任务并发。
本接口只减少读取工作集；不会替代 Fundwise 的缓存发布和 stale 使用策略。
接入后由 Fundwise 验证真实页面链路；上游回执路径为 `tdxman/docs/up_regime_bounded_read_delivery.md`。
