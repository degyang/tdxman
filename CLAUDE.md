# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build / Test / Lint

```bash
# 单元测试（无需网络，使用 tests/fixtures/ 中的 hex 数据）
python -m pytest tests/unit/ -v

# 集成测试（需要网络，默认跳过）
XMTDX_LIVE=1 python -m pytest tests/integration/ -v

# 类型检查（strict mypy）
mypy src/

# lint + format
ruff check src/ tests/
ruff format --check src/ tests/
```

## 架构

```
src/tdxman/
├── client.py          # TdxClient / AsyncTdxClient（高层 API）
├── transport/
│   ├── sync.py        # TdxConnection（socket）+ ping_host / ping_all
│   └── async_.py      # AsyncTdxConnection（asyncio）
├── commands/          # 每条命令：build_request() + parse_response()，无 IO
├── codec/             # price / volume / datetime / frame 编解码
└── models/            # 纯 dataclass，无业务逻辑
```

commands 层不依赖 transport，可独立单测。修改 codec 或 commands 时不需要网络。

## 协议编解码注意事项

- **价格编码**：变长有符号整数（类 LEB128），bit8=继续，bit7=符号。差分编码（相邻 tick 存 delta）。
- **成交量编码**：4 字节自定义浮点（`_decode_volume`），字节 3=指数，字节 0-2=精度。**不可用于价格字段**。
- **握手**：连接后必须顺序发送 3 条 setup 命令，响应丢弃。
- **帧格式**：16 字节响应头，body 按需 zlib 解压。
- 新增编解码逻辑时务必在 `tests/fixtures/` 中补充 hex fixture 并编写对应的离线解析测试。

## 已知限制

- `Market.BJ` 的 `get_security_list()` 不能稳定获取（服务器端问题），不要尝试依赖它。
- `limit_up` / `limit_down` 在 `SecurityQuote` 中默认为 `None`，涨跌停价应通过 `get_price_limits()` 或 `compute_price_limits()` 计算。

## 常用行情查询

### 服务器测速与优选

```bash
tdxman ping --timeout 3
```

`ping` 只展示候选服务器的测速结果，不会保存最佳地址。`TdxClient.from_best_host()`、`MacClient.from_best_host()` 等 Python 工厂方法会选择最低延迟的可用服务器，并将最佳地址保存到 `~/.tdxman/config.json`。候选 IP 池默认维护在 `config.py`；本地配置文件或 `TDXMAN_KNOWN_HOSTS` 可覆盖标准行情候选池。

### 行业板块与行业日 K

```bash
# 通达信一级、二级行业板块完整目录
tdxman board-list --type HY --format csv
tdxman board-list --type HY2 --format csv

# 使用目录返回的 market、code 查询行业板块日 K，例如 market=SH、code=881165
tdxman kline SH 881165 --period DAILY --count 250 --format csv
```

`board-list` 返回行业板块的 `market`、`code`、`name` 等字段。CLI 的 `kline` 走 MAC 协议的 `MacClient.get_stock_kline()`，可查询行业板块代码；不要改用标准协议的 `TdxClient.get_index_bars()` 查询此类 `88xxxx` 板块代码，该接口可能返回空响应。

### 实时指标与日 K 衍生指标

```bash
tdxman quote "SH 600519"
```

实时 `quote` 默认包含 `vol_ratio`（量比）、`turnover`（换手率）、`vol`（成交量）和 `float_shares`（流通股本）。日 K 不包含 `vol_ratio` 或 `turnover`，但收盘后可推算：

```python
# vol 的单位为手；float_shares 的单位为万股，结果为百分比数值。
bars["turnover_pct"] = bars["vol"] / float_shares

# 收盘量比：当日成交量 / 前 5 个交易日平均成交量。
bars = bars.sort_values("datetime")
bars["vol_ratio_close"] = bars["vol"] / bars["vol"].shift(1).rolling(5).mean()
```

历史换手率应按流通股本变化（解禁、增发、送配、拆合股等）的生效日期分段计算。盘后 `vol_ratio_close` 是基于完整日成交量的收盘口径；盘中实时量比还需要同一时刻的历史分时累计成交量，不能只由日 K 严格复原。

## 代码风格

- ruff: line-length 100, target py310, rules: E/F/I/UP
- mypy strict mode
- 所有 `get_*` 公开方法返回 `pd.DataFrame`（通过 `_df._to_df()` 转换）。内部方法仍使用 dataclass 列表。
- 依赖：pandas（>=2.0）、tzdata（>=2024.1）。
