# Tick Stock Panel 数据源对接：需求、设计与落地契约

日期：2026-09-21。当前状态以[恢复审查与验收记录](../tick-stock-panel-recovery-audit.md)为准。本文下方保留原需求及历史实现记录；此前 P1–P4 已完成的描述不能作为当前版本的全量验收结论。恢复版本通过隔离验证后，已按用户授权更新本项目正式源码，现有软链接将加载此版本；服务由用户启动。财务、复权因子及策略能力不在范围内。

依据：tdxman 当前工作区（HEAD `011ff38`，包含已有未提交实现）及 Tick Stock Panel 当前插件调用链。本文不是已发布 API 声明；所有新增模块、类和方法均为目标设计。已有方法以源码为准。

## 1. 需求与边界

使用 **tdxman 项目整体能力**——标准 `TdxClient`、`MacClient`、对应异步客户端及 `aspool.DataPool`——向 Tick Stock Panel 提供统一行情 Provider。不得将范围误限为标准 `TdxClient`，也不要求把 MAC 协议塞入现有标准 TCP 客户端。

范围包括：股票、ETF、指数日K；分钟K；全市场实时行情；指定指数实时行情；五档盘口；基础证券目录；当日全量分钟修复及最近分钟增量编排；可用性、能力发现、试拉和错误诊断。

非目标：财务五表、历史股本财务接口、除权/复权因子、指数成分权重、交易执行、策略计算。所有价格请求显式使用不复权。现有 SDK 的复权能力不因此进入 Provider 能力声明。基础证券名称、分类、上市日期等已有元数据允许读取，但不为补齐它们调用被排除的财务接口。

“能覆盖”表示现有取数基础可组合实现目标；不表示当前已经具备完整 Provider。服务器历史保留长度、指数成交量单位、北交所/ETF目录完整性及完整报价日期必须通过验收，不以方法存在作为通过依据。

## 2. TickFlow消费项目协议：先明确必须满足什么

本文“TickFlow接口协议”指 **Tick Stock Panel 当前服务层消费的数据源插件协议**，不是要求仿制TickFlow云端HTTP地址、鉴权、SDK所有方法或二进制线协议。tdxman内部标准/MAC传输协议保持原样，最终输出满足本节即可接入。后续章节逐项解释如何实现。

协议证据位于消费仓库：`docs/plugin-development.md`、`app/data_providers/custom/loader.py`、`app/services/kline_sync.py`、`quote_service.py`、`depth_service.py`、`instrument_sync.py`。路径以该仓库backend为app根，不能导入tdxman。下表明确区分“消费端现有要求”与本文后续提出的更严格实现策略。

### 2.1 注册、方法及调用方式

| 契约ID | 方法及现有默认值 | 返回 | 消费端语义 |
| --- | --- | --- | --- |
| TF01 | `get_daily(symbols,start_time,end_time,asset_type='stock',on_chunk_done=None)` | Polars DataFrame | 日K按daily路由；参数支持None日期边界 |
| TF02 | `iter_daily(...)`，参数同daily | Iterator[DataFrame] | 可选；大同步优先用迭代，每批与daily同结构，异常终止不作为成功结束 |
| TF03 | `get_minute(symbols,start_time,end_time,asset_type='stock',freq='1m',on_chunk_done=None)` | Polars DataFrame | 调用方按关键字传时间、资产、freq、回调，必须接受这些名字 |
| TF04 | `get_intraday_batch(symbols,count=300,asset_type='stock')` | Polars DataFrame | full_minute修复轮实际只调用method(symbols)，因此其他参数必须可省略 |
| TF05 | `get_intraday_latest(symbols=None,count=3)` | Polars DataFrame | 可选；实际调用method(count=count)，无symbols代表该源负责的全市场范围 |
| TF06 | `get_realtime()` | list[dict] | 无参数，全市场快照；不是单股报价，也不是DataFrame |
| TF07 | `get_realtime_indices(symbols)` | list[dict]或None | 可选，按指定指数补充；字段同实时 |
| TF08 | `get_depth_batch(symbols)` | dict[symbol,dict] | 五档独立路由，不跨源回退 |
| TF09 | `get_instruments(asset_type='stock')` | list[dict] | 可选，当前股票维表同步实际传'stock'，嵌套ext随后展平 |
| TF10 | `test_dataset(dataset,symbols=None)` | dict | 设置页试拉，返回provider/dataset/rows/columns/preview及可选error |
| TF11 | `close()`、check返回`(bool,str)` | None、tuple | 重载先close旧provider，check失败只禁用本插件 |

