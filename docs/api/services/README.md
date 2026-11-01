# 辅助服务

[总入口](../README.md) · [签名总表](../signatures.md)

- [统一客户端](unified.md)：把 MAC A 股和 MAC 扩展市场方法放在同一个上下文中。
- [F10、交易日、BaoStock](f10-workdays-baostock.md)：补充页面、已观测交易日与历史日线状态。
- [Tick Stock Panel 适配器](tick-stock-panel.md)：Polars 规范化日线、分钟、实时与五档接口。

这些服务不是 `TdxClient` 的继承扩展：`F10Client` 使用独立的 HTTP TQLEX 入口，`WorkdayService` 读取 MAC 上证指数日线，`BaostockClient` 使用 BaoStock SDK。选择数据源时保留来源及取数时间。
