#!/usr/bin/env python3
"""Run the supported daily aspool ingestion flow and write one audit receipt.

This is an operational orchestrator.  It intentionally invokes public ``aspool``
commands instead of importing their internal writers, so its order and failure
semantics remain the same for an operator, cron, and a future service runner.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

import duckdb

from aspool.operation_report import operation_outcome, run_command


@dataclass(frozen=True)
class Stage:
    """One public CLI operation in dependency order."""

    name: str
    command: tuple[str, ...]
    purpose: str


def pipeline_stages(root: Path, *, workers: int, fundamentals: bool) -> list[Stage]:
    """Return the complete daily sequence without running it."""
    common = (
        "--root",
        str(root),
        "--workers",
        str(workers),
        "--retries",
        "2",
        "--retry-delay",
        "1",
    )
    stages = [
        Stage(
            "directory",
            ("directory", "--type", "all", "--root", str(root)),
            "发布股票、ETF 和指数的当前有效目录",
        ),
        Stage(
            "stock_update",
            ("update", "--type", "stock", *common),
            "写入当日未复权 quote 与直接派生，再逐证券处理除权事件和因子后缀",
        ),
        Stage(
            "index_update",
            ("update", "--type", "index", *common),
            "以指数日 K 更新交易日历和指数原始行情",
        ),
        Stage(
            "etf_update",
            ("update", "--type", "etf", *common),
            "更新 ETF 未复权日线；源端确认无交易时保留 NO_TRADE",
        ),
    ]
    if fundamentals:
        stages.append(
            Stage(
                "fundamentals",
                (
                    "fundamentals",
                    "update",
                    "--root",
                    str(root),
                    "--retries",
                    "2",
                    "--retry-delay",
                    "1",
                ),
                "刷新最新财报和股东人数快照（不倒填历史）",
            )
        )
    return stages


def _sqlite_table(path: Path, candidates: tuple[str, ...]) -> str | None:
    if not path.is_file():
        return None
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
        found = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    return next((table for table in candidates if table in found), None)


def _feature_closure(root: Path, active_stocks: int | None) -> dict[str, Any]:
    """Bounded latest-session state audit for the stock derived layer."""
    path = root / "features.sqlite"
    table = _sqlite_table(path, ("stock_daily_features",))
    if table is None:
        path = root / "stocks.sqlite"
        table = _sqlite_table(path, ("daily_features",))
    if table is None:
        return {"available": False}
    expected_symbols: set[str] | None = None
    target = None
    catalog_path = root / "catalog.duckdb"
    if catalog_path.is_file():
        with duckdb.connect(str(catalog_path), read_only=True) as catalog:
            target_row = catalog.execute(
                "SELECT max(trade_date) FROM security_calendar WHERE is_open"
            ).fetchone()
            target = target_row[0].isoformat() if target_row and target_row[0] else None
            if target:
                expected_symbols = {
                    row[0]
                    for row in catalog.execute(
                        "SELECT symbol FROM securities WHERE asset_type='stock' AND active "
                        "AND (listing_date IS NULL OR listing_date<=?) "
                        "AND (delisting_date IS NULL OR delisting_date>?)",
                        [target, target],
                    ).fetchall()
                }
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
        latest = conn.execute(f"SELECT max(trade_date) FROM {table}").fetchone()[0]
        target = target or latest
        statuses = dict(
            conn.execute(
                f"SELECT calc_status,count(*) FROM {table} WHERE trade_date=? GROUP BY calc_status",
                (target,),
            )
        )
        trading = dict(
            conn.execute(
                f"SELECT coalesce(trading_status,'UNKNOWN'),count(*) FROM {table} "
                "WHERE trade_date=? GROUP BY coalesce(trading_status,'UNKNOWN')",
                (target,),
            )
        )
        observed_symbols = {
            row[0]
            for row in conn.execute(f"SELECT symbol FROM {table} WHERE trade_date=?", (target,))
        }
        traded_symbols = {
            row[0]
            for row in conn.execute(
                f"SELECT symbol FROM {table} WHERE trade_date=? AND calc_status='TRADED'",
                (target,),
            )
        }
        summary_table = _sqlite_table(path, ("market_regime_features", "market_daily_summary"))
        summary_rows = (
            conn.execute(
                f"SELECT count(*) FROM {summary_table} WHERE frequency='D' AND period_key=?",
                (target,),
            ).fetchone()[0]
            if summary_table
            else 0
        )
        summary_quality: dict[str, Any] = {}
        if summary_table:
            summary_columns = {
                row[1] for row in conn.execute(f"PRAGMA table_info({summary_table})")
            }
            quality_fields = (
                "trading_count",
                "valid_return_count",
                "invalid_return_count",
                "limit_unknown_count",
                "limit_invalid_count",
                "streak_unknown_count",
                "ma20_valid_count",
            )
            if set(quality_fields) <= summary_columns and "scope" in summary_columns:
                quality_row = conn.execute(
                    f"SELECT {','.join(quality_fields)} FROM {summary_table} "
                    "WHERE frequency='D' AND period_key=? AND scope='all_stocks'",
                    (target,),
                ).fetchone()
                if quality_row:
                    summary_quality = dict(zip(quality_fields, quality_row))
    rows = sum(statuses.values())
    missing_symbols = sorted(expected_symbols - observed_symbols) if expected_symbols else []
    extra_symbols = sorted(observed_symbols - expected_symbols) if expected_symbols else []
    factor_missing: list[str] = []
    factors = root / "adjustments.sqlite"
    factor_table = _sqlite_table(factors, ("stock_adjustment_factors",))
    if expected_symbols is not None and factor_table:
        with sqlite3.connect(factors.as_uri() + "?mode=ro", uri=True) as conn:
            covered = {
                row[0]
                for row in conn.execute(
                    f"SELECT DISTINCT symbol FROM {factor_table} "
                    "WHERE valid_from<=? AND valid_through>=?",
                    (target, target),
                )
            }
        factor_missing = sorted(traded_symbols - covered)
    exact_closed = (
        not missing_symbols and not extra_symbols
        if expected_symbols is not None
        else active_stocks is None or rows == active_stocks
    )
    factor_ready = expected_symbols is None or bool(factor_table) and not factor_missing
    core_quality_ready = not summary_quality or (
        summary_quality["valid_return_count"] == summary_quality["trading_count"]
        and summary_quality["invalid_return_count"] == 0
        and summary_quality["limit_unknown_count"] == 0
        and summary_quality["limit_invalid_count"] == 0
        and summary_quality["streak_unknown_count"] == 0
    )
    return {
        "available": True,
        "table": f"{path.name}:{table}",
        "date": target,
        "latest_date": latest,
        "rows": rows,
        "calc_status": statuses,
        "trading_status": trading,
        "active_stock_count": (
            len(expected_symbols) if expected_symbols is not None else active_stocks
        ),
        "missing_symbol_count": len(missing_symbols),
        "missing_symbol_sample": missing_symbols[:20],
        "extra_symbol_count": len(extra_symbols),
        "extra_symbol_sample": extra_symbols[:20],
        "factor_missing_count": len(factor_missing),
        "factor_missing_sample": factor_missing[:20],
        "factor_store_available": bool(factor_table),
        "factor_ready": factor_ready,
        "summary_rows": summary_rows,
        "summary_quality": summary_quality,
        "core_quality_ready": core_quality_ready,
        "closed": exact_closed
        and core_quality_ready
        and (expected_symbols is None or summary_rows > 0),
    }


def collect_audit(root: Path, run_aspool) -> dict[str, Any]:
    """Collect read-only contract, coverage, and latest derived-state evidence."""
    contract = run_aspool(("contract", "--format", "json"), capture=True)
    status = run_aspool(("status", "--root", str(root), "--format", "json"), capture=True)
    flows = json.loads(contract.stdout)
    datasets = json.loads(status.stdout)
    by_dataset = {row["dataset"]: row for row in datasets}
    active_stocks = by_dataset.get("securities", {}).get("symbols")
    required = {
        "securities",
        "calendar",
        "fundamentals",
        "shareholder-counts",
        "stock-bars",
        "corporate-actions",
        "stock-factors",
        "stock-features",
        "market-summary",
        "index-bars",
        "etf-bars",
        "etf-factors",
    }
    missing = sorted(required - set(by_dataset))
    closure = _feature_closure(root, active_stocks)
    return {
        "contract_blocks": len(flows),
        "contract_names": [flow["name"] for flow in flows],
        "datasets": datasets,
        "missing_required_datasets": missing,
        "stock_feature_closure": closure,
        "ready": bool(flows) and not missing and closure.get("closed", False),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(os.environ.get("ASPOOL_ROOT", Path(__file__).resolve().parents[2] / "data")),
        help="aspool 数据根目录（默认项目 data，可用 ASPOOL_ROOT 覆盖）",
    )
    parser.add_argument("--workers", type=int, default=4, choices=range(1, 9), metavar="1..8")
    parser.add_argument(
        "--with-fundamentals", action="store_true", help="纳入手动低频基本面快照更新"
    )
    parser.add_argument("--dry-run", action="store_true", help="仅显示计划，不写入数据")
    return parser.parse_args(argv)


@contextmanager
def pipeline_lock(root: Path):
    """Keep complete runs for one canonical data root from interleaving.

    Children inherit the descriptor so killing only the orchestrator does not
    release its lock while a public aspool command is still writing.
    """
    directory = root.parent / ".local" / "locks"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / f"{root.name}.daily-pipeline.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield handle.fileno()
    # Closing the last inherited descriptor releases flock. Never unlink the
    # file: a replaced inode would let another process bypass the active lock.


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    root = args.root.expanduser().resolve()
    project = Path(__file__).resolve().parents[2]
    stages = pipeline_stages(root, workers=args.workers, fundamentals=args.with_fundamentals)
    if args.dry_run:
        print(json.dumps([asdict(stage) for stage in stages], ensure_ascii=False, indent=2))
        return 0

    with ExitStack() as stack:
        try:
            lock_fd = stack.enter_context(pipeline_lock(root))
        except BlockingIOError:
            print(f"daily pipeline: already running for {root}", file=sys.stderr, flush=True)
            return 75
        return run_pipeline(args, root, project, stages, lock_fd)


def run_pipeline(args, root: Path, project: Path, stages: list[Stage], lock_fd: int) -> int:
    tick = perf_counter()
    report_dir = project / ".local" / "reports" / "daily-pipeline"
    report_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    aspool = Path(sys.executable).with_name("aspool")
    if not aspool.is_file():
        resolved = shutil.which("aspool")
        if resolved is None:
            raise SystemExit("当前 Python 环境中未找到 aspool；请先安装项目环境")
        aspool = Path(resolved)
    report: dict[str, Any] = {
        "status": "running",
        "root": str(root),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "options": {
            "workers": args.workers,
            "fundamentals": args.with_fundamentals,
        },
        "stages": [],
    }
    path = report_dir / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")

    def save_report() -> None:
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n")

    save_report()

    def run_aspool(command: tuple[str, ...], *, capture: bool = False):
        return run_command(
            aspool, command, cwd=project, env=env, capture=capture, pass_fds=(lock_fd,)
        )

    try:
        for stage in stages:
            stage_tick = perf_counter()
            started = datetime.now(timezone.utc).isoformat()
            print(f"daily pipeline: starting {stage.name}；{stage.purpose}", flush=True)
            entry: dict[str, Any] = {
                "name": stage.name,
                "purpose": stage.purpose,
                "command": list(stage.command),
                "started_at": started,
                "status": "running",
            }
            report["stages"].append(entry)
            save_report()
            try:
                completed = run_aspool(stage.command)
                entry.update(operation_outcome(completed, require_report=stage.name != "directory"))
                entry["exit_code"] = completed.returncode
                if entry["status"] == "failed":
                    raise subprocess.CalledProcessError(completed.returncode or 1, stage.command)
            except subprocess.CalledProcessError as exc:
                entry.update(status="failed", exit_code=exc.returncode)
                entry["finished_at"] = datetime.now(timezone.utc).isoformat()
                entry["elapsed_ms"] = round((perf_counter() - stage_tick) * 1000)
                save_report()
                print(
                    f"daily pipeline: failed {stage.name}；exit={exc.returncode}",
                    flush=True,
                )
                raise
            entry["finished_at"] = datetime.now(timezone.utc).isoformat()
            entry["elapsed_ms"] = round((perf_counter() - stage_tick) * 1000)
            save_report()
            print(f"daily pipeline: finished {stage.name}", flush=True)
        audit_tick = perf_counter()
        report["audit"] = collect_audit(root, run_aspool)
        report["audit_elapsed_ms"] = round((perf_counter() - audit_tick) * 1000)
        all_stages_ok = all(stage["status"] == "ok" for stage in report["stages"])
        report["status"] = "ok" if all_stages_ok and report["audit"]["ready"] else "partial"
    except subprocess.CalledProcessError as exc:
        report.update(status="failed", error=f"aspool exited {exc.returncode}")
    except KeyboardInterrupt:
        report.update(status="interrupted", error="operator interrupt")
    except Exception as exc:  # Preserve one inspectable receipt for any operational failure.
        report.update(status="failed", error=str(exc))
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["elapsed_ms"] = round((perf_counter() - tick) * 1000)
        save_report()
    print(f"daily pipeline: {report['status']}；报告：{path}")
    return 0 if report["status"] == "ok" else 130 if report["status"] == "interrupted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
