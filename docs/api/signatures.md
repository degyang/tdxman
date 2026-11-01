# Python API 签名总表

本页按当前源码提取所有公开客户端/服务方法签名，用于检查方法覆盖。参数行为、返回字段和风险请阅读[API 入口](README.md)下的业务页。`__init__`、上下文管理等在[连接与输出](connections.md)说明；以下不包含内部 `_` 方法。

返回类型是源码注解，不代表服务端永远返回完整数据。

## 标准行情

源文件：`src/tdxman/client.py`。

### `TdxClient`

| 方法 | 当前签名 | 返回 |
| --- | --- | --- |
| `from_best_host` | `from_best_host(cls, hosts: list[str] | None=None, port: int | None=None, timeout: float | None=None, ping_timeout: float=5.0, auto_reconnect: bool=True, heartbeat_interval: float=15.0, refresh: bool=False)` | `'TdxClient'` |
| `ping_all` | `ping_all(hosts: list[str] | None=None, port: int | None=None, timeout: float=5.0)` | `list[tuple[str, float]]` |
| `connect` | `connect(self)` | `None` |
| `close` | `close(self)` | `None` |
| `disconnect` | `disconnect(self)` | `None` |
| `ensure_connected` | `ensure_connected(self)` | `None` |
| `get_security_count` | `get_security_count(self, market: Market)` | `int` |
| `get_security_list` | `get_security_list(self, market: Market, start: int)` | `pd.DataFrame` |
| `get_security_list_all` | `get_security_list_all(self, pages: int | str='all')` | `pd.DataFrame` |
| `get_security_quotes` | `get_security_quotes(self, stocks: list[tuple[Market, str]])` | `pd.DataFrame` |
| `get_price_limits` | `get_price_limits(self, market: Market, code: str, name: str, pre_close: float)` | `tuple[float | None, float | None]` |
| `get_security_bars` | `get_security_bars(self, market: Market, code: str, category: KlineCategory, start: int, count: int=800)` | `pd.DataFrame` |
| `get_index_bars` | `get_index_bars(self, market: Market, code: str, category: KlineCategory, start: int, count: int=800)` | `pd.DataFrame` |
| `get_minute_time_data` | `get_minute_time_data(self, market: Market, code: str)` | `pd.DataFrame` |
| `get_history_minute_time_data` | `get_history_minute_time_data(self, market: Market, code: str, date: int)` | `pd.DataFrame` |
| `get_transaction_data` | `get_transaction_data(self, market: Market, code: str, start: int, count: int=800)` | `pd.DataFrame` |
| `get_history_transaction_data` | `get_history_transaction_data(self, market: Market, code: str, date: int, start: int, count: int=800)` | `pd.DataFrame` |
| `get_xdxr_info` | `get_xdxr_info(self, market: Market, code: str)` | `pd.DataFrame` |
| `get_finance_info` | `get_finance_info(self, market: Market, code: str)` | `pd.DataFrame` |
| `get_company_info_category` | `get_company_info_category(self, market: Market, code: str)` | `pd.DataFrame` |
| `get_company_info_content` | `get_company_info_content(self, market: Market, code: str, filename: str, offset: int, length: int)` | `str` |
| `get_block_info` | `get_block_info(self, filename: str)` | `pd.DataFrame` |
| `get_report_file` | `get_report_file(self, filename: str)` | `bytes` |
| `get_financial_file_list` | `get_financial_file_list(self, host: str | None=None)` | `pd.DataFrame` |
| `get_financial_file` | `get_financial_file(self, filename: str, host: str | None=None)` | `bytes` |
| `get_financial_records` | `get_financial_records(self, filename: str, host: str | None=None)` | `pd.DataFrame` |
| `get_market_stat` | `get_market_stat(self)` | `pd.DataFrame` |
| `get_fund_flow` | `get_fund_flow(self, market: Market, code: str)` | `pd.DataFrame` |
| `get_history_fund_flow` | `get_history_fund_flow(self, market: Market, code: str, start: int, count: int)` | `pd.DataFrame` |

### `AsyncTdxClient`

