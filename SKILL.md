---
name: tdxman
description: Use tdxman to retrieve TongDaXin market data and aspool to maintain local stock and index daily data pools, run daily synchronization, and provide data to Fundwise.
---

# tdxman

`tdxman` retrieves market data and writes JSON by default. `aspool` maintains the local stock and index data pools. Both CLIs are installed by this repository.

## Run the CLI

From the repository root, activate the project environment and install the editable package when the command entry point is missing or has changed:

```bash
source .venv/bin/activate
python -m pip install -e .
rehash
tdxman --help
```

Use `SH`, `SZ`, or `BJ` for A-share markets. `SH 000001` is the Shanghai Composite Index; `SZ 000001` is Ping An Bank.
Run `tdxman markets` to list these codes and examples.

All data commands write JSON to stdout by default. Use `--format json|table|csv` to select
the format. `--format table` keeps the terminal grid table; when written to a file it creates
a Markdown table (`.md`). Use `--output` for a file or directory. With a directory, the default
file name is the symbol; without `--format`, files default to JSON.

```bash
tdxman finance SZ 006324 --format json --output data/006324.json
tdxman kline SH 600519 --format csv --output data
tdxman quote "SZ 000001,SH 600519" --format table --output data/quotes
```

For multi-symbol `quote`, `--output` must be a directory; it creates one response file per symbol.
Tables show common fields by default; pass `--fields all` for every returned field. JSON and CSV always include every field.

```bash
tdxman quote "SZ 000001,SH 600519" --format table
tdxman quote 600519.SH --source baostock --count 30 --format table
tdxman kline SH 600519 --period DAILY --count 30 --format table
tdxman tick SH 600519 --days 5 --format table
```

CLI output normalizes numeric presentation: prices, amounts, market caps, finance amounts, and fund-flow amounts use two decimal places; quantities and counts use integers; other ratios retain up to four decimal places. JSON values remain numeric, so insignificant trailing zeroes may not appear.

## aspool daily workflow

Use `aspool` for persistent data maintenance and `tdxman` for individual queries. Run from the repository root. The pool defaults to `~/.aspool`; use the same `--root PATH` on every command when selecting another pool. In `settings/config.yaml`, `aspool.free_stockdb.root` is the import source, and `offline.vipdoc` is the local TongDaXin source, not the pool destination.

For an existing stock daily pool, run after 15:30 Asia/Shanghai on a trading day (for example 16:00):

```bash
source .venv/bin/activate
export TZ=Asia/Shanghai

aspool update &&
aspool sync --type index --source tdx --tdx-mode online --period daily &&
aspool sync --type ex --category ETF --source tdx --period daily &&
aspool status
```

This is a daily workflow, not an `aspool daily` command. For routine daily updates, `update` refreshes stock quotes and runs a BaoStock supplement before the subsequent index sync. For longer stock-history gaps, first run `aspool sync --type stock --source tdx --period daily`, then update quotes. Do not use `--limit` for a complete pool update.

- Stock online sync fetches from the latest page back to a five-stored-bar overlap, paging across longer gaps. It combines K lines with stored low-frequency fields and calculated ratios. Every seven days it refreshes the A-share directory; new active symbols bootstrap their longest available history, and symbols with no server K lines remain pending for the next sync. Run `aspool universe` to refresh and inspect this directory manually.
- Update refreshes the current day's stock record and low-frequency snapshots from quotes, then supplements incomplete Shanghai/Shenzhen stock data over the last 30 trading days using BaoStock. Configure `aspool.baostock.enabled/lookback`, or use `--no-baostock` / `--lookback N`. It has no `--type` or `--period` option. Its TongDaXin stage supports `--async` and `--workers 1..8` (default 4); BaoStock uses one serial session. Update rejects weekdays 09:00–15:30 inclusive using Asia/Shanghai time; this is a weekday guard, not a holiday calendar. Prefer running after 16:00, since the supplement excludes the current day before that hour.
- Use `aspool sync --source baostock --start YYYY-MM-DD --end YYYY-MM-DD` for explicit historical repairs. It fills raw daily prices, dated ST/reference-price fields, trading status and listing metadata, preserves valid primary values and reports conflicts. Missing rows are not suspensions. Beijing stocks are not covered. Consecutive limits remain unknown if the requested history has no reliable boundary. Inspect both price-limit and consecutive-limit coverage. See `docs/baostock.md` for local read APIs and reports.
- Index sync reads all available history **only for initial creation or a newly added index**. Existing indices fetch incremental data with a five-stored-bar overlap to replace incomplete bars and recent revisions. Requests use 30-row pages and continue across longer gaps until reaching the overlap; do not reload the entire history for daily maintenance. Only `--source tdx --period daily` is supported. Online sync supports `--async`; offline reads only vipdoc and cannot guarantee online freshness.
- Sync can run intraday and save an incomplete current-day bar. For daily closed-data consumption, run after close and select an explicitly confirmed closed trading date.
- Stale, undated, future-dated and invalid quote records are rejected without overwriting existing bars. Unchanged files are not rewritten. Stock reports are in `ROOT/reports/maintenance/`; distinguish missing quotes from failures.
- `&&` stops subsequent commands on failure. Inspect failures, correct their cause, and rerun as appropriate; successful index writes remain saved. Index reports under `ROOT/reports/index-sync/` include per-index coverage and rejected source rows. Do not report full success solely from process completion.

