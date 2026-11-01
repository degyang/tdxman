# 数据模型字段索引

[类型与单位](models.md) · [API 总入口](README.md)

本页按当前 dataclass 声明列出公开字段（隐藏 `_raw` 等内部字段）。它是字段名索引：标准协议转 DataFrame 时会移除内部字段并合并日期列；MAC 结果还受请求字段和服务端响应影响，不能把下面每个字段都视为每次查询必有。类型和单位以各业务页、源码及实际列为准。

## 标准行情模型

| 模型 | 字段 | 源模块 |
| --- | --- | --- |
| `IndexBar` | `open`, `close`, `high`, `low`, `vol`, `amount`, `year`, `month`, `day`, `hour`, `minute`, `up_count`, `down_count` | `tdxman.models.bar` |
| `SecurityBar` | `open`, `close`, `high`, `low`, `vol`, `amount`, `year`, `month`, `day`, `hour`, `minute` | `tdxman.models.bar` |
| `SecurityInfo` | `market`, `code`, `name`, `volunit`, `decimal_point`, `pre_close`, `industry_tdx`, `industry_sw` | `tdxman.models.security` |
| `SecurityQuote` | `market`, `code`, `price`, `pre_close`, `open`, `high`, `low`, `vol`, `cur_vol`, `amount`, `s_vol`, `b_vol`, `active1`, `active2`, `bid1`, `bid_vol1`, `bid2`, `bid_vol2`, `bid3`, `bid_vol3`, `bid4`, `bid_vol4`, `bid5`, `bid_vol5`, `ask1`, `ask_vol1`, `ask2`, `ask_vol2`, `ask3`, `ask_vol3`, `ask4`, `ask_vol4`, `ask5`, `ask_vol5`, `rise_speed`, `limit_up`, `limit_down`, `unknown_2`, `unknown_3`, `unknown_5`, `unknown_6`, `unknown_7`, `unknown_8`, `server_time` | `tdxman.models.quote` |

## 财务与统计模型

| 模型 | 字段 | 源模块 |
| --- | --- | --- |
| `CompanyInfoCategory` | `name`, `filename`, `start`, `length` | `tdxman.models.finance` |
| `FinanceInfo` | `market`, `code`, `liutong_guben`, `zong_guben`, `guojia_gu`, `faqiren_faren_gu`, `faren_gu`, `b_gu`, `h_gu`, `zhigong_gu`, `province`, `industry`, `updated_date`, `ipo_date`, `gudong_renshu`, `zong_zichan`, `liudong_zichan`, `guding_zichan`, `wuxing_zichan`, `liudong_fuzhai`, `changqi_fuzhai`, `ziben_gongjijin`, `jing_zichan`, `zhuying_shouru`, `zhuying_lirun`, `yingshou_zhangkuan`, `yingye_lirun`, `touzi_shouyu`, `jingying_xianjinliu`, `zong_xianjinliu`, `cunhuo`, `lirun_zonghe`, `shuihou_lirun`, `jing_lirun`, `weifen_lirun`, `meigujing_zichan`, `reserve2` | `tdxman.models.finance` |
| `FinancialFileInfo` | `filename`, `hash`, `filesize` | `tdxman.models.finance` |
| `FinancialRecord` | `code`, `market`, `report_date`, `fields` | `tdxman.models.finance` |
| `TdxBlock` | `name`, `category`, `count`, `codes` | `tdxman.models.finance` |
| `XdxrRecord` | `market`, `code`, `year`, `month`, `day`, `category`, `name`, `fenhong`, `peigujia`, `songzhuangu`, `peigu`, `suogu`, `xingquanjia`, `fenshu`, `panqian_liutong`, `panhou_liutong`, `qian_zongguben`, `hou_zongguben` | `tdxman.models.finance` |
| `MinuteBar` | `price`, `vol` | `tdxman.models.timeseries` |
| `TransactionRecord` | `hour`, `minute`, `price`, `vol`, `buyorsell`, `unknown_last` | `tdxman.models.timeseries` |
| `FundFlow` | `super_in`, `large_in`, `medium_in`, `small_in`, `super_out`, `large_out`, `medium_out`, `small_out` | `tdxman.models.stats` |
| `HistoricalFundFlow` | `year`, `month`, `day`, `super_in`, `super_out`, `large_in`, `large_out`, `medium_in`, `medium_out`, `small_in`, `small_out` | `tdxman.models.stats` |
| `MarketStat` | `up_count`, `down_count`, `neutral_count`, `suspended_count`, `total_count`, `total_amount`, `total_volume`, `total_market_cap`, `limit_up_count`, `limit_down_count`, `query_time` | `tdxman.models.stats` |

## 标准扩展模型

