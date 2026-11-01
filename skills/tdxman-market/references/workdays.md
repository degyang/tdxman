# `tdxman workdays`

```bash
tdxman workdays --date 2026-09-29 --format table
tdxman workdays --start 2026-09-01 --end 2026-09-30 --format csv --output output/workdays.csv
```

单日 `--date` 默认当前上海日期，支持 `YYYY-MM-DD` / `YYYYMMDD`；范围查询必须同时指定 `--start`、`--end`，不能与 `--date` 并用，起止日期跨度不得超过 3660 天。`--max-bars` 默认 10000，范围 1–15000，控制读取的上证指数日 K 根数，因而控制可判断的历史覆盖。此命令没有 `--fields`。

CLI 用 `WorkdayService(MacClient, max_bars=...)` 从上证指数 `000001.SH` 日 K 建立已观察到的交易日集合。单日结果带 `is_open`、前后已观察交易日及覆盖边界；超出覆盖范围时 `is_open: null`。范围超出覆盖边界则报错；范围内未观察到的日期标为休市，前提是指数数据本身完整。它不是未来节假日预测服务。结果的 `source` 是 `tdx:index:kline`。CLI：`src/tdxman/cli/cmd_workdays.py`；API：`src/tdxman/workdays.py#WorkdayService`。
