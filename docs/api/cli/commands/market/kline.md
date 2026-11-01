# `tdxman kline`

## 前置条件与命令

在已安装本项目 CLI 的环境中，先运行 `tdxman kline --help` 核对当前选项。`tdxman` 不提供参考项目里的 `schema` 命令。显式指定市场，避免将相同六位代码的股票与指数混淆。

```bash
tdxman kline 600519.SH --period DAILY --count 30 --format table
tdxman kline 000300.SH --period DAILY --count 120 --format json --output output/000300.json
tdxman kline 600519.SH --source baostock --period DAILY --adjust NONE --count 30 --format csv --output output/600519.csv
```

兼容旧写法 `tdxman kline SH 600519`。沪深标准指数代码先由 `tdxman board-list --type ZS` 或 `tdxman quote-list ZS` 发现；中证、国证、香港等扩展市场指数先由 `tdxman ex quote-list MARKET` 发现，再用 `tdxman ex kline MARKET CODE`。

## 参数选择

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `SYMBOL_OR_MARKET [CODE]` | 必填 | 推荐 `600519.SH` / `000001.SZ`；也接受 `SH 600519`。纯六位代码缺少市场会报错。`000001.SH` 是上证指数，`000001.SZ` 是平安银行。 |
| `--period` | `DAILY` | `DAILY`, `WEEKLY`, `MONTHLY`, `YEARLY`, `1MIN`, `5MIN`, `15MIN`, `30MIN`, `60MIN`。 |
| `--count` | `800` | 从 `--start` 起请求的 K 线数量；建议按所需范围设置，避免默认输出过大。 |
| `--start` | `0` | 距最新 K 线的记录偏移，不是日期；向更早历史翻页时增大。 |
| `--adjust` | `NONE` | `NONE`, `QFQ`, `HFQ`；标准指数只支持 `NONE`。 |
| `--source` | `tdx` | `tdx` 或 `baostock`。BaoStock 仅支持显式市场的沪深 A 股、`DAILY`、`NONE`；不支持北交所或指数。 |
| `--format` / `--output` | `json` / stdout | 格式为 `json`, `table`, `csv`。目录路径按代码写文件；单文件扩展名须分别为 `.json`, `.md`, `.csv`。 |
| `--fields` | `basic` | 仅控制终端/文件表格的列；`--fields all` 显示表格全部字段，JSON 和 CSV 始终包含全部字段。 |

## CLI 与 Python API 的对应关系

| CLI 路径 | 实际读取 | 使用时注意 |
| --- | --- | --- |
| 普通股票，默认 `--source tdx` | `MacClient.get_stock_kline(market, code, period=..., start=..., count=..., adjust=...)` | 返回 DataFrame，MAC 客户端每页最多取 700 根并自动分页，CLI 的 `count` 可大于单页。支持前复权和后复权；API 还有 `times` 参数，CLI 不暴露。 |
| 沪深标准指数，默认 `--source tdx` | `TdxClient.get_index_bars(market, code, category, start, count)` | CLI 按市场和代码前缀路由：上海 `000`/`880`/`999`，深圳 `399`。标准协议单次最多 800 根，长历史需按 `start` 分次请求；指数不复权。返回的 `up_count`/`down_count` 是该指数自身统计口径的上涨/下跌家数。 |
| `--source baostock` | `BaostockClient.get_daily(market, code, count=count+start)`，随后在本地按 `start` 截取 | 返回未复权日线，含明确上游标记的停牌日；偏移仍从最新记录计算。它是补充源，结果列包含 `trading_status`、`is_st`、`source` 等，与 TDX K 线列不完全相同。 |

`docs/api/hq/market.md` 中的 `get_security_bars` 是标准协议股票 API；本 CLI 的默认普通股票路径实际调用 MAC API，不要据该签名推断 CLI 自动分页或输出列。[项目 README](../../../../../README.md) 的 Python API 小节记录 MAC 用法和常见列。

## 分页、输出与验收

- TDX 股票 `count` 由客户端内部分页；标准指数每次最多取 800 根。跨多个指数窗口时用 `--start` 递增，并按返回的日期/时间检查是否重复或缺口，不按自然日推断交易日。
- `--start 0` 可能包含当前尚未收盘的日线。研究收盘数据时核对最新一根日期与收盘状态；仅有返回行数不代表窗口完整。
- JSON 是记录数组，不是参考项目的响应 envelope。空结果为 `[]`；保存大结果后检查实际文件、行数、首末时间和所需列。表格默认只保留常用字段，核对完整列时用 JSON、CSV 或 `--fields all`。
- 命令报参数错误时先看 `tdxman kline --help`。`BaoStock 补充接口仅支持 --period DAILY --adjust NONE`、不支持市场/证券代码、或指数不支持复权，均应改参数或改命令路径；连接失败才检查服务器，不把空结果解释为非交易日。

## 项目依据

- CLI：`src/tdxman/cli/cmd_kline.py`、`src/tdxman/cli/help.py`、`src/tdxman/cli/output.py`
- API：`src/tdxman/mac/client.py#get_stock_kline`、`src/tdxman/client.py#get_index_bars`、`src/tdxman/baostock.py#get_daily`
- 文档：[项目 README](../../../../../README.md)「CLI 参考」「Python API」、`docs/api/mac/market.md` 的 K 线说明

[CLI 总入口](../../README.md) · [命令索引](../../commands.md)