| 模型 | 字段 | 源模块 |
| --- | --- | --- |
| `ExInstrumentBar` | `open`, `high`, `low`, `close`, `position`, `trade`, `amount`, `year`, `month`, `day`, `hour`, `minute` | `tdxman.ex.models` |
| `ExInstrumentInfo` | `category`, `market`, `code`, `name`, `desc` | `tdxman.ex.models` |
| `ExInstrumentQuote` | `market`, `code`, `pre_close`, `open`, `high`, `low`, `price`, `kaicang`, `zongliang`, `xianliang`, `neipan`, `waipan`, `chicang`, `bid1`, `bid2`, `bid3`, `bid4`, `bid5`, `bid_vol1`, `bid_vol2`, `bid_vol3`, `bid_vol4`, `bid_vol5`, `ask1`, `ask2`, `ask3`, `ask4`, `ask5`, `ask_vol1`, `ask_vol2`, `ask_vol3`, `ask_vol4`, `ask_vol5` | `tdxman.ex.models` |
| `ExMarketInfo` | `market`, `category`, `name`, `short_name` | `tdxman.ex.models` |
| `ExMinuteBar` | `hour`, `minute`, `price`, `avg_price`, `volume`, `open_interest` | `tdxman.ex.models` |
| `ExTransactionRecord` | `hour`, `minute`, `second`, `price`, `volume`, `zengcang`, `nature` | `tdxman.ex.models` |

## MAC 协议模型

| 模型 | 字段 | 源模块 |
| --- | --- | --- |
| `AuctionItem` | `time`, `price`, `matched`, `unmatched` | `tdxman.mac.models` |
| `BelongBoardInfo` | `board_type`, `market`, `board_code`, `board_name`, `close`, `pre_close` | `tdxman.mac.models` |
| `BoardInfo` | `market`, `code`, `name`, `price`, `rise_speed`, `pre_close`, `symbol_market`, `symbol_code`, `symbol_name`, `symbol_price`, `symbol_rise_speed`, `symbol_pre_close` | `tdxman.mac.models` |
| `CapitalFlowData` | `date`, `main_in`, `main_out`, `main_net`, `small_in`, `small_out`, `small_net`, `mid_in`, `mid_out`, `mid_net`, `large_in`, `large_out`, `large_net` | `tdxman.mac.models` |
| `KlineOffsetInfo` | `total`, `returned` | `tdxman.mac.models` |
| `MacBar` | `datetime`, `open`, `high`, `low`, `close`, `vol`, `amount`, `float_shares` | `tdxman.mac.models` |
| `MacMultiTickChart` | `market`, `code`, `name`, `pre_close`, `open`, `high`, `low`, `close`, `vol`, `amount`, `turnover`, `avg`, `charts` | `tdxman.mac.models` |
| `MacMultiTickDay` | `date`, `pre_close`, `ticks` | `tdxman.mac.models` |
| `MacQuoteField` | `market`, `code`, `name`, `fields` | `tdxman.mac.models` |
| `MacSymbolInfo` | `market`, `code`, `name`, `time`, `activity`, `pre_close`, `open`, `high`, `low`, `close`, `momentum`, `vol`, `amount`, `inside_volume`, `outside_volume`, `turnover`, `avg` | `tdxman.mac.models` |
| `MacTick` | `time`, `price`, `avg`, `vol`, `momentum` | `tdxman.mac.models` |
| `MacTickChart` | `market`, `code`, `name`, `pre_close`, `open`, `high`, `low`, `close`, `vol`, `amount`, `turnover`, `avg`, `charts`, `source_date`, `reference_pre_close` | `tdxman.mac.models` |
| `MacTransaction` | `time`, `price`, `vol`, `trade_count`, `bs_flag` | `tdxman.mac.models` |
| `ServerSession` | `today`, `last_trading_day`, `sessions_1`, `sessions_2`, `market_param_1`, `market_param_2` | `tdxman.mac.models` |
| `UnusualItem` | `index`, `market`, `code`, `name`, `time`, `desc`, `value`, `unusual_type` | `tdxman.mac.models` |

## 离线模型

| 模型 | 字段 | 源模块 |
| --- | --- | --- |
| `CustomerBlock` | `blockname`, `block_type`, `codes` | `tdxman.offline.block` |
| `GbbqRecord` | `market`, `code`, `datetime`, `category`, `hongli_panqianliutong`, `peigujia_qianzongguben`, `songgu_qianzongguben`, `peigu_houzongguben` | `tdxman.offline.gbbq` |
| `ExDailyBar` | `open`, `high`, `low`, `close`, `amount`, `vol`, `settlement`, `hk_stock_amount`, `year`, `month`, `day` | `tdxman.offline.ex_daily_bar` |

标准 K 线的 `SecurityBar` 原始日期分量 `year/month/day/hour/minute` 由 `_merge_bar_datetime` 合并为 `date` 或 `datetime`；仅含年月日的模型由 `_to_df` 合并为 `date`。`FinancialRecord.fields` 是历史财务文件中的字段序列，需结合报告期及财务字段定义解释。源码：`src/tdxman/_df.py`、`src/tdxman/models/`、`src/tdxman/mac/models.py`、`src/tdxman/ex/models.py`、`src/tdxman/offline/`。