不在本期实现TF财务或复权方法，也不把它们列入datasets。`daily/minute/realtime/depth5/full_minute` 的注册声明必须与 `config.datasets` 一致；方法可被调用不意味着已经声明能力。消费端当前只有数据集级能力，不支持每资产独立开关。适配器内部覆盖明细不能自动改变该事实。

### 2.2 各返回结构的强制字段、类型与口径

| 返回结构 | 必需列/键 | 类型、单位和空值 |
| --- | --- | --- |
| 日K | symbol,date,open,high,low,close,volume,amount | symbol字符串后缀格式；date为pl.Date；六数值列Float64，OHLC不复权；volume手、amount元 |
| 分钟K，包括TF04/05 | symbol,datetime,open,high,low,close,volume,amount | datetime为上海naive `Datetime(us)`；数值Float64，volume手、amount元；无可靠amount必须保留列并置null |
| 实时 | symbol,last_price,prev_close,open,high,low,volume | symbol字符串；价格数值；volume为手；不可把缺失字段默默删除后作为合格报价 |
| 实时推荐字段 | amount,change_pct,change_amount,timestamp | amount元，change_pct小数，change_amount元，timestamp为Unix毫秒；缺失不是0 |
| 实时可选字段 | name,amplitude,turnover_rate,session | name字符串，amplitude/turnover_rate入口小数；缺失null，不使用启发式单位猜测 |
| 五档单证券记录 | bid_prices,bid_volumes,ask_prices,ask_volumes,timestamp | 买卖价格和数量分别五项，一至五档；数量手、价格元，timestamp毫秒 |
| 标的 | symbol,name,code,exchange,region,type,ext | code字符串保留前导零；exchange SH/SZ/BJ；ext是字典而非顶层扩展列 |
| 标的ext | listing_date,total_shares,float_shares,tick_size,limit_up,limit_down | 股本为股；价格/最小价差按价格单位；未知None，上市日ISO日期或可解析日期 |

日线可选quote_ts是行情更新时间，不是交易日午夜。指数OHLC是点位，不用人民币股价解释。日线/实时的volume消费端统一按“手”使用；如果上游指数只声明原始单位，必须验证后才能适配。

稳定空表包含与正常表相同的必需列及dtype，是本设计的加强约束；现有消费代码有时接受无列空表，不能因此故意输出不稳定schema。未知可选数据保持null，NaN/Infinity不应穿过JSON预览接口。

### 2.3 参数、日期和交易语义

- 证券统一`600519.SH`，ETF、指数也采用后缀；完整市场不可丢失。asset_type为stock/etf/index，不靠裸代码推断。
- Provider输出原始价格，由消费端另行复权；本期不会提供复权因子。不能以“SDK默认前复权”替代显式原始价。
- 分钟datetime为北京时间墙钟，不能输出UTC naive。消费端虽有时间守卫，但适配器必须自己正确转换，不依赖守卫猜时区。
- `on_chunk_done`由Provider以(cur,total)调用；消费端包装后才补第三个seg_label，不把三参回调泄漏到Provider。
- full_minute只负责当日1m，不等于一般历史minute。TF05存在时消费端会选增量路径；如果逐证券开销不合格，应不暴露该方法，使用已有仅修复轮降级路径。
- “返回数据非空”不证明区间完整。本文实现额外规定包含边界、排序去重、截断诊断；这些是为补齐消费接口未携带覆盖元数据的限制。

### 2.4 错误和空返回影响下游的实际行为

| 场景 | 现有消费端行为 | 实现必须避免 |
| --- | --- | --- |
| minute正常空表 | 视为成功，无TickFlow回退 | 把网络异常吞成空表，让回退失效 |
| minute抛异常/时间不合法 | 尝试TickFlow回退，是否可用另受原生权限检查 | 承诺失败后必有数据 |
| realtime失败 | Provider约定[]加warning；消费服务也捕获异常 | 把部分市场无说明地当全市场成功快照 |
| indices失败None | 保留上轮指数缓存 | 错用[]使有效缓存被替换为空 |
| indices成功[] | 成功但无记录 | 与失败混淆 |
| depth单批失败 | 上层捕获并继续其他批，不跨源回退 | 内部静默切换其他数据源 |
| full_minute异常 | 转为空轮；连续空轮由刷新服务处理 | 把昨日分钟回传为今日成功轮 |
| TF05不存在 | 降级仅修复轮 | 提供无效空实现，反复进入错误增量路径 |
| 日线迭代异常 | 同步不得当作正常流结束提交 | 部分失败被静默隐藏 |

