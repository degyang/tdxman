# CA-01—CA-13 概念分析能力回执

> 此为整改前评估。tdxman 1.1.3 已补齐部分接口并修复输入链，最新能力、实测与剩余边界见[整改交付回执](../achievements/concept_remediation_20261002.md)。原样本及结论保留其评估时点。

2026-10-02。结果：保留历史分析、分钟回放、监控、提醒与 AI 的支持目标；按真实输入和独立验收分包，不整体取消。**共享事实已经可复用，新增尾部分钟采集尚未实现。192 批/日仅是报价预算，不是完整刷新链路成本。**

本次仅做源码核对、有界只读样本及评估单回填，不实施 API 修复、采集、历史补算、服务切换或 Fundwise 业务改造。

原评估单：Fundwise `docs/northstar/tasks/UP-concept-analysis-remaining-api-assessment.md`。完整签名、字段有效数、来源路径、快照、实际条数与耗时在[可核对样本](../evidence/concept-capabilities-20261002/samples.json)；本文件中的拟议签名不是已经交付的接口。

## 版本与样本范围

- 候选源码：`feat/base-enriched-replica`，`be5ab15bb21955b6cfd09f6251d82e2ff650b45c`，基于 PR #2；版本名仍为 1.1.2，不能只凭版本号区分构建。
- tdxman 本仓库 `.venv` 安装版有 `read_board_daily`；**Fundwise `.venv` 实际是 main 源码 editable，仍没有此方法**。本次生产池公开日数据样本明确使用候选 `PYTHONPATH=src`，不是 Fundwise 服务可用性验收。正在运行的服务实际加载路径未检查。
- 日数据根为现有 `tdxman/data`，不复制数据库。8股为 `000001.SZ/000858.SZ/002304.SZ/300750.SZ/600036.SH/600519.SH/600900.SH/688981.SH`。
- 联网样本为休市期间返回的 2026-09-30 最近行情，不验证盘中延迟或未封口修订。标准 TDX 连接本次被服务器关闭；MAC 成功，两者不合并为一次成功验收。

## 支持边界修正

历史分析保留三种明确状态：①有目标日期的有效历史依据，按其计算；②用明确固定成分快照回看，展示 snapshot_id、采集日期与 `point_in_time=False`，不声称当日真实成分；③输入缺失导致某项不能计算，保留原因和覆盖。**缺复权因子、市值或其他评分输入，不补零、不自动重分配权重**；只有另立并公开的模型才能改变权重。

分钟回放保留：本地已有事实优先，源端按指定证券、日期、页数/条数与超时有界补取。显示请求日期、实际覆盖起止、缺口与未封口状态；未返回不自动判为停牌。股票单日分时的 close/price 不等同分钟 OHLC；多日回放每一天要有独立且有效的昨收。

监控、提醒与 AI 保留建设目标，分为独立工作包：先确定观察频率与范围、漏检可能、离线补查、提醒事件身份/去重及重新触发规则，再实施和验收。离线补查不宣称实时送达；AI 使用已发布事实、来源与缺失说明，不补造输入。本回执不改变这些任务的实施状态。

## 公开签名索引

签名省略 self，参数默认值和完整类型可从 samples.json 的 inspect 结果复核。

