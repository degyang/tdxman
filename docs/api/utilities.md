# 标识、配置与协议工具

[API 总入口](README.md) · [连接契约](connections.md)

这些工具主要从各自子模块导入；包根仅重导出 `ping_all`、`ping_mac_all`、服务器常量、`save_best_host` 和 `save_best_ex_host`。业务客户端内部也会使用其中一些工具。

## 证券标识：`tdxman.symbol`

| 函数 | 契约 |
| --- | --- |
| `parse_symbol(s)` | 接受 `000001.SZ`、`SZ.000001`、`SZ 000001` 等显式市场格式，返回 `(code, market)` 字符串元组；纯六位代码抛 `SymbolError` |
| `parse_symbols(s)` | 以逗号分隔并逐项解析，跳过空项 |
| `format_symbol(code, market)` / `format_symbols(pairs)` | 拼出 `CODE.MARKET`；仅格式化，不执行证券有效性核验 |
| `guess_market(code)` | 按代码前缀猜测，可能与实际证券身份不符 |
| `is_valid_symbol(s)` | 格式校验布尔值，不查询交易所目录 |

```python
from tdxman.symbol import parse_symbol

code, market = parse_symbol("600519.SH")
```

## 服务器配置：`tdxman.config` 与 `tdxman.bestip`

`tdxman.config` 的公开配置函数如下。环境变量、`~/.tdxman/config.json` 和内置默认值的优先关系见[连接契约](connections.md)。

| 函数 | 返回/作用 |
| --- | --- |
| `get_best_host()` / `get_best_standard_endpoint()` | 标准行情主机，或优选 `(host, port)` / `None` |
| `get_best_mac_host()` / `get_best_mac_endpoint()` | MAC 行情主机，或优选端点 |
| `get_best_ex_host()` / `get_best_ex_endpoint()` | 标准扩展行情主机，或优选端点 |
| `get_best_mac_ex_host()` / `get_best_mac_ex_endpoint()` | MAC 扩展行情主机，或优选端点 |
| `get_known_hosts()` / `get_mac_hosts()` | 标准/MAC 候选主机列表 |
| `get_ex_hosts()` / `get_mac_ex_hosts()` | 标准/MAC 扩展候选主机列表 |
| `get_calc_hosts()` | 财务文件下载主机列表 |
| `get_port()` / `get_timeout()` | 默认端口与超时秒数 |
| `save_best_host(host)` | 写入标准行情优选主机到配置文件 |
| `save_best_ex_host(host)` / `save_best_mac_ex_host(host)` | 写入对应扩展协议优选主机到配置文件 |

`save_best_host(host)`、`save_best_ex_host(host)`、`save_best_mac_ex_host(host)` 写入该配置文件；`tdxman.bestip.candidates(protocol, dialect=None)` 读静态端点集，`ranked` 读设备本机已验证排名，`preferred` 合并两者，`save_rankings(groups)` 原子替换 `settings/bestip.json`。`protocol` 为 `standard`、`mac` 或 `ex`，扩展 `dialect` 可区分标准/MAC。一般通过 [`tdxman bestip`](cli/commands/shared/bestip.md) 验证服务器后保存，而不要把 TCP 延迟直接当成业务可用性。

`tdxman.transport.sync.ping_host`、`ping_all`、`ping_mac_all` 只测连通与延迟，不进行完整行情验收。`TdxConnection` 是低层传输类，普通查询用高层客户端。

## 涨跌幅规则：`tdxman.codec.price_rules`

`is_star_board`、`is_gem_board`、`is_bj_board` 根据代码前缀判断板块。`resolve_limit_rule(market, code, name, trade_date, st_status=None, listed_days=None, observed_sessions=None)` 返回 `LimitRuleResult`：`rule` 存在、`no_limit=True` 或两者皆空的未知状态；`reason` 和 `no_limit_basis` 提供依据。`resolve_no_limit_window(market, code, trade_date)` 返回窗口天数与依据或 `None`；`get_no_limit_window_days(market, code, name)` 是按标的类型的便捷窗口长度。`compute_price_limits(market, code, name, pre_close, finance_info=None, listed_days=None, trade_date=None)` 返回 `(limit_up, limit_down)`，无法确认可为 `None`。这些结果依赖交易日期、历史 ST 身份和上市交易日证据；没有依据时请保留未知状态。具体规则与有效期以该模块源码为准。

源码：`src/tdxman/symbol.py`、`config.py`、`bestip.py`、`transport/sync.py`、`codec/price_rules.py`。
