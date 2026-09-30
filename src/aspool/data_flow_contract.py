"""Canonical input, storage, output, and CLI ownership for production data."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class DataFlow:
    """One independently owned production data block."""

    name: str
    storage: str
    inputs: str
    write_cli: str
    outputs: str
    read_cli: str
    lifecycle: str


DATA_FLOWS = (
    DataFlow(
        "securities",
        "catalog.duckdb: securities; index_memberships",
        "TDX current SH/SZ/BJ stock, ETF, and index directories; TDX finance IPO date",
        "aspool update --type stock; aspool directory --type stock|etf|index|all",
        "DataPool.read_security_info(); stock update scope",
        "aspool query --dataset securities",
        "routine incremental",
    ),
    DataFlow(
        "security_calendar",
        "catalog.duckdb: security_calendar",
        "dated SH index quote or index K-line sessions",
        "aspool update|sync --type index",
        "DataPool.read_trading_calendar(); daily derivation session axis",
        "aspool query --dataset calendar",
        "routine incremental",
    ),
    DataFlow(
        "fundamentals",
        "fundamentals.sqlite: stock_financial_reports",
        "TDX finance rows with source report dates",
        "aspool fundamentals update",
        "DataPool.read_fundamental_reports()",
        "aspool fundamentals status; aspool query --dataset fundamentals",
        "manual low-frequency history",
    ),
    DataFlow(
        "shareholder_counts",
        "fundamentals.sqlite: stock_shareholder_counts",
        "TDX finance gudong_renshu with source report dates",
        "aspool fundamentals update",
        "DataPool.read_shareholder_counts()",
        "aspool query --dataset shareholder-counts",
        "manual low-frequency history",
    ),
    DataFlow(
        "stock_daily_bars",
        "stocks.sqlite: daily_bars",
        "TDX current quote (update); TDX/BaoStock K-line (sync)",
        "aspool update; aspool sync --type stock",
        "DataPool.read_daily()",
        "aspool query --dataset stock-bars SYMBOL",
        "routine incremental",
    ),
    DataFlow(
        "corporate_actions",
        "stocks.sqlite: corporate_actions",
        "TDX corporate-action events for affected stocks",
        "aspool update; aspool sync --type stock",
        "reference close and adjustment-factor inputs for dependent rows",
        "aspool status; dependent public daily/features outputs",
        "routine incremental dependency",
    ),
    DataFlow(
        "stock_adjustment_factors",
        "adjustments.sqlite: stock_adjustment_factors; stock_factor_anchors",
        "verified stock corporate actions and external factor anchors",
        "aspool update|sync --type stock",
        "DataPool.read_daily(adjust='qfq'|'hfq')",
        "aspool platform status",
        "deterministic derived reference",
    ),
    DataFlow(
        "stock_daily_features",
        "features.sqlite: stock_daily_features",
        "stock daily bars, corporate actions, dated name/ST and session state",
        "aspool update; aspool sync --type stock",
        "DataPool.read_security_daily(); DataPool.read_limit_events()",
        "aspool query --dataset stock-features|limit-events",
        "atomic derived output",
    ),
    DataFlow(
        "market_daily_summary",
        "features.sqlite: market_regime_features",
        "stock daily bars and daily_features",
        "aspool update; aspool sync --type stock",
        "DataPool.read_market_summary(); Regime public feature input",
        "aspool query --dataset market-summary",
        "atomic derived output",
    ),
    DataFlow(
        "index_daily_bars",
        "indices.sqlite: daily_bars",
        "catalog active HY2/GN/FG/ZS index directory and TDX index K-lines",
        "aspool update|sync --type index",
        "DataPool.read_index_daily(); DataPool.list_indices()",
        "aspool query --dataset index-bars SYMBOL",
        "routine incremental",
    ),
    DataFlow(
        "etf_daily_bars",
        "etfs.sqlite: daily_bars",
        "current ETF directory and TDX ETF K-lines",
        "aspool update|sync --type etf",
        "DataPool.read_etf_daily(); DataPool.list_etfs()",
        "aspool query --dataset etf-bars SYMBOL",
        "routine incremental",
    ),
    DataFlow(
        "etf_adjustment_factors",
        "adjustments.sqlite: etf_adjustment_factors",
        "reviewed free-stockdb factor source during migration",
        "scripts/ops/migrate_etfs_sqlite.py",
        "DataPool.read_etf_daily(adjust='qfq'|'hfq')",
        "aspool status; aspool platform verify",
        "derived reference",
    ),
)


def data_flows() -> list[dict[str, str]]:
    """Return a JSON-serializable copy so CLI and tests share one source."""
    return [asdict(flow) for flow in DATA_FLOWS]


def validate_data_flows() -> None:
    """Reject incomplete or duplicate ownership declarations."""
    names = [flow.name for flow in DATA_FLOWS]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate data-flow name")
    for flow in DATA_FLOWS:
        values = asdict(flow)
        missing = [key for key, value in values.items() if not value.strip()]
        if missing:
            raise ValueError(f"Incomplete data flow {flow.name}: {missing}")