```python
# P1–P9：候选 DataPool；本地离线读取
# P1
DataPool.read_board_daily(*, start, end, kind=None,
    scope="all_stocks", fields=None, limit=100000, offset=0)
# P2
DataPool.read_daily(*, symbols=None, start=None, end=None, lookback=None,
    fields=None, adjust="none", adjustment_base=None)
# P3
DataPool.read_research_daily(*, symbols=None, start=None, end=None,
    lookback=None, fields=None)
# P4
DataPool.read_security_daily(*, symbols=None, start=None, end=None)
# P5
DataPool.read_limit_events(*, trade_date=None, start=None, end=None, symbols=None)
# P6
DataPool.read_index_daily(*, symbols=None, start=None, end=None, lookback=None, fields=None)
# P7
DataPool.read_security_info(*, symbols=None)
# P8
DataPool.read_fundamental_reports(*, symbols=None, start=None, end=None)
# P9
DataPool.read_trading_calendar(*, start=None, end=None)

# Q1–Q6：公开 MacClient；联网
# Q1
MacClient.get_board_list(board_type=BoardType.ALL, count=10000)
# Q2
MacClient.get_board_members(board_symbol, count=100000,
    sort_type=SortType.CHANGE_PCT, sort_order=SortOrder.DESC,
    fields=PresetField.COMMON, exclude_flags=None)
# Q3
MacClient.get_stock_quotes(stocks, fields=None)
# Q4
MacClient.get_stock_kline(market, code, period=Period.DAILY,
    start=0, count=800, times=1, adjust=Adjust.NONE)
# Q5
MacClient.get_tick_chart(market, code, date=None)
# Q6
MacClient.get_stock_quotes_list(category, start=0, count=80,
    sort_type=SortType.CHANGE_PCT, sort_order=SortOrder.DESC,
    exclude_flags=None, fields=None)

# S1–S3：公开标准 TdxClient；联网，start 必传
# S1
TdxClient.get_index_bars(market, code, category, start, count=800)
# S2
TdxClient.get_security_bars(market, code, category, start, count=800)
# S3
TdxClient.get_history_minute_time_data(market, code, date)
```

以上为签名记法；完整类型与默认枚举对象以 JSON 的 inspect 结果为准。Q3最多80只/次；Q2每页80；Q1每页150；Q4自动分页每协议页700；S1/S2单页至800。Q2无公开 start 参数，不把实时成员接口当 snapshot reader。

## 逐项回执

以下版本均采用上述候选基点；Fundwise 默认 SDK 能力不足的地方单列。计时为一次样本调用，排除连接发现，不外推全天延迟。所有离线调用网络请求数为0。没有验证的字段保持未知或 null，空的事件集合不等同接口失败。

