# 枚举完整取值

[常用类型与单位](models.md) · [API 总入口](README.md)

本页按当前源码列出市场、周期、分类与排序枚举的全部成员和值。`Category` 是 MAC 报价分类；`Market` 是沪深北证券市场码；`ExMarket` 是扩展市场码，三者不可互换。枚举能表达的值不保证所有服务器都有数据。

## `Market`

| 成员 | 值 |
| --- | ---: |
| `SZ` | `0` |
| `SH` | `1` |
| `BJ` | `2` |

## `KlineCategory`

| 成员 | 值 |
| --- | ---: |
| `MIN_5` | `0` |
| `MIN_15` | `1` |
| `MIN_30` | `2` |
| `MIN_60` | `3` |
| `DAY` | `4` |
| `WEEK` | `5` |
| `MONTH` | `6` |
| `MIN_1` | `7` |
| `MIN_3` | `8` |
| `YEAR` | `9` |
| `SEASON` | `10` |
| `YEAR_ALT` | `11` |

## `Period`

| 成员 | 值 |
| --- | ---: |
| `MIN_5` | `0` |
| `MIN_15` | `1` |
| `MIN_30` | `2` |
| `MIN_60` | `3` |
| `DAILY` | `4` |
| `WEEKLY` | `5` |
| `MONTHLY` | `6` |
| `MIN_1` | `7` |
| `MINS` | `8` |
| `DAYS` | `9` |
| `QUARTERLY` | `10` |
| `YEARLY` | `11` |
| `SECONDS` | `13` |

## `Adjust`

| 成员 | 值 |
| --- | ---: |
| `NONE` | `0` |
| `QFQ` | `1` |
| `HFQ` | `2` |

## `Category`

| 成员 | 值 |
| --- | ---: |
| `SH` | `0` |
| `SZ` | `2` |
| `A` | `6` |
| `B` | `7` |
| `KCB` | `8` |
| `BJ` | `12` |
| `CYB` | `14` |
| `BOARD_ALL` | `10000` |
| `BOARD_HY` | `10001` |
| `BOARD_HY2` | `10002` |
| `BOARD_GN` | `10004` |
| `BOARD_FG` | `10005` |
| `BOARD_DQ` | `10006` |
| `BOARD_OTHER` | `10007` |
| `BOARD_YJ_LEVEL1` | `10008` |
| `BOARD_YJ_LEVEL2` | `10009` |
| `BOARD_YJ_LEVEL3` | `10010` |
| `HGT` | `11001` |
| `SGT` | `11009` |
| `FXJS` | `11007` |
| `ETF` | `11005` |
| `LOF` | `11012` |
| `ZS` | `11052` |

## `BoardType`

| 成员 | 值 |
| --- | ---: |
| `HY` | `0` |
| `HY2` | `1` |
| `GN` | `3` |
| `FG` | `4` |
| `DQ` | `5` |
| `OTHER` | `6` |
| `YJ_LEVEL1` | `7` |
| `YJ_LEVEL2` | `8` |
| `YJ_LEVEL3` | `9` |
| `ALL` | `255` |

## `ExBoardType`

| 成员 | 值 |
| --- | ---: |
| `HK_ALL` | `0` |
| `HK_GN` | `1` |
| `HK_HY` | `2` |
| `US_ALL` | `3` |
| `US_GN` | `4` |
| `US_HY` | `5` |

## `SortType`