全量分钟消费端统计的requests可能仅为一次Provider调用，不等于tdxman内部实际网络请求数；适配器另记真实请求数用于性能验收。depth消费端可能按100只分片，标准报价上限80，因此Provider仍必须自行按底层上限二次分片。

### 2.5 指数覆盖与最小结构示例

当前界面核心指数是`000001.SH`上证、`399001.SZ`深成、`399006.SZ`创业板、`000680.SH`科创综指，不能把科创50当科创综指。消费端还会传入基准指数及用户监控指数，不得只硬编码四只并丢弃其余请求。未知标的应按明确失败语义处理。

实时一条记录的结构示例（仅演示字段，不是真实行情）：

```python
{
    "symbol": "600519.SH", "name": "贵州茅台",
    "last_price": 101.0, "prev_close": 100.0,
    "open": 100.0, "high": 102.0, "low": 99.0,
    "volume": 1000.0, "amount": 10100000.0,
    "change_amount": 1.0, "change_pct": 0.01,
    "turnover_rate": None, "amplitude": 0.03,
    "timestamp": None,
}
```

timestamp=None仅演示缺失表达，不能作为盘后时效验收的通过样本。不能输出`change_pct=1`表示1%，不能输出volume=100000股让下游再乘100。

标的需嵌套ext，例如`{"symbol":"600519.SH","name":"贵州茅台","code":"600519","exchange":"SH","region":"CN","type":"stock","ext":{"listing_date":None,"total_shares":None,"float_shares":None,"tick_size":None,"limit_up":None,"limit_down":None}}`。把股本只放顶层会被现有flatten忽略。

试拉最小结构为`{"provider":"tdxman","dataset":"daily","rows":0,"columns":["symbol","date","open","high","low","close","volume","amount"],"preview":[]}`；rows统计整个结果，不是preview长度。失败增加error，不能以rows=0假装成功。

## 3. 当前实现与复用位置

| 能力 | 当前可复用入口 | 尚需完成 |
| --- | --- | --- |
| 股票日线 | `DataPool.read_daily/read_research_daily`、标准 `get_security_bars`、MAC `get_stock_kline` | 单位转换、资产路由、区间分页 |
| ETF日线 | `DataPool.read_etf_daily`、MAC K线 | ETF目录覆盖、在线样本验证 |
| 指数/行业板块日线 | `DataPool.read_index_daily`、`get_index_bars`、MAC K线 | 板块走MAC；指数成交量口径核实 |
| 分钟K | 标准股票/指数K线、MAC K线 | 频率、时间范围、历史覆盖、量额验证 |
| 实时报价 | 标准 `get_security_quotes`、MAC `get_stock_quotes/get_stock_quotes_list` | 全市场集合、分页完整性、时间封装 |
| 五档 | 标准 SecurityQuote、MAC `PresetField.HANDICAP` | 五档数组、单位、时间与缺档处理 |
| 目录 | 标准证券目录、MAC分类目录、aspool ETF/指数目录 | 市场/资产分类、来源与覆盖状态 |
| 全量分钟 | 逐证券分钟K | 有界批量调度、部分失败与重试 |

关键源码：

- [标准客户端](../src/tdxman/client.py)、[MAC客户端](../src/tdxman/mac/client.py)。
- [标准K线模型](../src/tdxman/models/bar.py)、[报价模型](../src/tdxman/models/quote.py)、[MAC模型](../src/tdxman/mac/models.py)。
- [MAC K线解析](../src/tdxman/mac/commands/symbol_bar.py)、[字段预设](../src/tdxman/codec/bitmap.py)。
- [DataPool](../src/aspool/pool.py)、[ETF读取](../src/aspool/etf_api.py)、[指数读取](../src/aspool/index_api.py)、[日线字段契约](../src/aspool/api_contract.py)。

aspool 已有内部分钟同步不等于已有公开分钟读取接口；本期分钟查询直接复用客户端，不绕过 DataPool 读取内部 Parquet。

## 4. 架构与依赖落点

```text
Tick Stock Panel services
  -> backend/app/plugins/tdxman（指向tdxman受管目录的软链接）
  -> tdxman.integrations.tick_stock_panel.plugin（插件清单、探活与薄入口）
  -> tdxman.adapters.tick_stock_panel.TdxmanProvider（新增，可选依赖）
      -> 证券/资产识别、字段归一、分页、覆盖诊断
      -> aspool.DataPool：显式本地模式下读取日线
      -> MacClient/AsyncMacClient：MAC行情、板块、分类目录、分钟K
      -> TdxClient/AsyncTdxClient：标准行情、指数K线、五档
```