### Bootstrap and occasional maintenance

For an empty stock pool, run `aspool import --source free-stockdb --period daily` once; the command validates imported data. `aspool init` creates structure only. An existing pool does not need daily reimport. Import shared adjustment factors separately with `aspool import --source free-stockdb --factor`; do not import minute history as part of this daily workflow.

Index sync can bootstrap an empty index pool using `settings/board_index.json`. It covers HY/HY2/GN/FG plus selected ZS common indices, excludes names starting with `昨日`, and preserves source categories. ETF securities are excluded from this index pool because they have stock-style OHLCV and no index breadth counts; use the independent ETF pool instead:

```bash
python scripts/maintain_board_lists.py          # Preview added/removed/changed entries
python scripts/maintain_board_lists.py --write  # Apply the refreshed list
python scripts/maintain_board_lists.py --target etf --write  # Refresh settings/etf_list.json
aspool ex categories                                      # Show ex asset categories and status
aspool sync --type ex --category ETF --source tdx --period daily  # ETF daily bars from 2010 onward
```

Removing a list entry does not delete stored history. Use `aspool fundamentals` for a separate low-frequency refresh when needed; daily update already refreshes these snapshots. Use `aspool sync --type stock --source free-stockdb --period daily` only for requested full stock-history recalibration. Index synchronization does not use free-stockdb.

### Verify and hand off to Fundwise

`aspool status` reports stock、ETF、分钟线和指数的汇总覆盖；用 `DataPool.list_indices()` / `DataPool.list_etfs()` 查看逐资产名称、日期和行数。不要将全局最大日期视为全部标的已更新；仍需区分停牌与数据源不可用。

```python
from aspool import DataPool

pool = DataPool('~/.aspool')  # Match the maintenance root
print(pool.status())
print(pool.list_indices().to_string(index=False))
# Replace with the required confirmed closed trading date.
frame = pool.read_index_daily(symbols='SH.000300', end='2026-09-16', lookback=120)
etfs = pool.list_etfs()
etf_frame = pool.read_etf_daily(symbols='SZ.159366', end='2026-09-16', lookback=120)
```

For Fundwise integration, read [aspool API](docs/aspool_api.md). Use the public stock, ETF and index APIs instead of internal fundamental snapshots. Index data contains OHLCV, amount and up_count/down_count; absent breadth is stored as zero, not proof of no advancing/declining securities. ETF data uses stock-style shares, amount and turnover and does not expose breadth counts. Index volume uses source units, unlike stock shares. Current APIs do not guarantee immutable versions or point-in-time historical constituents.