| 方法 | 当前签名 | 返回 |
| --- | --- | --- |
| `from_best_host` | `from_best_host(cls, hosts: list[str] | None=None, port: int | None=None, timeout: float | None=None, ping_timeout: float=5.0, auto_reconnect: bool=True, heartbeat_interval: float=60.0, refresh: bool=False)` | `'AsyncTdxClient'` |
| `ping_all` | `ping_all(hosts: list[str] | None=None, port: int | None=None, timeout: float=5.0)` | `list[tuple[str, float]]` |
| `connect` | `async connect(self)` | `None` |
| `close` | `async close(self)` | `None` |
| `get_security_count` | `async get_security_count(self, market: Market)` | `int` |
| `get_security_list` | `async get_security_list(self, market: Market, start: int)` | `pd.DataFrame` |
| `get_security_list_all` | `async get_security_list_all(self, pages: int | str='all')` | `pd.DataFrame` |
| `get_security_quotes` | `async get_security_quotes(self, stocks: list[tuple[Market, str]])` | `pd.DataFrame` |
| `get_price_limits` | `async get_price_limits(self, market: Market, code: str, name: str, pre_close: float)` | `tuple[float | None, float | None]` |
| `get_security_bars` | `async get_security_bars(self, market: Market, code: str, category: KlineCategory, start: int, count: int=800)` | `pd.DataFrame` |
| `get_index_bars` | `async get_index_bars(self, market: Market, code: str, category: KlineCategory, start: int, count: int=800)` | `pd.DataFrame` |
| `get_minute_time_data` | `async get_minute_time_data(self, market: Market, code: str)` | `pd.DataFrame` |
| `get_history_minute_time_data` | `async get_history_minute_time_data(self, market: Market, code: str, date: int)` | `pd.DataFrame` |
| `get_transaction_data` | `async get_transaction_data(self, market: Market, code: str, start: int, count: int=800)` | `pd.DataFrame` |
| `get_history_transaction_data` | `async get_history_transaction_data(self, market: Market, code: str, date: int, start: int, count: int=800)` | `pd.DataFrame` |
| `get_xdxr_info` | `async get_xdxr_info(self, market: Market, code: str)` | `pd.DataFrame` |
| `get_finance_info` | `async get_finance_info(self, market: Market, code: str)` | `pd.DataFrame` |
| `get_company_info_category` | `async get_company_info_category(self, market: Market, code: str)` | `pd.DataFrame` |
| `get_company_info_content` | `async get_company_info_content(self, market: Market, code: str, filename: str, offset: int, length: int)` | `str` |
| `get_block_info` | `async get_block_info(self, filename: str)` | `pd.DataFrame` |
| `get_report_file` | `async get_report_file(self, filename: str)` | `bytes` |
| `get_financial_file_list` | `async get_financial_file_list(self, host: str | None=None)` | `pd.DataFrame` |
| `get_financial_file` | `async get_financial_file(self, filename: str, host: str | None=None)` | `bytes` |
| `get_financial_records` | `async get_financial_records(self, filename: str, host: str | None=None)` | `pd.DataFrame` |
| `get_market_stat` | `async get_market_stat(self)` | `pd.DataFrame` |
| `get_fund_flow` | `async get_fund_flow(self, market: Market, code: str)` | `pd.DataFrame` |
| `get_history_fund_flow` | `async get_history_fund_flow(self, market: Market, code: str, start: int, count: int)` | `pd.DataFrame` |

## MAC 行情

源文件：`src/tdxman/mac/client.py`。

### `MacClient`