| 成员 | 值 |
| --- | ---: |
| `CODE` | `0` |
| `NAME` | `1` |
| `PRE_CLOSE` | `2` |
| `OPEN` | `3` |
| `HIGH` | `4` |
| `LOW` | `5` |
| `PRICE` | `6` |
| `BID` | `7` |
| `ASK` | `8` |
| `VOLUME` | `9` |
| `TOTAL_AMOUNT` | `10` |
| `LAST_VOLUME` | `11` |
| `CHANGE` | `12` |
| `CHANGE_PCT` | `14` |
| `AMPLITUDE_PCT` | `15` |
| `AVG` | `16` |
| `PE_DYNAMIC` | `17` |
| `ENTRUST_RATIO` | `18` |
| `INSIDE_VOLUME` | `19` |
| `OUTSIDE_VOLUME` | `20` |
| `IN_OUT_RATIO` | `21` |
| `BID_VOLUME` | `23` |
| `ASK_VOLUME` | `24` |
| `LOCKED_RATIO` | `27` |
| `LOCKED_AMOUNT` | `28` |
| `OPEN_AMOUNT` | `29` |
| `OPEN_TURNOVER_PCT` | `30` |
| `VOL_RATIO` | `35` |
| `TURNOVER_RATE` | `36` |
| `FLOAT_SHARES` | `37` |
| `FLOAT_MARKET_CAP` | `38` |
| `TOTAL_MARKET_CAP_AB` | `39` |
| `UNMATCHED_VOLUME` | `42` |
| `STRENGTH_PCT` | `45` |
| `SPEED_PCT` | `46` |
| `ACTIVITY` | `47` |
| `SHORT_TURNOVER_PCT` | `204` |
| `VOL_SPEED_PCT` | `208` |
| `MAIN_NET_AMOUNT` | `212` |
| `MAIN_NET_RATIO` | `215` |
| `AUCTION_LIMIT_BUY` | `258` |
| `AMOUNT_2M` | `268` |
| `OPEN_SNATCH_PCT` | `266` |
| `OPEN_PCT` | `281` |
| `HIGH_PCT` | `282` |
| `LOW_PCT` | `283` |
| `AVG_CHANGE_PCT` | `284` |
| `DRAWDOWN_PCT` | `286` |
| `ATTACK_PCT` | `287` |

## `SortOrder`

| 成员 | 值 |
| --- | ---: |
| `NONE` | `0` |
| `DESC` | `1` |
| `ASC` | `2` |

## `FilterType`

| 成员 | 值 |
| --- | ---: |
| `NEW` | `1` |
| `KC` | `2` |
| `ST` | `4` |
| `CY` | `8` |
| `HK_CONNECT` | `16` |
| `BJ` | `32` |
| `APPROVAL` | `64` |
| `REGISTRATION` | `128` |

## `ExMarket`

| 成员 | 值 |
| --- | ---: |
| `TEMP_STOCK` | `1` |
| `ZZ_FUTURES_OPTION` | `4` |
| `DL_FUTURES_OPTION` | `5` |
| `SH_FUTURES_OPTION` | `6` |
| `CFFEX_OPTION` | `7` |
| `SH_STOCK_OPTION` | `8` |
| `SZ_STOCK_OPTION` | `9` |
| `BASIC_FX` | `10` |
| `CROSS_FX` | `11` |
| `INTL_INDEX` | `12` |
| `COMEX_FUTURES` | `16` |
| `NYMEX_FUTURES` | `17` |
| `CBOT_FUTURES` | `18` |
| `HK_FINANCIAL_FUTURES` | `23` |
| `HK_FINANCIAL_OPTIONS` | `24` |
| `HK_STOCK_FUTURES` | `25` |
| `HK_STOCK_OPTIONS` | `26` |
| `HK_INDEX` | `27` |
| `ZZ_FUTURES` | `28` |
| `DL_FUTURES` | `29` |
| `SH_FUTURES` | `30` |
| `HK_MAIN_BOARD` | `31` |
| `OPEN_END_FUND` | `33` |
| `MONETARY_FUND` | `34` |
| `MACRO_INDICATOR` | `38` |
| `FUTURES_INDEX` | `42` |
| `B_TO_H` | `43` |
| `NEEQ` | `44` |
| `SH_GOLD` | `46` |
| `CFFEX_FUTURES` | `47` |
| `HK_GEM` | `48` |
| `HK_FUND` | `49` |
| `TREASURY_VALUATION` | `54` |
| `SUNSHINE_PRIVATE_FUND` | `56` |
| `BROKER_COLLECTIVE_FINANCE` | `57` |
| `BROKER_MONETARY_FINANCE` | `58` |
| `MAIN_FUTURES_CONTRACT` | `60` |
| `CSI_INDEX` | `62` |
| `GZ_ARBITRAGE_FUTURES` | `65` |
| `GZ_FUTURES` | `66` |
| `GZ_OPTIONS` | `67` |
| `RISK_CONTROL_INDEX` | `68` |
| `HUAZHENG_INDEX` | `69` |
| `EXTENDED_SECTOR_INDEX` | `70` |
| `HK_STOCK_GGT` | `71` |
| `GE_STOCK` | `73` |
| `US_STOCK` | `74` |
| `SG_STOCK` | `78` |
| `MONEY_MARKET` | `91` |
| `FUND_VALUATION` | `93` |
| `HK_DARK_POOL` | `98` |
| `CODE_MIRROR` | `100` |
| `SZSE_INDEX` | `102` |

源码：`src/tdxman/models/enums.py`、`src/tdxman/mac/enums.py`。
