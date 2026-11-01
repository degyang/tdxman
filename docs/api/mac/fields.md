# MAC 报价字段位图

[MAC 行情](README.md) · [报价接口](market.md) · [API 总入口](../README.md)

`get_stock_quotes` 和 `get_stock_quotes_list` 的 `fields` 可使用 `FieldBit`、`PresetField`、`FieldSelection` 或 `FieldBit` 序列。未传时批量报价默认 `PresetField.COMMON`，列表报价默认 `PresetField.BASIC + PresetField.VOLUME`。字段请求不保证服务器逐项返回；批量报价请检查 `DataFrame.attrs["missing_fields"]`。

```python
from tdxman import MacClient, Market
from tdxman.codec.bitmap import FieldBit, PresetField

with MacClient.from_best_host() as client:
    quote = client.get_stock_quotes(
        [(Market.SH, "600519")], fields=PresetField.OHLC + FieldBit.AMOUNT
    )
```

## 预设

| 预设 | 字段 |
| --- | --- |
| `PresetField.NONE` | 无 |
| `PresetField.OHLC` | `OPEN`, `HIGH`, `LOW`, `CLOSE` |
| `PresetField.BASIC` | `OPEN`, `HIGH`, `LOW`, `CLOSE`, `PRE_CLOSE`, `VOL` |
| `PresetField.QUOTE` | `BID_PRICE`, `ASK_PRICE`, `BID_VOLUME`, `ASK_VOLUME`, `LAST_VOLUME` |
| `PresetField.VOLUME` | `VOL`, `AMOUNT`, `TURNOVER`, `VOL_RATIO` |
| `PresetField.FUNDAMENTAL` | `TOTAL_SHARES`, `FLOAT_SHARES`, `EPS`, `NET_ASSETS` |
| `PresetField.ENHANCED` | `OPEN`, `HIGH`, `LOW`, `CLOSE`, `VOL`, `FLOAT_SHARES`, `ACTIVITY` |
| `PresetField.AH_CODE_FIELDS` | `OPEN`, `HIGH`, `LOW`, `CLOSE`, `VOL`, `AH_CODE`, `LOT_SIZE`, `INDUSTRY` |
| `PresetField.BOARD_STATS` | `LIMIT_UP_COUNT`, `LIMIT_DOWN_COUNT`, `UP_COUNT`, `DOWN_COUNT` |
| `PresetField.HANDICAP` | `BID_PRICE`, `BID2_PRICE`, `BID3_PRICE`, `BID4_PRICE`, `BID5_PRICE`, `ASK_PRICE`, `ASK2_PRICE`, `ASK3_PRICE`, `ASK4_PRICE`, `ASK5_PRICE`, `BID_VOLUME`, `LIMIT_UP_COUNT`, `BID3_VOLUME`, `BID4_VOLUME`, `UP_COUNT`, `ASK_VOLUME`, `LIMIT_DOWN_COUNT`, `ASK3_VOLUME`, `ASK4_VOLUME`, `DOWN_COUNT` |
| `PresetField.COMMON` | `PRE_CLOSE`, `OPEN`, `HIGH`, `LOW`, `CLOSE`, `VOL`, `VOL_RATIO`, `AMOUNT`, `TOTAL_SHARES`, `FLOAT_SHARES`, `EPS`, `NET_ASSETS`, `SECURITY_TYPE_PRICE`, `TOTAL_MARKET_CAP_AB`, `PE_DYNAMIC`, `LOT_SIZE_INFO`, `DIVIDEND_YIELD`, `LAST_VOLUME`, `TURNOVER`, `STOCK_TAG_FLAGS`, `DECIMAL_POINT`, `BUY_PRICE_LIMIT`, `SELL_PRICE_LIMIT`, `PRICE_DECIMAL_INFO`, `LOT_SIZE`, `PRE_IOPV`, `SPEED_PCT`, `FLAG_KCB`, `PE_TTM`, `PE_STATIC`, `MAIN_NET_AMOUNT`, `VOL_SPEED_PCT`, `SHORT_TURNOVER_PCT`, `CIRCULATING_CAPITAL_Z` |
| `PresetField.DEBUG` | 调试用全字段；非稳定业务列集 |
| `PresetField.ALL` | `PRE_CLOSE`, `OPEN`, `HIGH`, `LOW`, `CLOSE`, `VOL`, `VOL_RATIO`, `AMOUNT`, `INSIDE_VOLUME`, `OUTSIDE_VOLUME`, `TOTAL_SHARES`, `FLOAT_SHARES`, `EPS`, `NET_ASSETS`, `SECURITY_TYPE_PRICE`, `TOTAL_MARKET_CAP_AB`, `PE_DYNAMIC`, `BID_PRICE`, `ASK_PRICE`, `SERVER_UPDATE_DATE`, `SERVER_UPDATE_TIME`, `LOT_SIZE_INFO`, `BOARD_STRENGTH`, `DIVIDEND_YIELD`, `BID_VOLUME`, `ASK_VOLUME`, `LAST_VOLUME`, `TURNOVER`, `INDUSTRY`, `INDUSTRY_CHANGE_UP`, `STOCK_TAG_FLAGS`, `DECIMAL_POINT`, `BUY_PRICE_LIMIT`, `SELL_PRICE_LIMIT`, `PRICE_DECIMAL_INFO`, `LOT_SIZE`, `PRE_IOPV`, `SPEED_PCT`, `AVG_PRICE`, `IOPV`, `PE_TTM_VOL_RELATED`, `EX_PRICE_PLACEHOLDER`, `OPERATING_REVENUE`, `FLAG_KCB`, `FLAG_BJ`, `CIRCULATING_CAPITAL_Z`, `AFTER_HOURS_VOLUME`, `PE_TTM`, `PE_STATIC`, `INDEX_METRIC`, `MAIN_NET_AMOUNT`, `BID_ASK_RATIO`, `NON_INDEX_FLAG`, `CHANGE_20D_PCT`, `YTD_PCT`, `STOCK_CLASS_CODE`, `PERCENT_BASE`, `MTD_PCT`, `CHANGE_1Y_PCT`, `PREV_CHANGE_PCT`, `CHANGE_3D_PCT`, `CHANGE_60D_PCT`, `CHANGE_5D_PCT`, `CHANGE_10D_PCT`, `PREV2_CHANGE_PCT`, `BID2_PRICE`, `ASK2_PRICE`, `AH_CODE`, `UNKNOWN_CODE`, `OPEN_AMOUNT`, `ANNUAL_LIMIT_UP_DAYS`, `ACTIVITY`, `DIVIDEND_YIELD_RATE`, `CONSECUTIVE_UP_DAYS`, `LIMIT_UP_COUNT`, `LIMIT_DOWN_COUNT`, `INDUSTRY_SUB`, `AUCTION_BUY_LIMIT`, `AUCTION_SELL_LIMIT`, `VOL_SPEED_PCT`, `SHORT_TURNOVER_PCT`, `AMOUNT_2M`, `MAIN_NET_AMOUNT_COPY`, `MAIN_NET_RATIO`, `RETAIL_NET_AMOUNT`, `MAIN_NET_5M_AMOUNT`, `MAIN_NET_3D_AMOUNT`, `MAIN_NET_5D_AMOUNT`, `MAIN_NET_10D_AMOUNT`, `MAIN_BUY_NET_AMOUNT`, `DDX`, `DDY`, `DDZ`, `DDF`, `STOCK_FLAG_A`, `STOCK_FLAG_B`, `AUCTION_VOL_RATIO`, `PREV_AMOUNT`, `RECENT_INDICATOR`, `BID3_PRICE`, `BID4_PRICE`, `BID5_PRICE`, `ASK3_PRICE`, `ASK4_PRICE`, `ASK5_PRICE`, `BID3_VOLUME`, `BID4_VOLUME`, `UP_COUNT`, `ASK3_VOLUME`, `ASK4_VOLUME`, `DOWN_COUNT`, `BID_ASK_DIFF`, `CHANGE_UP_TYPE`, `SAFETY_SCORE`, `HIGHLIGHT_COUNT`, `CHANGE_AT_1000`, `CHANGE_AT_1030`, `CHANGE_AT_1100`, `CHANGE_AT_1130`, `CHANGE_AT_1330`, `CHANGE_AT_1400`, `CHANGE_AT_1430` |