| 方法 | 当前签名 | 返回 |
| --- | --- | --- |
| `from_best_host` | `from_best_host(cls, hosts: list[str] | None=None, port: int | None=None, timeout: float | None=None, ping_timeout: float=5.0, auto_reconnect: bool=True, heartbeat_interval: float=15.0, refresh: bool=False)` | `MacClient` |
| `ping_all` | `ping_all(hosts: list[str] | None=None, port: int | None=None, timeout: float=5.0)` | `list[tuple[str, float]]` |
| `connect` | `connect(self)` | `None` |
| `close` | `close(self)` | `None` |
| `disconnect` | `disconnect(self)` | `None` |
| `ensure_connected` | `ensure_connected(self)` | `None` |
| `get_stock_quotes` | `get_stock_quotes(self, stocks: list[tuple[int, str]], fields: object=None)` | `pd.DataFrame` |
| `get_stock_quotes_list` | `get_stock_quotes_list(self, category: Category, start: int=0, count: int=80, sort_type: SortType=SortType.CHANGE_PCT, sort_order: SortOrder=SortOrder.DESC, exclude_flags: list[FilterType] | None=None, fields: Fields | None=None)` | `pd.DataFrame` |
| `get_stock_kline` | `get_stock_kline(self, market: int, code: str, period: Period=Period.DAILY, start: int=0, count: int=800, times: int=1, adjust: Adjust=Adjust.NONE)` | `pd.DataFrame` |
| `get_minute_bars` | `get_minute_bars(self, market: int, code: str, *, date, since=None, max_pages=4, page_size=240, asset_type='stock')` | `pd.DataFrame` |
| `get_tick_chart` | `get_tick_chart(self, market: int, code: str, date: int | None=None)` | `pd.DataFrame` |
| `get_tick_charts` | `get_tick_charts(self, market: int, code: str, date: int | None=None, days: int=5)` | `pd.DataFrame` |
| `get_chart_sampling` | `get_chart_sampling(self, market: int, code: str)` | `pd.DataFrame` |
| `get_transactions` | `get_transactions(self, market: int, code: str, count: int=2000, start: int=0, date: int | None=None)` | `pd.DataFrame` |
| `get_symbol_info` | `get_symbol_info(self, market: int, code: str)` | `pd.DataFrame` |
| `get_board_list` | `get_board_list(self, board_type: BoardType=BoardType.ALL, count: int=10000)` | `pd.DataFrame` |
| `get_board_members` | `get_board_members(self, board_symbol: str, count: int=100000, sort_type: SortType=SortType.CHANGE_PCT, sort_order: SortOrder=SortOrder.DESC, fields: object=PresetField.COMMON, exclude_flags: list[FilterType] | None=None)` | `pd.DataFrame` |
| `get_belong_board` | `get_belong_board(self, market: int, code: str)` | `pd.DataFrame` |
| `get_capital_flow` | `get_capital_flow(self, market: int, code: str)` | `pd.DataFrame` |
| `get_auction` | `get_auction(self, market: int, code: str)` | `pd.DataFrame` |
| `get_unusual` | `get_unusual(self, market: int, start: int=0, count: int=0)` | `pd.DataFrame` |
| `get_server_info` | `get_server_info(self)` | `pd.DataFrame` |
| `get_kline_offset` | `get_kline_offset(self, offset: int=0, count: int=128000)` | `pd.DataFrame` |
| `get_file_meta` | `get_file_meta(self, filename: str)` | `pd.DataFrame` |
| `download_file_chunk` | `download_file_chunk(self, filename: str, index: int, offset: int, size: int)` | `bytes` |
| `download_file` | `download_file(self, filename: str, filesize: int=0)` | `bytearray` |
| `get_goods_list` | `get_goods_list(self, market: int, start: int=0, count: int=600)` | `pd.DataFrame` |

### `AsyncMacClient`

| 方法 | 当前签名 | 返回 |
| --- | --- | --- |
| `from_best_host` | `from_best_host(cls, hosts: list[str] | None=None, port: int | None=None, timeout: float | None=None, ping_timeout: float=5.0, auto_reconnect: bool=True, heartbeat_interval: float=15.0, refresh: bool=False)` | `AsyncMacClient` |
| `ping_all` | `ping_all(hosts: list[str] | None=None, port: int | None=None, timeout: float=5.0)` | `list[tuple[str, float]]` |
| `connect` | `async connect(self)` | `None` |
| `close` | `async close(self)` | `None` |
| `disconnect` | `async disconnect(self)` | `None` |
| `ensure_connected` | `async ensure_connected(self)` | `None` |
| `get_stock_quotes` | `async get_stock_quotes(self, stocks: list[tuple[int, str]], fields: object=None)` | `pd.DataFrame` |
| `get_stock_quotes_list` | `async get_stock_quotes_list(self, category: Category, start: int=0, count: int=80, sort_type: SortType=SortType.CHANGE_PCT, sort_order: SortOrder=SortOrder.DESC, exclude_flags: list[FilterType] | None=None, fields: Fields | None=None)` | `pd.DataFrame` |
| `get_stock_kline` | `async get_stock_kline(self, market: int, code: str, period: Period=Period.DAILY, start: int=0, count: int=800, times: int=1, adjust: Adjust=Adjust.NONE)` | `pd.DataFrame` |
| `get_minute_bars` | `async get_minute_bars(self, market: int, code: str, *, date, since=None, max_pages=4, page_size=240, asset_type='stock')` | `pd.DataFrame` |
| `get_tick_chart` | `async get_tick_chart(self, market: int, code: str, date: int | None=None)` | `pd.DataFrame` |
| `get_tick_charts` | `async get_tick_charts(self, market: int, code: str, date: int | None=None, days: int=5)` | `pd.DataFrame` |
| `get_chart_sampling` | `async get_chart_sampling(self, market: int, code: str)` | `pd.DataFrame` |
| `get_transactions` | `async get_transactions(self, market: int, code: str, count: int=2000, start: int=0, date: int | None=None)` | `pd.DataFrame` |
| `get_symbol_info` | `async get_symbol_info(self, market: int, code: str)` | `pd.DataFrame` |
| `get_board_list` | `async get_board_list(self, board_type: BoardType=BoardType.ALL, count: int=10000)` | `pd.DataFrame` |
| `get_board_members` | `async get_board_members(self, board_symbol: str, count: int=100000, sort_type: SortType=SortType.CHANGE_PCT, sort_order: SortOrder=SortOrder.DESC, fields: object=PresetField.COMMON, exclude_flags: list[FilterType] | None=None)` | `pd.DataFrame` |
| `get_belong_board` | `async get_belong_board(self, market: int, code: str)` | `pd.DataFrame` |
| `get_capital_flow` | `async get_capital_flow(self, market: int, code: str)` | `pd.DataFrame` |
| `get_auction` | `async get_auction(self, market: int, code: str)` | `pd.DataFrame` |
| `get_unusual` | `async get_unusual(self, market: int, start: int=0, count: int=0)` | `pd.DataFrame` |
| `get_server_info` | `async get_server_info(self)` | `pd.DataFrame` |
| `get_kline_offset` | `async get_kline_offset(self, offset: int=0, count: int=128000)` | `pd.DataFrame` |
| `get_file_meta` | `async get_file_meta(self, filename: str)` | `pd.DataFrame` |
| `download_file_chunk` | `async download_file_chunk(self, filename: str, index: int, offset: int, size: int)` | `bytes` |
| `download_file` | `async download_file(self, filename: str, filesize: int=0)` | `bytearray` |
| `get_goods_list` | `async get_goods_list(self, market: int, start: int=0, count: int=600)` | `pd.DataFrame` |