新增适配器和插件资产都放在 tdxman 项目内；消费项目中的插件目录只作为软链接目标，不维护副本。适配器不导入消费项目的 `app.*`，不读取其 preferences 或底层存储，也不执行 enriched 计算。旧客户端、aspool 公共方法签名与原有单位保持不变。

当前模块布局（已实现文件列为普通注释，规划文件注明待新增）：

```text
src/tdxman/adapters/tick_stock_panel/
  __init__.py       # 导出 TdxmanProvider
  provider.py       # 消费端接口、能力与异常转换
  contracts.py      # Schema、配置与错误码；完整覆盖诊断仍待扩展
  normalize.py      # 确定性的字段/单位/日期转换
  routing.py        # 资产到标准/MAC/本地入口的路由
  universe.py       # MAC目录分页、分类、去重及覆盖
src/tdxman/integrations/tick_stock_panel/plugin/
  plugin.yaml       # 由Stock Panel插件加载器读取的清单
  __init__.py       # check/availability和Provider导出；不复制转换逻辑
```

映射固定为：

```text
Tick Stock Panel/backend/app/plugins/tdxman
  -> tdxman/src/tdxman/integrations/tick_stock_panel/plugin
```

开发或部署前须确保消费端Python环境可导入安装后的tdxman包；软链接只提供插件清单与入口，不替代Python依赖安装。软链接由集成脚本创建和检查，不能在两侧各维护一份plugin.yaml。

连接复用优先使用现有生命周期；不提前建设常驻服务、HTTP层、Node桥接或新数据库。Polars 作为可选对接依赖延迟导入；使用本地模式时才要求 aspool 依赖。对应 extras 名称在实现时与现有 pyproject 核对后添加，不能把设计中的名称作为现成安装命令。

本期日线模式明确为 `online`（默认）或 `local`，一次请求固定模式。local 只调用 DataPool，只读、不联网、不自动同步；online 不静默混入本地旧数据。跨标准/MAC切换只能走经过同口径验证的显式路由，记录来源，不能把日期、复权或单位不同的结果拼起来。

## 5. 公共接口契约（目标）

Provider 为普通Python类，按实际插件鸭子类型协议实现；不要求继承消费端 `MarketDataProvider`。后者的 realtime/instruments 返回类型与插件调用链不完全一致。

```python
class TdxmanProvider:
    name = "tdxman"
    builtin = True

    def __init__(self, config=None): ...
    def close(self) -> None: ...
    def get_daily(self, symbols, start_time, end_time,
                  asset_type="stock", on_chunk_done=None): ...
    def iter_daily(self, symbols, start_time, end_time,
                   asset_type="stock", on_chunk_done=None): ...
    def get_minute(self, symbols, start_time, end_time,
                   asset_type="stock", freq="1m", on_chunk_done=None): ...
    def get_intraday_batch(self, symbols, count=300, asset_type="stock"): ...
    def get_intraday_latest(self, symbols=None, count=3): ...
    def get_realtime(self): ...
    def get_realtime_indices(self, symbols): ...
    def get_depth_batch(self, symbols): ...
    def get_instruments(self, asset_type="stock"): ...
    def test_dataset(self, dataset, symbols=None): ...
```

日K与分钟K返回 Polars DataFrame，`iter_daily` 返回迭代器；实时和标的接口返回 `list[dict]`；五档返回 `dict[str,dict]`；指数实时失败返回 `None`。`config.datasets` 必须是字典，与消费项目 plugin.yaml 能力一致。

公共参数：

- `symbols` 保留市场及前导零；输入顺序不影响结果。重复请求去重，空列表立即返回稳定空结构。只有明确允许的 `symbols=None` 表示全量。
- `asset_type` 只接受 `stock/etf/index`；非法值或资产与目录冲突明确报错。
- 起止边界包含；带时区输入先转上海时间。日线按上海日期裁剪，分钟按完整上海墙钟裁剪。开始晚于结束拒绝。
- 日线无起点时分页取尽服务端可用历史，达到配置上限必须报告截断；local 返回实际已存范围。分钟无起点表示可提供的最近窗口，返回覆盖说明，不暗示全历史。
- `freq` 首期支持 `1m/5m/15m/30m/60m`，分别映射两套枚举，不能直接把分钟数字当枚举值。其他频率拒绝，不降级成1m。
- `on_chunk_done(cur,total)` 为两参数，空批也推进；`total` 指事先确定的证券批次任务，不混用行数/未知页数，成功完成保证 `cur==total`。