| ID / 时间类型 | 公开入口与已验证样本 | 覆盖、实际成本与当前状态 | 最小处理、兼容影响与未验证项 |
|---|---|---|---|
| CA-01 实时目录/成分；历史成分参考 | Q1/Q2；种业880710 13成员、分散染料880706 20成员，另取水产品880903 12成员，与种业重叠300189.SZ；存量 snapshot 绑定可解释 | 三个Q2分别1次协议请求，0.0431/0.0340/0.0603秒；种业+水产品去重并集24股，报价预算1批。成员少于80，无重复，未触及320上限；无公开 total/快照完整性认证 | **没有按snapshot_id读取完整成员的公开入口**。建议有界只读reader，见下节；当前多页后页前置需局部修复，动态成分/EOF未验。内部SQL诊断不作为公开能力 |
| CA-02 实时成员行情 | Q2/Q3；种业+分散染料33股去重报价，字段投影close/pre_close/amount与服务器日期时间 | Q3一次协议请求、33行、0.0318秒；逐板块重复报价改成按已选完整成员去重。当前“全部GN去重并集”的规模未测，不能套用33/24股样本 | null/有效0分别保存；股本/市值、停牌、扩展位适用资产及80/81边界待验。不能把80股一批等同原子同一来源时点；不为每次GET重抓全部成员 |
| CA-03 历史日K/事件与日级输入 | P2/P3/P4/P5；2026-09-30八股：close/pre_close/pct_chg/amount/换手/量比有效8/8；股本和市值四项有效0/8；P4八行，P5当日零事件 | P3一调用8行0.1793秒；P4 0.1587秒、P5 0.1550秒。原表股本、市值及其source同为空，**不是reader把已有值丢掉** | 历史股本有效日期/可知日期链缺失，财务值不能直接倒填；来源修复与有依据计算分开，见下节。保留缺失因子，不补零或改权重；零事件样本不验涨停事件算法 |
| CA-04 历史概念日统计/六因子输入 | P1已有14列，含member_count/trading_member_count/valid_return_count/avg_return和成分绑定；无需遍历个股读取现存聚合 | 一日269GN约269行、一个P1；实测30日8070行0.5428秒。普通中位数、涨跌平/强弱家数、金额合计与换手/量比均值**未提供** | 单板块在公开完整成员reader补齐后，可有界读P3并派生，约成员1调用+日事实1调用；全GN新成员统计的并集规模/读取成本未测，不用平均收益冒充热度/风险，普通中位数不替换为上中位数 |
| CA-05 历史日排名矩阵 | P1，scope=all_stocks/kind=concept；7日1883行、30日8070行，每日269GN，全部类别ready，avg_return均有值 | 7日2026-09-21—09-30 0.2538秒；30日2026-08-19—09-30 0.5428秒；各一次本地读取、无分页、next_offset=None | 支持完整日涨幅排名矩阵。依据是2026-10-01采集的固定current_snapshot，非历史PIT；有效成分数仍逐格展示。跨次分页不同版未提供保证，优先一次有界调用 |
| CA-06 实时指数分钟/历史指数日K | P6 + S1推荐路径；本次S1连接失败，Q4备用验证000001.SH/399001.SZ/399006.SZ/000680.SH/880008.SH各240分钟与2日日K | 五指数各分钟1请求0.0354—0.0587秒、日K1请求0.0335—0.0356秒；分钟09-30 09:31—15:00，前日日K09-29；P6本地五指数7日35行0.0431秒 | 已有price/amount备用事实，MAC指数float_shares/vol不得进产品；标准五指数本轮未通过。前日基准可选取，盘中对齐/资金净额窗口/封口修订未验；叠加消费链仍须Fundwise验收 |
| CA-07 历史股日K/实时报价尾行 | P2 none/qfq，8股2026-08-03—09-30各42日，共336行；qfq基准query_end，attrs列出每股所用因子 | none 0.1795秒、qfq 0.2454秒，各1调用；本样本全部价格有效，但pre_close有效307/336、trading_status220/336，不能说全部日事实完备 | 保留缺日、因子和交易状态质量；quote仅provisional尾行，不能覆盖正式日K。10—2000日是请求范围需求，未验2000日/实际除权日及任意股票复权覆盖 |
| CA-08 实时/历史分钟回放 | Q4 GN800根窗口；Q5 600519.SH指定20260930和20260901各240条分时 | GN窗口2协议请求0.1011秒，09-24 13:41至09-30 15:00：09-28/29/30各240、09-24只有80，不能称09-24完整。两个股票分时各1请求0.0359/0.0397秒 | Q5只公开time/price/avg/vol/momentum，无日期、昨收、OHLC、amount；日期来自请求，返回头未公开，量口径未验。不能直接当完整分钟OHLC。最远历史/缺分钟/盘中封口未验；本地优先、有界补取，保存语义见下节 |
| CA-09 证券目录/搜索 | P7八股，symbol/code/market/name/active/listing_date/delisting_date/updated_at | 8行、1调用0.0417秒；参数symbols=None可一次读当前目录，源码未设置本接口总行数上限，完整目录数量本轮未测 | 支持当前目录索引，不等同历史名称。股票/ETF/指数和退出日期完整性待验；消费者首次读取明确限定资产/预算，不能拿updated_at当每日历史名称证据 |
| CA-10 历史股票/基准日收益及当日估计 | P2/P6/P4/P9，正常股票及5指数已有指定窗口样本 | 8股股票/5指数按窗口各一次调用，复用CA-07/06；收益与阈值由Fundwise算，无上游“异动分” | ST/市场路由/参考时点/是否含当日须明确。正常样本存在，缺指数/停牌负例本轮未补测；不得因为查到指数数据就宣布异动模块验收完成 |
| CA-11 历史关键价位输入 | P2复用CA-07 336行OHLCVA与换手/qfq | 已加载同一窗口不再调用源端；各指标所需前驱/ATR预热由Fundwise请求明确窗口 | 支持在有效输入上计算；11组价位/推荐/提醒未在上游实现，也不属本次交付。MA/ATR窗口缺失要显式失败或质量标记，2000日极限未验 |
| CA-12 历史财务参考 | P8八股2025-09-30—2026-09-30范围9条记录，liutong_guben/zong_guben有效9/9，published_at有效0/9 | 1调用0.0450秒；source=tdxman:finance，保留source_hash/fetched_at/payload_json；EPS/ROE等未在此合同中提供齐全 | **period_end实际由供应商updated_date映射，未证实为报告期**；published_at写为None。只能展示来源更新事实及缺口，不能承诺历史已知报告/有效股本。股本和财务金额字段需核验单位/更新日期映射，不能绕过缺口去填CA-03 |
| CA-13 日事实/衍生失效与修订 | P1/P2/P6/P4，按实际依赖日期/标的读取并比较业务值、null、质量和snapshot_id；现有发布保护可保证单次池读取 | 30日GN8070行可一次读，0.5428秒；8股42日336行0.1795秒；这是保守刷新成本，不是精确变更游标 | 没有向Fundwise交付按日期精确修订服务/分页token。基础增量replica不自动变成消费端修订API。冷历史依赖按请求窗重读或手动刷新；失败保留旧结果，完整成功响应的行消失单独处理 |

