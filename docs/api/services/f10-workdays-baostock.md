# F10、交易日与 BaoStock

[辅助服务](README.md) · [精确签名](../signatures.md)

## F10 公司页面

`F10Client(base_url='http://static.tdx.com.cn:7615/TQLEX', timeout=8.0)` 使用独立 HTTP 接口，不需要 7709 连接。`query(market, code, section='profile', *, report_type='zcfzb', report_date=None)` 返回 `F10Response(entry, tables, raw)`；`to_frame()` 把各表行展开为 DataFrame，并增加 `result_set` 来源列。支持栏目：`profile`、`report`、`business`、`dividends`、`shareholders`、`topics`、`announcements`、`news`、`governance`、`forecast`。`business` 若未指定 `report_date`，会先查询可用报告期；证券必须是与 `market` 匹配的六位数字代码。

```python
from tdxman import F10Client, Market

response = F10Client().query(Market.SH, "600519", "profile")
frame = response.to_frame()
```

## 已观测交易日

`WorkdayService(client, max_bars=10000)` 从传入 `MacClient` 的上证指数 `000001` 日 K 线生成日期序列。`refresh()` 重读并返回天数；`coverage` 给出首末日期；`is_workday(value)` 返回 `True/False`，超出覆盖时返回 `None`；`next_workday`、`previous_workday` 超出覆盖或无对应日时返回 `None`；`range(start, end)` 返回闭区间内交易日，超出覆盖会抛 `ValueError`。日期接受 `date`、`datetime`、`YYYY-MM-DD` 或 `YYYYMMDD` 字符串。

```python
from tdxman import MacClient, WorkdayService

with MacClient.from_best_host() as client:
    calendar = WorkdayService(client)
    print(calendar.coverage, calendar.is_workday("2026-09-30"))
```

覆盖只反映成功取到的指数 K 线，不是未来交易所日历。

## BaoStock 补充数据

`BaostockClient(timeout=15)` 需要单独安装并连接 BaoStock SDK。`security_code(market, code)` 把明确的沪深 A 股市场与代码转为 `sh.600519` 等标识，北交所和不匹配前缀会抛 `ValueError`。在上下文中调用 `get_daily(market, code, *, start=None, end=None, count=30)` 获取**未复权**日线、交易状态和 ST 状态；若需整个起止区间，传 `start` 且设 `count=None`。日线结果含 `date/symbol/market/code`、OHLC、`pre_close`、成交量额、估值指标、`is_st`、`trading_status`、`source`、`fetched_at`，`attrs` 记录价格调整、量和金额单位。`get_stock_basic(market, code)` 获取证券基础信息；`get_trade_calendar(start, end)` 获取交易日历，后两者也返回 DataFrame。其异常是 `tdxman.baostock.BaostockError`。BaoStock 使用进程全局会话，同一进程中避免并行直接操作 SDK。

```python
from datetime import date
from tdxman import Market
from tdxman.baostock import BaostockClient

with BaostockClient() as client:
    daily = client.get_daily(Market.SH, "600519", start=date(2026, 9, 1),
                             end=date(2026, 9, 30))
```

源码：`src/tdxman/f10.py`、`src/tdxman/workdays.py`、`src/tdxman/baostock.py`。
