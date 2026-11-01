# `tdxman` CLI

[API 总入口](../README.md) · [31 个命令逐项说明](commands.md) · [全部参数与默认值](options.md)

安装项目后运行 `tdxman --help`，或运行 `tdxman <command> --help` 查看当前安装版本的选项。`tdxman ex <command>` 是扩展市场子命令。CLI 会按命令调用 [标准行情](../hq/README.md)、[MAC 行情](../mac/README.md)、[MAC 扩展行情](../exhq/mac.md)、[离线读取](../reader/README.md)或[辅助服务](../services/README.md)；同名数据不保证使用同一个 API。例如普通 `kline` 用 MAC，而标准指数 `kline` 用标准 7709。

## 标的与市场写法

单只 A 股推荐 `600519.SH` 或 `000001.SZ`；旧格式 `SH 600519` 也支持。批量 `quote` 以逗号分隔，如 `"000001.SZ,600519.SH"`。上述单标的行情命令中，纯六位代码缺市场会报错。股票目录用 [`tdxman stock-codes`](commands/market/stock-codes.md)，其 `--code` 筛选支持纯六位代码。扩展市场命令使用 `MARKET CODE`，例如 `HK_MAIN_BOARD 00700`；先用 `tdxman ex markets` 和 `tdxman ex quote-list MARKET` 查询代码范围。`tdxman markets`、`tdxman ex markets` 是本地枚举，不会连接服务器。

```bash
tdxman quote "000001.SZ,600519.SH" --format table
tdxman kline 600519.SH --period DAILY --count 30 --adjust QFQ --format csv
tdxman ex quote HK_MAIN_BOARD 00700 --format json
```

## 共用输出规则

大多数数据命令提供 `--format json|table|csv`（默认 JSON）和 `--output PATH`。有 `--fields basic|all` 的命令默认 `basic`，只筛选**表格**列；JSON/CSV 始终写全部列。`f10` 与 `workdays` 也提供格式和输出路径，但没有 `--fields`；`bestip`、`version` 是纯文本。JSON 是记录数组，空结果为 `[]`，没有 REST 响应信封。表格输出到终端是网格文本，写到 `.md` 文件时是 Markdown 表格；CSV 文件用 `.csv`，JSON 用 `.json`。数字显示会按字段四舍五入，需保留原始精度时调用 Python API。

`--output` 指向有后缀的文件时，后缀必须与格式一致；无后缀路径视为目录，按标的或命令名自动命名。多标的 `quote` 必须输出到目录，会按代码拆分；同一六位代码跨市场时还需检查每行 `market`。命令将数据写到 stdout，进度与错误可写到 stderr。具体的默认文件名与选项见[命令逐项说明](commands.md)。

## 日期与偏移

`--start` 通常是从最新记录往前的条数偏移，**不是日期**；`--date` 在股票分时/逐笔命令通常是 `YYYYMMDD`，BaoStock 的 `--start-date/--end-date` 是 `YYYY-MM-DD`。`workdays` 接受两种日期字符串。`tdxman ex tick --date` 当前存在整数传给 `datetime.date` API 的已知错误，见[该命令页](commands/ex/tick.md)。在线返回可能包含当日未完成记录，离线结果则只反映本机已下载文件。

源码：`src/tdxman/cli/__init__.py`、`parsers.py`、`output.py` 和各 `cmd_*.py`。这些页面依据当前源码整理；没有对外部实时服务器逐命令执行验收。
