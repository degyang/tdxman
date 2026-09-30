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

## Current aspool production operations

This section is authoritative for the SQLite production root `tdxman/data/` and supersedes the legacy daily workflow below. Keep reports, logs and receipts in `.local/`, never in `data/`.

### Daily post-close update

After 15:30 Asia/Shanghai, run:

```bash
bash scripts/ops/run_daily_data_pipeline.sh
```

Both shell entry points (`run_daily_data_pipeline.sh`, `repair_recent_data_gaps.sh`) resolve and enter the project root first, set a shared environment (`TZ` defaults to Asia/Shanghai in the pipeline, `UV_CACHE_DIR` under `.local/`), and run the Python orchestrator with `.venv/bin/python` when present, else `uv run --frozen`. The data root defaults to the project `data/`; override it with the `ASPOOL_ROOT` environment variable or `--root`.

Use `--with-fundamentals` only when refreshing the latest financial-report and shareholder-count snapshots. These are low-frequency latest snapshots, not point-in-time financial history.

The normal daily path is fixed:

1. Publish complete current stock, ETF and index directories.
2. `update --type stock`: write validated unadjusted quotes, names and trading state first; then reconcile bounded corporate-action/factor suffixes per symbol. A factor failure retains the valid raw bar and leaves factor-dependent features incomplete with an explicit report entry. Factor suffixes commit in bounded small batches under the same pool write lock as their mirror copy.
3. `update --type index`: update index K-lines and the trading calendar.
4. `update --type etf`: update unadjusted ETF K-lines; preserve source-confirmed `NO_TRADE` without making a zero-price bar.
5. Optionally refresh fundamentals, then audit all production data blocks.

Before each bounded write the writer verifies the previous mirror commit is complete. An incomplete mirror rejects the write with `DERIVED_NOT_READY` and requires an explicit `aspool platform reconcile`; later partial updates must not overwrite pending-recovery state, and the daily path never auto-rebuilds mirrors in full.

The pipeline receipt aggregates each stage's persisted sub-report. A single-security failure marks that stage `partial` instead of `failed`: independent later stages still run, while the overall run status becomes `partial`. `factor_unavailable` items (for example `no_verified_anchor`) are counted and reported separately (`factor_quality`) and are not mixed into failure lists.

Do not place `sync` in the normal daily path. `sync` is a bounded historical-repair operation, not a second daily fetch.

### Tail-gap repair

Check before writing:

```bash
bash scripts/ops/repair_recent_data_gaps.sh --count 10
```

The checker is read-only and uses the persisted trading calendar only. It checks exact current-stock terminal-state closure on the latest session; older stock sessions only for whole-day absence or explicit `MISSING`/`INVALID`; the Shanghai Composite calendar anchor; entirely absent ETF market days; and whether daily Regime public rows follow stock features. It intentionally does not enforce per-security historical completeness. It cannot prove the stored calendar is fresh — a missed daily run also leaves the calendar stale — and the receipt states that limit. It prints the exact bounded repair commands.

Only when it reports a gap, run:

```bash
bash scripts/ops/repair_recent_data_gaps.sh --count 30 --repair
```

`--repair` first runs a bounded `aspool sync --type index` to refresh the trading calendar (never infer holidays from weekdays), re-detects gaps, then repairs only affected domains with `aspool sync --type stock|etf --count N` (index is not re-run). Missing daily market summaries are recomputed locally per date inside the stock sync, without re-fetching complete daily lines. It finishes with a recheck and exits non-zero whenever residual gaps remain or any operation failed. Default `count` is 10 and the maximum is 60. Use explicit `--start/--end` only for a known historical incident. Use BaoStock only as an explicit fallback for remaining stock `MISSING` values:

```bash
aspool sync --type stock --source baostock --status missing
```

BaoStock does not support BJ. Leave unavailable BJ data as `MISSING`; do not turn it into a no-trade bar.

### Data and acceptance invariants

- `stocks.sqlite`, `indices.sqlite` and `etfs.sqlite` contain unadjusted bars. Read adjusted prices from raw bars plus sparse factors in `adjustments.sqlite`.
- Corporate-action or factor changes recompute only affected stock suffixes. Do not add a whole-history adjustment pass to daily runs.
- A single-symbol factor error must not roll back its valid raw quote or stop independent market blocks. Database/transaction failures still stop the stage; source failures stop after the configured consecutive-failure threshold.
- `NO_TRADE` is a successful source response with no trade. Stale, failed or invalid responses remain `MISSING`/`INVALID`; none creates a synthetic bar. BJ is excluded from limit-up/down and streak statistics.
- ETF factors are reviewed reference data, not an online TDX daily feed. Report their range separately from ETF daily-bar freshness.
- `aspool platform verify` is a read-only check: the default shallow level compares row counts and mirror state only, and `content_equal=null` means content was not verified, not that it is equal. `aspool platform verify --deep` performs the full-content comparison.
- Daily stock derived output includes Enriched base fields and `market_regime_features`. Weekly/monthly writers and Fundwise scoring/cache remain separate consumer responsibilities.

Read `.local/reports/daily-pipeline/*.json`, then verify the public contract and coverage:

```bash
aspool contract --format json
aspool status --root data --format json
```

Require all 12 contract blocks, including `stock-factors` and `etf-factors`. Acceptance compares the exact active-stock set on the target trading day, factor coverage for traded symbols, and the daily market summary; a maximum date or equal row count alone is insufficient. An event-driven factor table having an older maximum effective date is not by itself a stale-data failure. See `docs/design/daily_data_pipeline.md` and `docs/design/production_data_flow_contract.md` for command details.

## Legacy aspool daily workflow

The following legacy commands remain as historical or compatibility reference. Do not use them for the current SQLite production workflow.

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
- Use `aspool sync --source baostock --start YYYY-MM-DD --end YYYY-MM-DD` for explicit historical repairs. It fills raw daily prices, dated ST/reference-price fields, trading status and listing metadata, preserves valid primary values and reports conflicts. Missing rows are not suspensions. Beijing stocks are not covered. Consecutive limits remain unknown if the requested history has no reliable boundary. Inspect both price-limit and consecutive-limit coverage. See `docs/ops/baostock.md` for local read APIs and reports.
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

For Fundwise integration, read [aspool API](docs/design/aspool_api.md). Use the public stock, ETF and index APIs instead of internal fundamental snapshots. Index data contains OHLCV, amount and up_count/down_count; absent breadth is stored as zero, not proof of no advancing/declining securities. ETF data uses stock-style shares, amount and turnover and does not expose breadth counts. Index volume uses source units, unlike stock shares. Current APIs do not guarantee immutable versions or point-in-time historical constituents.

See [README daily workflow](README.md#daily-工作流每日收盘后同步股票etf-和指数) for the user-facing procedure, [index design](docs/design/aspool_index_design.md), and [ETF design](docs/design/aspool_etf_design.md) for storage and synchronization details.

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