## 标准扩展行情

源文件：`src/tdxman/ex/client.py`。

### `ExTdxClient`

| 方法 | 当前签名 | 返回 |
| --- | --- | --- |
| `from_best_host` | `from_best_host(cls, hosts: list[str] | None=None, port: int | None=None, timeout: float=15.0, ping_timeout: float=5.0, auto_reconnect: bool=True, refresh: bool=False)` | `'ExTdxClient'` |
| `ping_all` | `ping_all(hosts: list[str] | None=None, port: int | None=None, timeout: float=5.0)` | `list[tuple[str, float]]` |
| `connect` | `connect(self)` | `None` |
| `close` | `close(self)` | `None` |
| `get_markets` | `get_markets(self)` | `list[ExMarketInfo]` |
| `get_instrument_count` | `get_instrument_count(self)` | `int` |
| `get_instrument_info` | `get_instrument_info(self, start: int, count: int=100)` | `list[ExInstrumentInfo]` |
| `get_instrument_quote` | `get_instrument_quote(self, market: int, code: str)` | `ExInstrumentQuote | None` |
| `get_instrument_quote_list` | `get_instrument_quote_list(self, market: int, category: int, start: int=0, count: int=80)` | `list[OrderedDict[str, object]]` |
| `get_instrument_bars` | `get_instrument_bars(self, category: int, market: int, code: str, start: int=0, count: int=700)` | `list[ExInstrumentBar]` |
| `get_history_instrument_bars_range` | `get_history_instrument_bars_range(self, market: int, code: str, start_date: int, end_date: int)` | `list[ExInstrumentBar]` |
| `get_minute_time_data` | `get_minute_time_data(self, market: int, code: str)` | `list[ExMinuteBar]` |
| `get_history_minute_time_data` | `get_history_minute_time_data(self, market: int, code: str, date: int)` | `list[ExMinuteBar]` |
| `get_transaction_data` | `get_transaction_data(self, market: int, code: str, start: int=0, count: int=1800)` | `list[ExTransactionRecord]` |
| `get_history_transaction_data` | `get_history_transaction_data(self, market: int, code: str, date: int, start: int=0, count: int=1800)` | `list[ExTransactionRecord]` |

### `AsyncExTdxClient`