## 6. 字段和封装细节：如何满足TF协议

### 6.1 证券与资产

输出 `600519.SH/000001.SZ/920000.BJ`；市场枚举映射为 SZ=0、SH=1、BJ=2，不靠数字前缀猜交易所。aspool 当前实现已输出后缀格式，同时接受旧前缀输入；部分旧文档尚未同步。

股票、ETF、指数分开路由和存储。`000001.SH` 与 `000001.SZ` 不得合并。行业板块 `88xxxx` 走 MAC，不能直接交给标准指数接口。aspool 的 list_indices/list_etfs 只是已存覆盖目录，不能单独作为全市场证券名单。

### 6.2 日K

| 字段 | 类型 | 要求 |
| --- | --- | --- |
| symbol | String | 带市场后缀，不缺失 |
| date | Date | 上海交易日期 |
| open/high/low/close | Float64 | 不复权；股票/ETF为元，指数为点 |
| volume | Float64 | 手；股票/ETF K线股数除100 |
| amount | Float64 | 元 |
| quote_ts | Int64，可选 | 真实行情更新时间的UTC毫秒戳；无依据置null/不提供 |

标准 K线 `vol`、MAC日K `vol`、aspool股票/ETF `volume` 是股；金额已是元。禁止照搬 stock-sdk 实时成交额乘10000。aspool指数单位为 `tdx_index_volume`，未证实与本项目“手”一致之前不得原样接入或盲除100；必须取得端点/资产级单位依据与样本，否则该资产路由显式不可用。

MAC日线 `datetime` 取日期；标准日线/aspool `date` 转 Date。日线零点日期戳不是报价更新时间，不能赋给 `quote_ts`。禁止适配器自行复权，MAC显式 `Adjust.NONE`。

主键 `(symbol,date)`；升序输出；分页重叠采用确定性的规则（同源同日后取样覆盖前取样），发现非预期冲突报错。OHLC必须有限且关系合法，量额非负；停牌全零记录按消费端既有规则过滤，不能填造交易日。缺必需字段的非空行属于坏数据，不能靠选现有列掩盖缺列。

### 6.3 分钟K与当日分钟

固定八列：`symbol:String, datetime:Datetime(us), open/high/low/close/volume/amount:Float64`。datetime 为上海 naive 墙钟，主键 `(symbol,datetime)`；volume 为手，amount 为元，可空且保留该列。源确实缺少的价格值使用 null，不得伪造开盘价，但不能把只有价格点的分时接口冒充完整OHLC分钟K。

优先调用标准/MAC真实分钟K。`get_tick_chart/get_tick_charts/get_minute_time_data` 缺少完整逐分钟OHLC/成交额，不能用全天OHLC填每行；不能用收盘价乘成交量伪造金额。

MAC解析日期时间已经是墙钟，不能再次按UTC加8小时。标准分钟K已组合为 datetime。分钟原始成交量须用对应资产fixture证实后按来源转换；指数仍受独立单位闸门约束。

按真实源的bar时间含义保留结束/起始时刻，验收09:30/09:31、11:30、13:00、15:00及集合竞价；不得任意移动一分钟或强行凑240/241根。分页到达请求起点才算覆盖完成，不能默认800根等于历史完整。服务端缺行不自动判定为停牌。

`get_intraday_batch` 返回当前上海交易日每证券最多count根，不返回前一日冒充今日；非交易日无法确定日期时返回带诊断的空结果。`get_intraday_latest` 返回相同日期每证券最近count根，`symbols=None` 使用本轮冻结的股票/ETF目录快照。两者均通过分钟标准化，不能把累计实时报价直接充当一分钟OHLCV。

历史深度需实测声明。不要复制 stock-sdk 的 `minute_history_days=5`，也不要因未声明而让UI误认为无限历史；初期采用已验证的保守窗口，超过窗口明确报告。全市场逐股查询不等于单次全市场接口，只有性能和覆盖验收通过才能开启 `full_minute`。

### 6.4 实时与指数