## 三个缺口的直接回答

### 完整成分快照读取

既有基础成分快照确实保存完整 members_json，种业13、分散染料20；两者绑定 `223785d26172ed3060464df137f0c42744643eb24fc6b98caa2d450a9e888c4a`。此结果来自只读内部诊断，Fundwise不能依赖内部表。

建议最小新增签名（**尚未实现**）：

```python
DataPool.read_board_members(*, snapshot_id, kind, board_ids,
                           limit=100000, offset=0)
```

返回板块身份、symbol、snapshot_id、membership_as_of/basis及分页实际覆盖；缺快照、错误kind、截断明确区分。证券名称应单独从P7当前目录取，不伪造快照时名称。先限板块数量/成员行数，在同一只读会话内读原快照；两小板块预计1本地调用、33条关系，网络0，此为预算而非实测新API耗时。无需全PIT平台、迁移历史成分或采全市场；历史重算继续用原绑定。

### 日级市值缺失

2026-09-30八股原始存储的total_share/float_share/total_mv/float_mv及source字段全空；公开P3也全空。最近42日窗口却有312/336条股本、市值，证明**不是全池永远不支持**，也不能靠“多数日期有值”修复指定日缺口。

源码当前当日writer接收来源股本，历史snapshot enrichment仅在观测日期相等时可用；P3不按财报期隐式填补。9条财务来源记录虽然有股本，其公告日为空，且updated_date映射到period_end不保证财报期/股本生效日。缺少有效时点是独立输入缺口，不能用当前股本倒填历史。

修复候选先明确当日quote字段投影、单位和来源时点，在有同日可靠股本时计算 `raw_close × shares` 并保存依赖；历史部分需可靠有效股本区间/公司行为依据，再按受影响范围计算。现价对应当前市值展示与历史同日市值评分分别验收。本次不回填、不补0、不重分配六因子权重。

### 历史分钟覆盖与保存版本

GN800条只证实09-28/29/30完整样本和09-24下午部分；不能从count=800推导最远日期。股票Q5在09-01和09-30有240条响应，但公开结果只有HH:MM，不提供来源日期/昨收，需保留请求日期并验对应来源，不能把它等同分钟OHLCVA或全部历史可读。

Fundwise `BoardStore.index_minutes` 主键是source/symbol/stamp，`INSERT OR REPLACE`保存的是**目前最新采到的修订值**。普通概念分钟不带逐次采集版本；旧发布receipt/fingerprint不能还原被覆盖的分钟。880008 reference_versions另存不可变payload，只对其绑定的参考事实生效，不能推广到全部概念。当前未返回的旧分钟不会自动删除，因此“已存条数”不等于最新源端完整性。

分钟回放可以继续支持，但默认标“最新采集修订值”；若要回放“当时看见的版本”，另包保存采集版本/变更记录并验收。增量采集必须保留未封口及有限重叠修订窗口，单纯只取新增时间戳会漏源端修订；具体窗口待盘中证据决定。

## 完整刷新成本：源码推导，不是全天实测