| 方法 | 当前签名 | 返回 |
| --- | --- | --- |
| `from_best_host` | `from_best_host(cls, hosts: list[str] | None=None, port: int | None=None, timeout: float=15.0, ping_timeout: float=5.0, auto_reconnect: bool=True, heartbeat_interval: float=60.0, refresh: bool=False)` | `'AsyncExTdxClient'` |
| `ping_all` | `ping_all(hosts: list[str] | None=None, port: int | None=None, timeout: float=5.0)` | `list[tuple[str, float]]` |
| `connect` | `async connect(self)` | `None` |
| `close` | `async close(self)` | `None` |
| `get_markets` | `async get_markets(self)` | `list[ExMarketInfo]` |
| `get_instrument_count` | `async get_instrument_count(self)` | `int` |
| `get_instrument_info` | `async get_instrument_info(self, start: int, count: int=100)` | `list[ExInstrumentInfo]` |
| `get_instrument_quote` | `async get_instrument_quote(self, market: int, code: str)` | `ExInstrumentQuote | None` |
| `get_instrument_quote_list` | `async get_instrument_quote_list(self, market: int, category: int, start: int=0, count: int=80)` | `list[OrderedDict[str, object]]` |
| `get_instrument_bars` | `async get_instrument_bars(self, category: int, market: int, code: str, start: int=0, count: int=700)` | `list[ExInstrumentBar]` |
| `get_history_instrument_bars_range` | `async get_history_instrument_bars_range(self, market: int, code: str, start_date: int, end_date: int)` | `list[ExInstrumentBar]` |
| `get_minute_time_data` | `async get_minute_time_data(self, market: int, code: str)` | `list[ExMinuteBar]` |
| `get_history_minute_time_data` | `async get_history_minute_time_data(self, market: int, code: str, date: int)` | `list[ExMinuteBar]` |
| `get_transaction_data` | `async get_transaction_data(self, market: int, code: str, start: int=0, count: int=1800)` | `list[ExTransactionRecord]` |
| `get_history_transaction_data` | `async get_history_transaction_data(self, market: int, code: str, date: int, start: int=0, count: int=1800)` | `list[ExTransactionRecord]` |

## MAC 扩展行情

源文件：`src/tdxman/ex/mac_client.py`。

### `MacExClient`

| 方法 | 当前签名 | 返回 |
| --- | --- | --- |
| `from_best_host` | `from_best_host(cls, hosts: list[str] | None=None, port: int | None=None, timeout: float=15.0, ping_timeout: float=5.0, auto_reconnect: bool=True, refresh: bool=False)` | `'MacExClient'` |
| `ping_all` | `ping_all(hosts: list[str] | None=None, port: int | None=None, timeout: float=5.0)` | `list[tuple[str, float]]` |
| `connect` | `connect(self)` | `None` |
| `close` | `close(self)` | `None` |
| `disconnect` | `disconnect(self)` | `None` |
| `ensure_connected` | `ensure_connected(self)` | `None` |
| `goods_count` | `goods_count(self, market: int | None=None)` | `int` |
| `goods_list` | `goods_list(self, market: int, start: int=0, count: int=600)` | `pd.DataFrame` |
| `goods_quotes` | `goods_quotes(self, stocks: list[tuple[int, str]], fields: Any=None)` | `pd.DataFrame` |
| `goods_quotes_list` | `goods_quotes_list(self, market: int, start: int=0, count: int=100, sort_type: SortType=SortType.CODE, sort_order: SortOrder=SortOrder.NONE)` | `pd.DataFrame` |
| `goods_kline` | `goods_kline(self, market: int, code: str, period: Period=Period.DAILY, start: int=0, count: int=800, adjust: Adjust=Adjust.NONE)` | `pd.DataFrame` |
| `goods_tick_chart` | `goods_tick_chart(self, market: int, code: str, query_date: date | None=None)` | `pd.DataFrame` |
| `goods_chart_sampling` | `goods_chart_sampling(self, market: int, code: str)` | `pd.DataFrame` |
| `goods_transaction` | `goods_transaction(self, market: int, code: str, query_date: date | None=None, start: int=0, count: int=2000)` | `pd.DataFrame` |

### `AsyncMacExClient`

| 方法 | 当前签名 | 返回 |
| --- | --- | --- |
| `from_best_host` | `from_best_host(cls, hosts: list[str] | None=None, port: int | None=None, timeout: float=15.0, ping_timeout: float=5.0, auto_reconnect: bool=True, heartbeat_interval: float=60.0, refresh: bool=False)` | `'AsyncMacExClient'` |
| `ping_all` | `ping_all(hosts: list[str] | None=None, port: int | None=None, timeout: float=5.0)` | `list[tuple[str, float]]` |
| `connect` | `async connect(self)` | `None` |
| `close` | `async close(self)` | `None` |
| `goods_count` | `async goods_count(self, market: int | None=None)` | `int` |
| `goods_list` | `async goods_list(self, market: int, start: int=0, count: int=600)` | `pd.DataFrame` |
| `goods_quotes` | `async goods_quotes(self, stocks: list[tuple[int, str]], fields: Any=None)` | `pd.DataFrame` |
| `goods_quotes_list` | `async goods_quotes_list(self, market: int, start: int=0, count: int=100, sort_type: SortType=SortType.CODE, sort_order: SortOrder=SortOrder.NONE)` | `pd.DataFrame` |
| `goods_kline` | `async goods_kline(self, market: int, code: str, period: Period=Period.DAILY, start: int=0, count: int=800, adjust: Adjust=Adjust.NONE)` | `pd.DataFrame` |
| `goods_tick_chart` | `async goods_tick_chart(self, market: int, code: str, query_date: date | None=None)` | `pd.DataFrame` |
| `goods_chart_sampling` | `async goods_chart_sampling(self, market: int, code: str)` | `pd.DataFrame` |
| `goods_transaction` | `async goods_transaction(self, market: int, code: str, query_date: date | None=None, start: int=0, count: int=2000)` | `pd.DataFrame` |