| 输出字段 | 来源/转换 | 空值规则 |
| --- | --- | --- |
| symbol | market+code | 缺失拒收 |
| last_price | 标准price / MAC close | 必需且有限 |
| prev_close | pre_close | 缺失不可伪造 |
| open/high/low | 对应字段 | 保留停牌语义，校验非法值 |
| volume | 报价vol，手 | 不套用K线除100 |
| amount | 报价amount，元 | 缺失null |
| change_amount | last_price-prev_close | 前提字段缺失则null |
| change_pct | change_amount/prev_close | 昨收<=0时null |
| turnover_rate | MAC turnover百分数 /100 | 缺失null |
| amplitude | 有可靠百分数则/100，或(high-low)/prev_close | 无依据null |
| name | 名称/目录 | 未知null |
| timestamp | 完整报价日期时间→UTC毫秒 | 未知null，不冒充新行情 |

标准 `server_time` 只有时分秒，MAC `SERVER_UPDATE_TIME` 也不能单独证明交易日期。日期必须来自可信行情会话/交易日来源，附内部时间质量诊断。周末、节假日、盘前及停牌不能直接拼本机今日。没有完整日期时允许普通报价降级，但不可通过依赖时效的盘后定版验收；获取时间只记 `fetched_at`，不替代交易时间。

全市场实时以本轮证券目录快照为分母，统计expected/received/missing及各资产、各市场数量。MAC分类列表默认count=80，必须显式取全；涨跌幅动态排序分页会漂移，优先冻结目录后按码分批报价。目录发现也需去重、页上限、无进展检查与数量校验，不能把非空视为全量。

普通实时覆盖股票和ETF；指数走 `get_realtime_indices(symbols)` 按请求补拉，防止重复。失败返回None以保留消费端上轮指数缓存；成功但确实无记录返回[]。部分失败无法在现有返回协议表达时整次返回None并记录失败证券，避免清空部分有效指数。

### 6.5 五档

```python
{
    "600519.SH": {
        "bid_prices": [10.0, 9.99, 9.98, 9.97, 9.96],
        "bid_volumes": [100.0, 200.0, 300.0, 400.0, 500.0],
        "ask_prices": [10.01, 10.02, 10.03, 10.04, 10.05],
        "ask_volumes": [90.0, 180.0, 270.0, 360.0, 450.0],
        "timestamp": 1789954200000,
    }
}
```

四个数组严格五项，一档到五档。标准bid1..5/ask1..5及对应量展开；MAC显式请求HANDICAP，不能把默认COMMON当五档。盘口数量“手”的来源单位须用fixture验证；价格元，不采用成交额转换规则。

源确认无挂单的0可保留；缺字段不等于无挂单，不用0补未知档位。初期不输出不完整盘口，记录该symbol失败，避免假盘口；时间戳无法可靠形成时该条不能作为完整depth5验收样本。单批失败由上层隔离，不在此处跨供应商回退。

### 6.6 标的与预览

`get_instruments` 返回 `symbol,name,code,exchange,region='CN',type` 及 `ext`。ext允许 `listing_date,total_shares,float_shares,tick_size,limit_up,limit_down`，没有可靠来源一律None。排除财务后不承诺股本完整，不为填满字段调用财务接口；MAC已有报价股本若使用须按该字段独立单位转换。标准FinanceInfo已转换为股的事实仅作防重复缩放提醒，不纳入本期数据路径。

标准北交所security_list不稳定，目录优先组合MAC A/BJ/ETF分类并验证覆盖。证券目录需区分股票、ETF、指数，不能把全部证券强制type=stock。上市日期未知不是今日上市；未知涨跌停价不是0。

`test_dataset` 返回 `provider,dataset,rows,columns,preview`，失败增加error，非空坏数据不能显示测试成功。日期/时间为ISO文本，NaN转null；preview至多5行。可加coverage/warnings字段但保持消费端兼容。试拉不写行情库，不切换路由。

## 7. 质量、错误、连接及缓存

新增内部错误码：`INVALID_ARGUMENT/UNSUPPORTED_ASSET/UNSUPPORTED_FREQUENCY/UPSTREAM_UNAVAILABLE/SCHEMA_MISMATCH/UNIT_UNVERIFIED/TIME_UNVERIFIED/INCOMPLETE_COVERAGE`。保留原异常链，不输出完整配置或凭据。

内部每次请求记录不可变诊断：request_id、数据集、资产、请求范围、实际范围、来源、失败证券、截断原因、时间质量及耗时。不能使用共享的单个last_error供并发请求互相覆盖。

| 公共方法 | 失败契约 |
| --- | --- |
| daily/minute/iter_daily | 参数、结构、来源失败抛明确异常；正常无记录返回有类型空表；不得将失败吞为成功空表 |
| realtime | 失败返回[]并告警；部分结果默认不发布为完整快照，记录覆盖诊断 |
| realtime_indices | 失败None；成功无数据[] |
| depth | 失败批次抛异常供上层隔离；坏证券不伪造条目 |
| instruments | 获取失败抛异常；成功无名单[]，不覆盖为伪空目录 |
| test_dataset | 捕获并返回结构化error及覆盖说明 |

