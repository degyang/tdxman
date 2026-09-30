#!/usr/bin/env python3
"""Detect recent operational data gaps and optionally repair only affected domains."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Gap:
    asset_type: str
    reason: str
    dates: tuple[str, ...]
    sample: tuple[str, ...] = ()


def _feature_tables(root: Path) -> tuple[Path, str, str]:
    path = root / "features.sqlite"
    if path.is_file():
        return path, "stock_daily_features", "market_regime_features"
    return root / "stocks.sqlite", "daily_features", "market_daily_summary"


def detect_recent_gaps(root: Path, count: int) -> list[Gap]:
    """Use persisted calendar and terminal states; do not contact a market source."""
    import duckdb

    with duckdb.connect(str(root / "catalog.duckdb"), read_only=True) as catalog:
        sessions = [
            str(row[0])
            for row in catalog.execute(
                "SELECT trade_date FROM security_calendar WHERE is_open "
                "ORDER BY trade_date DESC LIMIT ?",
                [count],
            ).fetchall()
        ]
        sessions.reverse()
        if not sessions:
            return [Gap("all", "没有交易日历，无法判断近期缺口", ())]
        latest = sessions[-1]
        expected_latest = {
            str(row[0])
            for row in catalog.execute(
                "SELECT symbol FROM securities WHERE asset_type='stock' AND active "
                "AND (listing_date IS NULL OR listing_date<=?) "
                "AND (delisting_date IS NULL OR delisting_date>?)",
                [latest, latest],
            ).fetchall()
        }

    gaps: list[Gap] = []
    feature_db, features, regime = _feature_tables(root)
    with sqlite3.connect(feature_db.as_uri() + "?mode=ro", uri=True) as conn:
        empty_days = []
        unresolved_days = []
        latest_missing: set[str] = set()
        for day in sessions:
            observed = {
                str(row[0])
                for row in conn.execute(
                    f"SELECT DISTINCT symbol FROM {features} WHERE trade_date=?", (day,)
                ).fetchall()
            }
            unresolved = conn.execute(
                f"SELECT count(*) FROM {features} WHERE trade_date=? "
                "AND coalesce(trading_status,'') IN ('MISSING','INVALID')",
                (day,),
            ).fetchone()[0]
            if day == latest:
                latest_missing = expected_latest - observed
            elif expected_latest and not observed:
                empty_days.append(day)
            if unresolved:
                unresolved_days.append(day)
        if empty_days:
            gaps.append(
                Gap(
                    "stock",
                    "股票派生在该交易日整日缺失",
                    tuple(empty_days),
                )
            )
        if latest_missing:
            gaps.append(
                Gap(
                    "stock",
                    "最新交易日未覆盖当前有效股票目录",
                    (latest,),
                    tuple(sorted(latest_missing))[:20],
                )
            )
        if unresolved_days:
            gaps.append(Gap("stock", "股票仍有 MISSING 或 INVALID 终态", tuple(unresolved_days)))

        tables = {
            str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if regime in tables:
            latest_feature = conn.execute(f"SELECT max(trade_date) FROM {features}").fetchone()[0]
            latest_market = conn.execute(
                f"SELECT max(period_key) FROM {regime} WHERE frequency='D'"
            ).fetchone()[0]
            if expected_latest and latest_feature != latest_market:
                gaps.append(
                    Gap("stock", "日级 Regime 公共特征没有跟随股票派生", (str(latest_feature),))
                )
        else:
            gaps.append(Gap("stock", "日级 Regime 公共特征表不存在", ()))

    # The Shanghai Composite is both the calendar anchor and a core market input.
    # Other indices may legitimately be sparse, so do not enforce full directory
    # coverage or per-symbol historical continuity here.
    with sqlite3.connect((root / "indices.sqlite").as_uri() + "?mode=ro", uri=True) as conn:
        days = [
            day
            for day in sessions
            if conn.execute(
                "SELECT 1 FROM daily_bars WHERE symbol='000001.SH' AND trade_date=? LIMIT 1",
                (day,),
            ).fetchone()
            is None
        ]
        if days:
            gaps.append(Gap("index", "上证指数交易日历锚点缺失", tuple(days), ("000001.SH",)))

    # ETF can validly have no trade on a session.  Only an entirely absent market date is a gap;
    # per-symbol confirmation remains the responsibility of the ETF sync quote fallback.
    with sqlite3.connect((root / "etfs.sqlite").as_uri() + "?mode=ro", uri=True) as conn:
        days = [
            day
            for day in sessions
            if conn.execute(
                "SELECT 1 FROM daily_bars WHERE trade_date=? LIMIT 1", (day,)
            ).fetchone()
            is None
        ]
        if days:
            gaps.append(Gap("etf", "该交易日没有任何 ETF 日线", tuple(days)))

    return gaps


def repair_commands(root: Path, gaps: list[Gap], count: int, workers: int) -> list[tuple[str, ...]]:
    """Collapse multiple observations per domain into one bounded public sync command."""
    affected = {gap.asset_type for gap in gaps}
    return [
        (
            "sync",
            "--type",
            asset,
            "--count",
            str(count),
            "--root",
            str(root),
            "--workers",
            str(workers),
            "--retries",
            "2",
            "--retry-delay",
            "1",
        )
        for asset in ("stock", "index", "etf")
        if asset in affected
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data"))
    parser.add_argument("--count", type=int, default=10, choices=range(1, 61), metavar="1..60")
    parser.add_argument("--workers", type=int, default=4, choices=range(1, 9), metavar="1..8")
    parser.add_argument("--repair", action="store_true", help="发现缺口后执行对应的 aspool sync")
    args = parser.parse_args(argv or sys.argv[1:])
    root = args.root.expanduser().resolve()
    gaps = detect_recent_gaps(root, args.count)
    commands = repair_commands(root, gaps, args.count, args.workers)
    receipt = {"gaps": [asdict(gap) for gap in gaps], "commands": [list(cmd) for cmd in commands]}
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    if not args.repair or not commands:
        return 0
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("未找到 uv；请安装 uv 后重试")
    project = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env.setdefault("UV_CACHE_DIR", str(project / ".local" / "uv-cache"))
    for command in commands:
        subprocess.run([uv, "run", "aspool", *command], cwd=project, env=env, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