## 统一客户端

源文件：`src/tdxman/unified.py`。

### `UnifiedTdxClient`

| 方法 | 当前签名 | 返回 |
| --- | --- | --- |
| `connect` | `connect(self)` | `None` |
| `close` | `close(self)` | `None` |
| `disconnect` | `disconnect(self)` | `None` |
| `get_stock_quotes` | `get_stock_quotes(self, stocks: list[tuple[int, str]], fields: object=None)` | `pd.DataFrame` |
| `get_stock_quotes_list` | `get_stock_quotes_list(self, category: Category, start: int=0, count: int=80, sort_type: SortType=SortType.CHANGE_PCT, sort_order: SortOrder=SortOrder.DESC, exclude_flags: list[FilterType] | None=None, fields: object=None)` | `pd.DataFrame` |
| `get_stock_kline` | `get_stock_kline(self, market: int, code: str, period: Period=Period.DAILY, start: int=0, count: int=800, times: int=1, adjust: Adjust=Adjust.NONE)` | `pd.DataFrame` |
| `get_tick_chart` | `get_tick_chart(self, market: int, code: str, date: int | None=None)` | `pd.DataFrame` |
| `get_tick_charts` | `get_tick_charts(self, market: int, code: str, date: int | None=None, days: int=5)` | `pd.DataFrame` |
| `get_chart_sampling` | `get_chart_sampling(self, market: int, code: str)` | `pd.DataFrame` |
| `get_transactions` | `get_transactions(self, market: int, code: str, count: int=2000, start: int=0, date: int | None=None)` | `pd.DataFrame` |
| `get_symbol_info` | `get_symbol_info(self, market: int, code: str)` | `pd.DataFrame` |
| `get_board_list` | `get_board_list(self, board_type: BoardType=BoardType.ALL, count: int=10000)` | `pd.DataFrame` |
| `get_board_members` | `get_board_members(self, board_symbol: str, count: int=100000, sort_type: SortType=SortType.CHANGE_PCT, sort_order: SortOrder=SortOrder.DESC, fields: object=None, exclude_flags: list[FilterType] | None=None)` | `pd.DataFrame` |
| `get_belong_board` | `get_belong_board(self, market: int, code: str)` | `pd.DataFrame` |
| `get_capital_flow` | `get_capital_flow(self, market: int, code: str)` | `pd.DataFrame` |
| `get_auction` | `get_auction(self, market: int, code: str)` | `pd.DataFrame` |
| `get_unusual` | `get_unusual(self, market: int, start: int=0, count: int=0)` | `pd.DataFrame` |
| `get_server_info` | `get_server_info(self)` | `pd.DataFrame` |
| `get_kline_offset` | `get_kline_offset(self, offset: int=0, count: int=128000)` | `pd.DataFrame` |
| `get_file_meta` | `get_file_meta(self, filename: str)` | `pd.DataFrame` |
| `download_file_chunk` | `download_file_chunk(self, filename: str, index: int, offset: int, size: int)` | `bytes` |
| `download_file` | `download_file(self, filename: str, filesize: int=0)` | `bytearray` |
| `get_goods_list` | `get_goods_list(self, market: int, start: int=0, count: int=600)` | `pd.DataFrame` |
| `goods_count` | `goods_count(self, market: int)` | `int` |
| `goods_list` | `goods_list(self, market: int, start: int=0, count: int=600)` | `pd.DataFrame` |
| `goods_quotes` | `goods_quotes(self, stocks: list[tuple[int, str]], fields: Any=None)` | `pd.DataFrame` |
| `goods_quotes_list` | `goods_quotes_list(self, market: int, start: int=0, count: int=100, sort_type: SortType=SortType.CODE, sort_order: SortOrder=SortOrder.NONE)` | `pd.DataFrame` |
| `goods_kline` | `goods_kline(self, market: int, code: str, period: Period=Period.DAILY, start: int=0, count: int=800, adjust: Adjust=Adjust.NONE)` | `pd.DataFrame` |
| `goods_tick_chart` | `goods_tick_chart(self, market: int, code: str, query_date: object=None)` | `pd.DataFrame` |
| `goods_chart_sampling` | `goods_chart_sampling(self, market: int, code: str)` | `pd.DataFrame` |
| `goods_transaction` | `goods_transaction(self, market: int, code: str, query_date: object=None, start: int=0, count: int=2000)` | `pd.DataFrame` |