`iter_daily` 只能有界读取和yield；中途失败抛出，消费端不得提交为完整同步。`get_daily` 是收集式便利方法，受总行数/内存上限约束，大范围走迭代接口。

标准单次报价最多80只；标准K线最多800条/次；MAC K线每页最多700条，MAC分类报价每页最多80条。复用已有连接工厂，连接不能跨线程无锁共享；每worker独立连接/串行队列，限制并发、请求超时、总任务截止时间、重试次数及退避。close幂等，关闭客户端和worker，取消不得留下后台同步。

目录可使用有限TTL不可变缓存；一次请求固定目录版本，不在分页中更换名单。实时缓存不得刷新时间戳掩盖陈旧数据。先完成源数据归一再进入消费端存储、generation和SSE链路，适配层不自行操作消费端缓存。

## 8. 消费项目接线（后续实现）

在tdxman新增受管插件资产 `src/tdxman/integrations/tick_stock_panel/plugin/`；其中plugin.yaml与薄入口的entry/check使用实际可导入路径。entry构造tdxman适配器，check验证依赖、配置、本地池/连接条件；不启动全市场扫描。Tick Stock Panel的`backend/app/plugins/tdxman`只创建到该目录的软链接。区分“已加载”与“数据集验证通过”。

目标datasets为 `daily,minute,realtime,depth5,full_minute`，按阶段验收后开启；不声明 `financial/adj_factor`。`get_realtime_indices/get_instruments` 是可选方法，不另造矩阵能力。`iter_daily/get_intraday_latest` 是增强方法。

当前消费项目能力粒度是数据集而非资产：如果daily对股票通过、指数仍因单位未验证而不可用，不能假装全部通过；描述和诊断必须列明资产限制，完成全资产验收后才能声称全覆盖。收益回测仍需其他来源复权数据，不能因本期原始行情可用就宣称已满足复权需求。

pip/uv安装路径、可选extras、插件重载及UI能力矩阵联调在实施时验证。安装完成后必须刷新消费端注册表；仅在独立进程ping成功不足以证明WebUI可选。不修改既有用户数据源选择。

## 9. 实施顺序与交付物

| 阶段 | 交付 | 完成门槛 |
| --- | --- | --- |
| P0 契约样本 | 本文各字段的标准/MAC/aspool fixture，单位与日期证据 | 指数成交量、分钟量额、盘口量、报价日期逐项明确；未知项不伪造 |
| P1 日线 | 可选适配包、三资产日线、local/online、iter_daily | 已完成三资产 local/online 路由、分页、日期/单位/空表与真实本地样本验收 |
| P2 实时与盘口 | 目录、实时、指数补拉、五档 | 已完成 MAC 目录、股票/ETF实时、指数补拉、五档；修复五档高位位图与别名字段解析，真实全市场实时为7,304条 |
| P3 分钟 | 多周期分钟、当日修复、最新N根调度 | 已完成1/5/15/30/60m 映射、墙钟规范化、当日修复与最新N根编排；真实单标的分钟与当日240根样本通过 |
| P4 接线 | tdxman受管插件清单/薄入口、软链接、试拉、重载 | 已创建受管清单/薄入口、消费端软链接；后端 `.venv` editable 安装并由真实加载器注册五类数据集 |

逐阶段提交可审查实现，不借机改造旧CLI或aspool契约；已有未提交变更属于工作区基线。每阶段记录源路由和适用资产、返回字段、验证结果与未解决限制。

## 10. 验收矩阵