See [README daily workflow](README.md#daily-工作流每日收盘后同步股票etf和指数) for the user-facing procedure, [index design](docs/aspool_index_design.md), and [ETF design](docs/aspool_etf_design.md) for storage and synchronization details.

## Commands

```bash
# Connectivity and general market data
tdxman ping --timeout 3 --format table
tdxman version
tdxman quote "SZ 000001,SH 600519"
# quote-list categories: SH/SZ=Shanghai/Shenzhen A shares, A=all A shares, B=B shares,
# KCB=STAR Market, CYB=ChiNext, BJ=Beijing Stock Exchange, ETF/LOF=funds,
# HGT/SGT=Shanghai/Shenzhen Stock Connect constituents, FXJS=risk warning, ZS=standard indices.
tdxman quote-list A --count 20 --sort CHANGE_PCT --order DESC --format table
tdxman kline SZ 000001 --period 5MIN --count 30 --adjust QFQ
tdxman tick SZ 000001 --date 20250115
tdxman transaction SH 600519 --count 100 --format table

# Index categories and discovery
# TongDaXin sector/research indices (mostly 881xxx): all / level-1 and level-2
# industry / concept / style. This is not the full standard-index directory.
tdxman board-list --format table
tdxman board-list --type HY --format table
tdxman board-list --type HY2 --format table
tdxman board-list --type GN --format table
tdxman board-list --type FG --format table
tdxman board-list --type DQ --format table
# OTHER and YJ_LEVEL1/2/3 are named board categories.
tdxman board-list --type OTHER --format table
tdxman board-list --type YJ_LEVEL1 --format table
tdxman board-list --type YJ_LEVEL2 --format table
tdxman board-list --type YJ_LEVEL3 --format table
tdxman board-members 881001 --format table  # live component quotes
tdxman board-members 000699 --count 20 --format table  # supported standard-index constituents
tdxman belong-board SZ 000001 --format table # security's boards

# Standard A-share indices: Shanghai, Shenzhen, and CNI indices
# (for example 000300, 000688, 399001, 399006)
tdxman board-list --type ZS --count 600 --format table
tdxman quote-list ZS --count 600 --format table
tdxman kline SH 000300 --period DAILY --count 120 --format table
tdxman kline SZ 399001 --period DAILY --count 120 --format table
# Standard-index K lines include up_count/down_count under the index's own breadth scope.

# Extended index categories: discover code first, then run `ex kline MARKET CODE ...`
tdxman ex quote-list CSI_INDEX --count 600 --format table       # CSI
tdxman ex quote-list SZSE_INDEX --count 600 --format table      # CNI
tdxman ex quote-list HK_INDEX --count 600 --format table        # Hong Kong
tdxman ex quote-list INTL_INDEX --count 600 --format table      # international
tdxman ex quote-list FUTURES_INDEX --count 600 --format table   # commodity
tdxman ex quote-list RISK_CONTROL_INDEX --count 600 --format table
tdxman ex quote-list HUAZHENG_INDEX --count 600 --format table
tdxman ex quote-list EXTENDED_SECTOR_INDEX --count 600 --format table
tdxman ex kline CSI_INDEX 000300 --period DAILY --count 120 --format table

tdxman capital-flow SH 600519 --format table
tdxman auction SZ 000001 --format table
tdxman unusual SH --count 100 --format table
tdxman market-stat --format table
tdxman server-info --format table
tdxman symbol-info SZ 000001 --format table

# Financial data
tdxman finance SH 600519 --format table
tdxman fund-flow SZ 000001 --count 20 --closed-only --format table

# Hong Kong, US, and futures markets
tdxman ex markets
tdxman ex kline HK_MAIN_BOARD 00700 --count 30 --format table
tdxman ex quote US_STOCK TSLA --format table
tdxman ex quote-list SH_FUTURES --count 100 --format table
tdxman ex tick HK_MAIN_BOARD 00700 --format table
```

`finance` returns the latest structured financial summary, including share capital, balance sheet, profit, and per-share fields. It does not return the complete text-based F10 document catalogue.

`fund-flow` returns historical daily flows grouped into `super_*`, `large_*`, `medium_*`, and `small_*` fields, in yuan. `--start` is the offset from the newest record, not a date; the server returns the selected rows in chronological order. Use `--closed-only` to omit the current trading day's potentially incomplete row. It is for individual securities, not indices: index transactions have neutral direction and cannot produce directional fund flows.

## Protocol and local data boundaries

The CLI uses MAC protocol clients for normal A-share and extended-market queries. `finance` and `fund-flow` use the standard protocol APIs; the historical fund-flow fallback uses MAC daily bars when standard day-bar responses are incomplete.

Use `tdxman offline MARKET CODE --period DAILY|1MIN|5MIN` for local K lines. The default vipdoc path is `offline.vipdoc` in `settings/config.yaml`; use `--vipdoc PATH` to override it. Python module `tdxman.offline` also supports `.lc1`, `.lc5`, block, capital-change, and historical finance files.