### `AsyncUnifiedTdxClient`

| 方法 | 当前签名 | 返回 |
| --- | --- | --- |
| `connect` | `async connect(self)` | `None` |
| `close` | `async close(self)` | `None` |
| `disconnect` | `async disconnect(self)` | `None` |
| `get_stock_quotes` | `async get_stock_quotes(self, stocks: list[tuple[int, str]], fields: object=None)` | `pd.DataFrame` |
| `get_stock_quotes_list` | `async get_stock_quotes_list(self, category: Category, start: int=0, count: int=80, sort_type: SortType=SortType.CHANGE_PCT, sort_order: SortOrder=SortOrder.DESC, exclude_flags: list[FilterType] | None=None, fields: object=None)` | `pd.DataFrame` |
| `get_stock_kline` | `async get_stock_kline(self, market: int, code: str, period: Period=Period.DAILY, start: int=0, count: int=800, times: int=1, adjust: Adjust=Adjust.NONE)` | `pd.DataFrame` |
| `get_tick_chart` | `async get_tick_chart(self, market: int, code: str, date: int | None=None)` | `pd.DataFrame` |
| `get_tick_charts` | `async get_tick_charts(self, market: int, code: str, date: int | None=None, days: int=5)` | `pd.DataFrame` |
| `get_chart_sampling` | `async get_chart_sampling(self, market: int, code: str)` | `pd.DataFrame` |
| `get_transactions` | `async get_transactions(self, market: int, code: str, count: int=2000, start: int=0, date: int | None=None)` | `pd.DataFrame` |
| `get_symbol_info` | `async get_symbol_info(self, market: int, code: str)` | `pd.DataFrame` |
| `get_board_list` | `async get_board_list(self, board_type: BoardType=BoardType.ALL, count: int=10000)` | `pd.DataFrame` |
| `get_board_members` | `async get_board_members(self, board_symbol: str, count: int=100000, sort_type: SortType=SortType.CHANGE_PCT, sort_order: SortOrder=SortOrder.DESC, fields: object=None, exclude_flags: list[FilterType] | None=None)` | `pd.DataFrame` |
| `get_belong_board` | `async get_belong_board(self, market: int, code: str)` | `pd.DataFrame` |
| `get_capital_flow` | `async get_capital_flow(self, market: int, code: str)` | `pd.DataFrame` |
| `get_auction` | `async get_auction(self, market: int, code: str)` | `pd.DataFrame` |
| `get_unusual` | `async get_unusual(self, market: int, start: int=0, count: int=0)` | `pd.DataFrame` |
| `get_server_info` | `async get_server_info(self)` | `pd.DataFrame` |
| `get_kline_offset` | `async get_kline_offset(self, offset: int=0, count: int=128000)` | `pd.DataFrame` |
| `get_file_meta` | `async get_file_meta(self, filename: str)` | `pd.DataFrame` |
| `download_file_chunk` | `async download_file_chunk(self, filename: str, index: int, offset: int, size: int)` | `bytes` |
| `download_file` | `async download_file(self, filename: str, filesize: int=0)` | `bytearray` |
| `get_goods_list` | `async get_goods_list(self, market: int, start: int=0, count: int=600)` | `pd.DataFrame` |
| `goods_count` | `async goods_count(self, market: int)` | `int` |
| `goods_list` | `async goods_list(self, market: int, start: int=0, count: int=600)` | `pd.DataFrame` |
| `goods_quotes` | `async goods_quotes(self, stocks: list[tuple[int, str]], fields: Any=None)` | `pd.DataFrame` |
| `goods_quotes_list` | `async goods_quotes_list(self, market: int, start: int=0, count: int=100, sort_type: SortType=SortType.CODE, sort_order: SortOrder=SortOrder.NONE)` | `pd.DataFrame` |
| `goods_kline` | `async goods_kline(self, market: int, code: str, period: Period=Period.DAILY, start: int=0, count: int=800, adjust: Adjust=Adjust.NONE)` | `pd.DataFrame` |
| `goods_tick_chart` | `async goods_tick_chart(self, market: int, code: str, query_date: object=None)` | `pd.DataFrame` |
| `goods_chart_sampling` | `async goods_chart_sampling(self, market: int, code: str)` | `pd.DataFrame` |
| `goods_transaction` | `async goods_transaction(self, market: int, code: str, query_date: object=None, start: int=0, count: int=2000)` | `pd.DataFrame` |