| 编号 | 必须验证的行为 | 通过标准 |
| --- | --- | --- |
| A01 | 跨市场同码、ETF/指数分类 | 不混合SH.000001与SZ.000001；输出规范后缀 |
| A02 | 股票/ETF K线量额 | 83246080股转换832460.8手；金额不缩放；单位fixture可追溯 |
| A03 | 实时报价量额/比例 | 100手保持100；0.43%变0.0043；昨收0不产生无穷值 |
| A04 | 日期和频率 | UTC输入转上海；1m/5m等正确枚举；包含边界；非法频率拒绝 |
| A05 | 全部分页 | >80只报价、>700根MAC、>800根标准；空页、重复页、上限、重叠均有测试 |
| A06 | local日线 | 股票/ETF/指数独立；读取无网络/无写入；缺池、坏数据、空集合明确 |
| A07 | 分钟真实性 | OHLC真实；金额缺失null；午休、开收盘、跨日、短历史和停牌不伪造 |
| A08 | 指数单位/路由 | 核心指数及行业板块样本；量单位已证实，不仅测试价格 |
| A09 | 报价时间 | 盘前、周末、节假日、停牌、服务器时间缺失；旧报价不能变今日新行情 |
| A10 | 五档 | 五项顺序、数量单位、无单0与缺失区别、坏档位不冒充有效盘口 |
| A11 | 全市场完整性 | 股票SH/SZ/BJ与ETF有expected/received差异报告；漂移分页不漏而不报 |
| A12 | 错误隔离 | realtime=[]、indices=None语义准确；历史失败可识别；插件失效不阻断其他源 |
| A13 | 资源边界 | 多线程不共享裸连接；超时、重试、取消、close与内存有界 |
| A14 | 全量分钟 | 当日过滤、每证券count、重复时间去重、部分失败、最新窗口和运行成本 |
| A15 | 消费端联调 | 重载后可选、试拉字段正确、四类服务真实消费、未声明能力不出现 |
| A16 | 非目标守卫 | 不请求财务、不计算复权、不新增权重接口，原始价保持原始价 |

每接口离线测试包括正常、空、字段缺失、单位、时区、分页和异常；使用冻结协议样本，不依赖真实网络作为CI前提。在线验收选SH/SZ/BJ股票、沪深ETF、核心指数与行业板块；单标的通过后再测全市场规模，记录服务端地址标识、日期、样本数量、耗时、缺口及历史最早日期。在线请求只读，不切换生产路由或写消费端行情库。

全量分钟不是“必须6秒拉完全市场”的承诺。报告目标证券数、连接数、请求数、吞吐、峰值内存与实际轮次时长，再确定刷新配置；不能拿少量证券成绩证明全市场可用。

### 10.1 协议到实现与测试的追踪

| 消费协议 | 适配器落点与满足方式 | 必须通过的验收 |
| --- | --- | --- |
| TF01 日K | routing选择DataPool/标准/MAC；normalize统一价量及日期 | A01/A02/A04/A05/A06/A08 |
| TF02 迭代 | provider按有界证券批/页yield，报错终止，不预先收集全量 | A05/A12/A13 |
| TF03 分钟 | MAC/标准真实分钟K，显式原始价、频率映射、单位和墙钟 | A04/A05/A07/A08 |
| TF04 当日修复 | provider冻结目录、逐批获取今日1m并限制count | A07/A11/A13/A14 |
| TF05 最新增量 | 可选方法；最新N根编排达到规模标准才暴露 | A13/A14/A15 |
| TF06 全市场实时 | universe冻结名单，按码报价；normalize映射实时字段 | A01/A03/A09/A11/A12 |
| TF07 实时指数 | 接收完整请求指数集，按市场查询，None与[]分离 | A08/A09/A12/A15 |
| TF08 五档 | HANDICAP/标准字段，二次分片<=80，四数组及时间校验 | A05/A09/A10/A12 |
| TF09 标的 | universe合并目录、资产分类、嵌套ext未知null | A01/A11/A16 |
| TF10 试拉 | provider调用真实方法，序列化预览、保留错误与覆盖 | A12/A15 |
| TF11 生命周期 | check配置/依赖检查，close释放连接，清单与datasets一致 | A12/A13/A15/A16 |

## 11. 本文证据与当前未完成项

本轮检查已执行tdxman现有离线测试：`test_aspool_pool.py`、`test_aspool_index.py`、`test_aspool_etf.py`、`test_commands_offline.py`、`test_protocol_fixes.py`、`test_mac_tick_charts.py`，共67项通过。它们验证现有实现，不代表本文新增适配器已经实现或A01–A16已通过。

已确认的文档/代码差异：aspool输出已为code.market；FinanceInfo解析后股本已为股，部分模型注释仍写万股。接入应以解析器和冻结测试为依据，不能重复缩放。

实施前仍须解决：指数与部分分钟/盘口端点单位证据、完整报价交易日期来源、全市场目录完整性和实际分钟保留窗口。若某源无法提供可靠语义，应明确限制该资产/数据集，不以补0、猜日期或吞异常凑齐“全覆盖”。

最终交付定义：A01–A16适用项均有证据，消费项目通过真实Provider接线；仅实现方法名、ping或返回非空DataFrame不算完成。