Fundwise `TdxBoardSource.quotes()`每轮目录count=600，再按80取全部269GN报价；`minutes()`逐指数 `get_stock_kline(...start=0,count=240)`。BoardService过滤目标日后落共享库，但采集请求仍是整日窗口，未传last_stamp或新增尾部count。

以269GN、完整交易日48个5分钟观察槽、每槽一次成功刷新、目录仍2页、无重试为条件：

| 项目 | 每轮协议请求 | 48轮/日 |
|---|---:|---:|
| GN目录 | 2 | 96 |
| GN报价 | ceil(269/80)=4 | 192 |
| GN分钟 | 269 | 12912 |
| 880008报价与分钟，未跨轮复用时 | 最多2 | 最多96 |
| 合计 | 275—277 | 13200—13296 |

15分钟16轮同条件为4400—4432次。GN分钟返回行数上限48×269×240=3098880条，尚不含参考指数。参考事实跨模块同demand复用会降低对应成本，不消除269GN整日分钟请求。连接发现、重连、重试、手动刷新、成分、历史补取、写入和业务计算另计。实际执行频率、跳过任务、服务配置与盘中响应尺寸未测，不能把该模型当实际日流量账单。

单次MAC240分钟样本约0.035—0.059秒，只能说明本次休市小样本；不能把它线性外推为稳定盘中SLA。下一包要量测请求数、源字节数/返回行数、连接/失败、实际覆盖、修订/漏检和处理耗时，再比较整日重复窗口与增量窗口。

## 独立工作包与验收门槛

1. **版本/公开事实包**：对齐Fundwise SDK构建；修复多页反序/末页超取，说明EOF/时间/单位；补最小完整快照reader。验收count=1/80/81、重复/动态分页/失败及原snapshot绑定，不改变业务公式。
2. **日输入包**：指定日期股本、市值、财务日期/单位定点检查。只在可靠历史依据上计算；覆盖缺市值、缺因子和已知0，验证无自动补零/权重重分配。
3. **分钟采集/回放包**：本地覆盖读取、有界源端补取、新增尾部及重叠修订；明确最新值或采集版本。验收跨午休/隔夜、未封口、源修订、缺页、错误日期及恢复，不默认建全历史分钟库。
4. **监控包**：明确5/15分钟或其他观察频率、订阅范围、离线补查；量测间隔内事件漏检，并区分观察到/可推断/无法判断。
5. **提醒包**：定义事件身份、规则版本、去重、冷却及恢复后重新触发，验证离线补查不重复通知。
6. **AI包**：基于已发布事实与覆盖说明解读，结果带输入依据；模型输出不补造缺失指标。逐包实施验收，不以本次文档完成宣称已上线。

测试未执行的边界仍列为未验证：盘中封口/修订、动态分页、81条超取的当前实返、全GN成员并集成本、缺指数/停牌负例、财务时点/单位、历史分钟最远范围和在运行服务的构建。本轮没有据此取消功能，也没有自动开始上述实现。

## 复核入口

在候选 worktree 使用现有解释器，显式指定源码和已有数据根；所有数据库连接均为只读。本次联网主探针失败的标准路径由第二个有界 MAC 探针补充，失败记录保留：

```bash
PYTHONPATH=src /mnt/d/Workstation/Services/tdxman/.venv/bin/python \
  docs/evidence/concept-capabilities-20261002/probe.py \
  --root /mnt/d/Workstation/Services/tdxman/data \
  --output /tmp/tdxman-ca-probe.json --online
PYTHONPATH=src /mnt/d/Workstation/Services/tdxman/.venv/bin/python \
  docs/evidence/concept-capabilities-20261002/minute_probe.py
```

第二个探针读取并补充前一个固定 `/tmp` 输出，仅返回小样本/覆盖摘要；不得作为采集任务或刷新调度器。samples.json另外记录Fundwise `.venv`的实际模块路径/哈希及成本推导，assessment.json记录CA行数、矩阵发布状态、成本算术和原评估单保留检查。证据不取代后续局部实现或盘中验收。