## 辅助服务

源文件：`src/tdxman/f10.py`。

### `F10Response`

| 方法 | 当前签名 | 返回 |
| --- | --- | --- |
| `to_frame` | `to_frame(self)` | `pd.DataFrame` |

### `F10Client`

| 方法 | 当前签名 | 返回 |
| --- | --- | --- |
| `query` | `query(self, market: Market, code: str, section: str='profile', *, report_type: str='zcfzb', report_date: str | None=None)` | `F10Response` |

## 交易日

源文件：`src/tdxman/workdays.py`。

### `WorkdayService`

| 方法 | 当前签名 | 返回 |
| --- | --- | --- |
| `refresh` | `refresh(self)` | `int` |
| `coverage` | 只读属性 | `tuple[date, date]` |
| `is_workday` | `is_workday(self, value: date | datetime | str)` | `bool | None` |
| `next_workday` | `next_workday(self, value: date | datetime | str, *, include_self: bool=False)` | `date | None` |
| `previous_workday` | `previous_workday(self, value: date | datetime | str, *, include_self: bool=False)` | `date | None` |
| `range` | `range(self, start: date | datetime | str, end: date | datetime | str)` | `list[date]` |

## BaoStock 补充

源文件：`src/tdxman/baostock.py`。

### `BaostockClient`

| 方法 | 当前签名 | 返回 |
| --- | --- | --- |
| `security_code` | `security_code(market: int | Market, code: str)` | `str` |
| `get_daily` | `get_daily(self, market: int | Market, code: str, *, start: date | None=None, end: date | None=None, count: int | None=30)` | `pd.DataFrame` |
| `get_stock_basic` | `get_stock_basic(self, market: int | Market, code: str)` | `pd.DataFrame` |
| `get_trade_calendar` | `get_trade_calendar(self, start: date, end: date)` | `pd.DataFrame` |

## 离线模块公开函数

`tdxman.offline` 导出的读取和定位函数均在[本地文件](reader/README.md)给出逐项说明。

## Tick Stock Panel 适配器

源文件：`src/tdxman/adapters/tick_stock_panel/provider.py`；返回值与配置见[适配器契约](services/tick-stock-panel.md)。

### `TdxmanProvider`

| 方法 | 当前签名 | 返回 |
| --- | --- | --- |
| `close` | `close(self)` | `None` |
| `check` | `check(self)` | `tuple[bool, str]` |
| `get_daily` | `get_daily(self, symbols: object, start_time: object | None=None, end_time: object | None=None, asset_type: str='stock', on_chunk_done: ChunkDone | None=None)` | `Any`（Polars DataFrame） |
| `iter_daily` | `iter_daily(self, symbols: object, start_time: object | None=None, end_time: object | None=None, asset_type: str='stock', on_chunk_done: ChunkDone | None=None)` | `Iterator[Any]`（Polars DataFrame 分块） |
| `test_dataset` | `test_dataset(self, dataset: str, symbols: object | None=None)` | `dict[str, Any]` |
| `get_instruments` | `get_instruments(self, asset_type: str='stock')` | `list[dict[str, Any]]` |
| `get_realtime` | `get_realtime(self)` | `list[dict[str, Any]]` |
| `get_realtime_indices` | `get_realtime_indices(self, symbols: object)` | `list[dict[str, Any]] | None` |
| `get_minute` | `get_minute(self, symbols: object, start_time: object | None=None, end_time: object | None=None, asset_type: str='stock', freq: str='1m', on_chunk_done: ChunkDone | None=None)` | `Any`（Polars DataFrame） |
| `get_intraday_batch` | `get_intraday_batch(self, symbols: object, count: int=300, asset_type: str='stock')` | `Any`（Polars DataFrame） |
| `get_intraday_latest` | `get_intraday_latest(self, symbols: object | None=None, count: int=3)` | `Any`（Polars DataFrame） |
| `get_depth_batch` | `get_depth_batch(self, symbols: object)` | `dict[str, dict[str, Any]]` |