可用 `+` 或 `|` 组合预设与单字段。`ALL` 请求全部已定义字段，返回内容仍由服务器决定。

## 字段位

下表的“列名”按 `FieldBit.field_name` 计算；别名与原位共享数值，解码时可能使用规范名。`fmt` 是二进制解析格式，不等于展示精度。

| 字段 | 位值 | 列名 | fmt | 源码描述 |
| --- | --- | --- | --- | --- |
| `FieldBit.PRE_CLOSE` | `0x00` | `pre_close` | `<f` | 昨收 |
| `FieldBit.OPEN` | `0x01` | `open` | `<f` | 开盘价 |
| `FieldBit.HIGH` | `0x02` | `high` | `<f` | 最高价 |
| `FieldBit.LOW` | `0x03` | `low` | `<f` | 最低价 |
| `FieldBit.CLOSE` | `0x04` | `close` | `<f` | 收盘价 |
| `FieldBit.VOL` | `0x05` | `vol` | `<I` | 成交量 |
| `FieldBit.VOL_RATIO` | `0x06` | `vol_ratio` | `<f` | 量比 |
| `FieldBit.AMOUNT` | `0x07` | `amount` | `<f` | 总金额(元) |
| `FieldBit.INSIDE_VOLUME` | `0x08` | `inside_volume` | `<I` | 内盘 |
| `FieldBit.OUTSIDE_VOLUME` | `0x09` | `outside_volume` | `<I` | 外盘 |
| `FieldBit.TOTAL_SHARES` | `0x0A` | `total_shares` | `<f` | 总股数(单位万) |
| `FieldBit.FLOAT_SHARES` | `0x0B` | `float_shares` | `<f` | 流通股(单位万) |
| `FieldBit.EPS` | `0x0C` | `eps` | `<f` | 每股收益 |
| `FieldBit.NET_ASSETS` | `0x0D` | `net_assets` | `<f` | 净资产 |
| `FieldBit.SECURITY_TYPE_PRICE` | `0x0E` | `security_type_price` | `<f` | 证券类型价 |
| `FieldBit.TOTAL_MARKET_CAP_AB` | `0x0F` | `total_market_cap_ab` | `<f` | AB股总市值 |
| `FieldBit.PE_DYNAMIC` | `0x10` | `pe_dynamic` | `<f` | 市盈率(动) |
| `FieldBit.BID_PRICE` | `0x11` | `bid_price` | `<f` | 买一价 |
| `FieldBit.ASK_PRICE` | `0x12` | `ask_price` | `<f` | 卖一价 |
| `FieldBit.SERVER_UPDATE_DATE` | `0x13` | `server_update_date` | `<I` | 服务器更新日期 YYYYMMDD |
| `FieldBit.SERVER_UPDATE_TIME` | `0x14` | `server_update_time` | `<I` | 服务器更新时间 HHMMSS |
| `FieldBit.LOT_SIZE_INFO` | `0x15` | `lot_size_info` | `<I` | 未确定 |
| `FieldBit.BOARD_STRENGTH` | `0x16` | `board_strength` | `<f` | 板块强度(涨跌家数差) |
| `FieldBit.DIVIDEND_YIELD` | `0x17` | `dividend_yield` | `<f` | 每股股息(元) |
| `FieldBit.BID_VOLUME` | `0x18` | `bid_volume` | `<I` | 买量 |
| `FieldBit.ASK_VOLUME` | `0x19` | `ask_volume` | `<I` | 卖量 |
| `FieldBit.LAST_VOLUME` | `0x1A` | `last_volume` | `<I` | 现量 |
| `FieldBit.TURNOVER` | `0x1B` | `turnover` | `<f` | 换手 |
| `FieldBit.INDUSTRY` | `0x1C` | `industry` | `<I` | 行业分类代码 |
| `FieldBit.INDUSTRY_CHANGE_UP` | `0x1D` | `industry_change_up` | `<f` | 行业涨跌幅 |
| `FieldBit.STOCK_TAG_FLAGS` | `0x1E` | `stock_tag_flags` | `<I` | 股票标签位图 |
| `FieldBit.DECIMAL_POINT` | `0x1F` | `decimal_point` | `<I` | 数据精度 |
| `FieldBit.BUY_PRICE_LIMIT` | `0x20` | `buy_price_limit` | `<f` | 涨停价 |
| `FieldBit.SELL_PRICE_LIMIT` | `0x21` | `sell_price_limit` | `<f` | 跌停价 |
| `FieldBit.PRICE_DECIMAL_INFO` | `0x22` | `price_decimal_info` | `<I` | 价格精度标志 |
| `FieldBit.LOT_SIZE` | `0x23` | `lot_size` | `<I` | 所属地区板块/每手股数 |
| `FieldBit.PRE_IOPV` | `0x24` | `pre_iopv` | `<f` | 昨IOPV |
| `FieldBit.SPEED_PCT` | `0x25` | `speed_pct` | `<f` | 涨速 |
| `FieldBit.AVG_PRICE` | `0x26` | `avg_price` | `<f` | 均价 |
| `FieldBit.IOPV` | `0x27` | `iopv` | `<f` | IOPV |
| `FieldBit.PE_TTM_VOL_RELATED` | `0x28` | `pe_ttm_vol_related` | `<f` | 前参考价(美股适用) |
| `FieldBit.EX_PRICE_PLACEHOLDER` | `0x29` | `ex_price_placeholder` | `<f` | 前金额参考 |
| `FieldBit.OPERATING_REVENUE` | `0x2A` | `operating_revenue` | `<f` | 营业收入(万) |
| `FieldBit.FLAG_KCB` | `0x2B` | `flag_kcb` | `<I` | 科创板标志 |
| `FieldBit.FLAG_BJ` | `0x2C` | `flag_bj` | `<I` | 北交所标志 |
| `FieldBit.CIRCULATING_CAPITAL_Z` | `0x2D` | `circulating_capital_z` | `<f` | 流通股本Z（单位：万股） |
| `FieldBit.AFTER_HOURS_VOLUME` | `0x2E` | `after_hours_volume` | `<i` | 盘后量 |
| `FieldBit.PE_TTM` | `0x30` | `pe_ttm` | `<f` | 市盈率TTM |
| `FieldBit.PE_STATIC` | `0x31` | `pe_static` | `<f` | 市盈率静 |
| `FieldBit.INDEX_METRIC` | `0x37` | `index_metric` | `<f` | 指数指标 |
| `FieldBit.MAIN_NET_AMOUNT` | `0x38` | `main_net_amount` | `<f` | 今日主力净流入 |
| `FieldBit.BID_ASK_RATIO` | `0x39` | `bid_ask_ratio` | `<f` | 委比 |
| `FieldBit.NON_INDEX_FLAG` | `0x3A` | `non_index_flag` | `<I` | 非指数标志 |
| `FieldBit.CHANGE_20D_PCT` | `0x3B` | `change_20d_pct` | `<f` | 20日涨幅% |
| `FieldBit.YTD_PCT` | `0x3C` | `ytd_pct` | `<f` | 年初至今% |
| `FieldBit.STOCK_CLASS_CODE` | `0x3E` | `stock_class_code` | `<I` | 证券子分类码 |
| `FieldBit.PERCENT_BASE` | `0x3F` | `percent_base` | `<I` | 百分比基底 |
| `FieldBit.MTD_PCT` | `0x40` | `mtd_pct` | `<f` | 月初至今% |
| `FieldBit.CHANGE_1Y_PCT` | `0x41` | `change_1y_pct` | `<f` | 一年涨幅% |
| `FieldBit.PREV_CHANGE_PCT` | `0x42` | `prev_change_pct` | `<f` | 昨涨幅% |
| `FieldBit.CHANGE_3D_PCT` | `0x43` | `change_3d_pct` | `<f` | 3日涨幅% |
| `FieldBit.CHANGE_60D_PCT` | `0x44` | `change_60d_pct` | `<f` | 60日涨幅% |
| `FieldBit.CHANGE_5D_PCT` | `0x45` | `change_5d_pct` | `<f` | 5日涨幅% |
| `FieldBit.CHANGE_10D_PCT` | `0x46` | `change_10d_pct` | `<f` | 10日涨幅% |
| `FieldBit.PREV2_CHANGE_PCT` | `0x47` | `prev2_change_pct` | `<f` | 前日涨幅% |
| `FieldBit.BID2_PRICE` | `0x48` | `bid2_price` | `<f` | 买二价 |
| `FieldBit.ASK2_PRICE` | `0x49` | `ask2_price` | `<f` | 卖二价 |
| `FieldBit.AH_CODE` | `0x4A` | `ah_code` | `<I` | 对应A/H股code |
| `FieldBit.UNKNOWN_CODE` | `0x4B` | `unknown_code` | `<I` | 少部分有数据 |
| `FieldBit.OPEN_AMOUNT` | `0x57` | `open_amount` | `<f` | 开盘金额(元) |
| `FieldBit.ANNUAL_LIMIT_UP_DAYS` | `0x58` | `annual_limit_up_days` | `<i` | 年涨停天数 |
| `FieldBit.ACTIVITY` | `0x59` | `activity` | `<I` | 活跃度 |
| `FieldBit.DIVIDEND_YIELD_RATE` | `0x5B` | `dividend_yield_rate` | `<f` | 股息率% |
| `FieldBit.CONSECUTIVE_UP_DAYS` | `0x5C` | `consecutive_up_days` | `<i` | 连涨天 |
| `FieldBit.LIMIT_UP_COUNT` | `0x5D` | `limit_up_count` | `<I` | 涨停数(板块) / 买二量(个股) |
| `FieldBit.BID2_VOLUME`（别名） | `0x5D` | `limit_up_count` | `<I` | 涨停数(板块) / 买二量(个股) |
| `FieldBit.LIMIT_DOWN_COUNT` | `0x5E` | `limit_down_count` | `<I` | 跌停数(板块) / 卖二量(个股) |
| `FieldBit.ASK2_VOLUME`（别名） | `0x5E` | `limit_down_count` | `<I` | 跌停数(板块) / 卖二量(个股) |
| `FieldBit.INDUSTRY_SUB` | `0x5F` | `industry_sub` | `<I` | 行业二级分类 |
| `FieldBit.AUCTION_BUY_LIMIT` | `0x66` | `auction_buy_limit` | `<f` | 连续竞价买入上限 |
| `FieldBit.AUCTION_SELL_LIMIT` | `0x67` | `auction_sell_limit` | `<f` | 连续竞价卖出下限 |
| `FieldBit.VOL_SPEED_PCT` | `0x68` | `vol_speed_pct` | `<f` | 量涨速% |
| `FieldBit.SHORT_TURNOVER_PCT` | `0x69` | `short_turnover_pct` | `<f` | 短换手% |
| `FieldBit.AMOUNT_2M` | `0x6A` | `amount_2m` | `<f` | 2分钟金额(元) |
| `FieldBit.MAIN_NET_AMOUNT_COPY` | `0x6B` | `main_net_amount_copy` | `<f` | 今日主力净流入(副本) |
| `FieldBit.MAIN_NET_RATIO` | `0x6C` | `main_net_ratio` | `<f` | 主力净比% |
| `FieldBit.RETAIL_NET_AMOUNT` | `0x6D` | `retail_net_amount` | `<f` | 散户单增比 |
| `FieldBit.MAIN_NET_5M_AMOUNT` | `0x6E` | `main_net_5m_amount` | `<f` | 5分钟主力净额 |
| `FieldBit.MAIN_NET_3D_AMOUNT` | `0x6F` | `main_net_3d_amount` | `<f` | 近三日主力净额 |
| `FieldBit.MAIN_NET_5D_AMOUNT` | `0x70` | `main_net_5d_amount` | `<f` | 近五日主力净额 |
| `FieldBit.MAIN_NET_10D_AMOUNT` | `0x71` | `main_net_10d_amount` | `<f` | 近十日主买金额(待确定) |
| `FieldBit.MAIN_BUY_NET_AMOUNT` | `0x72` | `main_buy_net_amount` | `<f` | 今日主买净额 |
| `FieldBit.DDX` | `0x73` | `ddx` | `<f` | DDX |
| `FieldBit.DDY` | `0x74` | `ddy` | `<f` | DDY |
| `FieldBit.DDZ` | `0x75` | `ddz` | `<f` | DDZ |
| `FieldBit.DDF` | `0x76` | `ddf` | `<f` | DDF |
| `FieldBit.STOCK_FLAG_A` | `0x77` | `stock_flag_a` | `<f` | 个股标志位A |
| `FieldBit.STOCK_FLAG_B` | `0x78` | `stock_flag_b` | `<f` | 个股标志位B(副本) |
| `FieldBit.AUCTION_VOL_RATIO` | `0x7A` | `auction_vol_ratio` | `<f` | 竞价昨比 |
| `FieldBit.PREV_AMOUNT` | `0x7B` | `prev_amount` | `<f` | 昨成交额(元) |
| `FieldBit.RECENT_INDICATOR` | `0x7D` | `recent_indicator` | `<f` | 近日指标提示 |
| `FieldBit.BID3_PRICE` | `0x80` | `bid3_price` | `<f` | 买三价 |
| `FieldBit.BID4_PRICE` | `0x81` | `bid4_price` | `<f` | 买四价 |
| `FieldBit.BID5_PRICE` | `0x82` | `bid5_price` | `<f` | 买五价 |
| `FieldBit.ASK3_PRICE` | `0x83` | `ask3_price` | `<f` | 卖三价 |
| `FieldBit.ASK4_PRICE` | `0x84` | `ask4_price` | `<f` | 卖四价 |
| `FieldBit.ASK5_PRICE` | `0x85` | `ask5_price` | `<f` | 卖五价 |
| `FieldBit.BID3_VOLUME` | `0x86` | `bid3_volume` | `<I` | 买三量 |
| `FieldBit.BID4_VOLUME` | `0x87` | `bid4_volume` | `<I` | 买四量 |
| `FieldBit.UP_COUNT` | `0x88` | `up_count` | `<I` | 上涨家数(板块) / 买五量(个股) |
| `FieldBit.BID5_VOLUME`（别名） | `0x88` | `up_count` | `<I` | 上涨家数(板块) / 买五量(个股) |
| `FieldBit.ASK3_VOLUME` | `0x89` | `ask3_volume` | `<I` | 卖三量 |
| `FieldBit.ASK4_VOLUME` | `0x8A` | `ask4_volume` | `<I` | 卖四量 |
| `FieldBit.DOWN_COUNT` | `0x8B` | `down_count` | `<I` | 下跌家数(板块) / 卖五量(个股) |
| `FieldBit.ASK5_VOLUME`（别名） | `0x8B` | `down_count` | `<I` | 下跌家数(板块) / 卖五量(个股) |
| `FieldBit.BID_ASK_DIFF` | `0x8C` | `bid_ask_diff` | `<i` | 委差 |
| `FieldBit.CHANGE_UP_TYPE` | `0x8D` | `change_up_type` | `<i` | 封板状态 |
| `FieldBit.SAFETY_SCORE` | `0x8E` | `safety_score` | `<f` | 安全分 |
| `FieldBit.HIGHLIGHT_COUNT` | `0x8F` | `highlight_count` | `<f` | 亮点数 |
| `FieldBit.CHANGE_AT_1000` | `0x90` | `change_at_1000` | `<f` | 日内涨幅% 10:00 |
| `FieldBit.CHANGE_AT_1030` | `0x91` | `change_at_1030` | `<f` | 日内涨幅% 10:30 |
| `FieldBit.CHANGE_AT_1100` | `0x92` | `change_at_1100` | `<f` | 日内涨幅% 11:00 |
| `FieldBit.CHANGE_AT_1130` | `0x93` | `change_at_1130` | `<f` | 日内涨幅% 11:30 |
| `FieldBit.CHANGE_AT_1330` | `0x94` | `change_at_1330` | `<f` | 日内涨幅% 13:30 |
| `FieldBit.CHANGE_AT_1400` | `0x95` | `change_at_1400` | `<f` | 日内涨幅% 14:00 |
| `FieldBit.CHANGE_AT_1430` | `0x96` | `change_at_1430` | `<f` | 日内涨幅% 14:30 |

源码：`src/tdxman/codec/bitmap.py`。对“未确定”“待确定”字段，不要自行赋予业务含义。
