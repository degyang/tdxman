"""Manual TDX finance refresh for historical fundamentals."""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path

from tdxman.client import TdxClient
from tdxman.models.enums import Market

from .fundamentals_store import (
    fundamentals_connection,
    normalize_finance,
    write_finance_rows,
)
from .pool import pool_lock
from .securities import active_securities, ensure_securities
from .source_retry import read_with_retry


def _selected(root, symbols, limit):
    available = [row[0] for row in active_securities(root, "stock")]
    if symbols:
        requested = list(dict.fromkeys(symbols))
        unknown = sorted(set(requested) - set(available))
        if unknown:
            raise ValueError("Unknown or inactive stock symbols: " + ", ".join(unknown))
        available = requested
    return available[:limit] if limit else available


def update_fundamentals(
    root,
    *,
    symbols=(),
    limit=None,
    retries=2,
    retry_delay=1.0,
    max_consecutive_failures=3,
):
    root = Path(root).expanduser().resolve()
    ensure_securities(root)
    selected = _selected(root, symbols, limit)
    report = {
        "status": "running",
        "source": "tdxman:finance",
        "requested": len(selected),
        "success": [],
        "failed": [],
        "remaining_block": [],
        "retries": [],
    }
    rows = []
    manager = client = None
    consecutive = 0

    def close():
        nonlocal manager, client
        current, manager, client = manager, None, None
        if current is not None:
            with suppress(Exception):
                current.__exit__(None, None, None)

    def read(symbol):
        nonlocal manager, client

        def attempt():
            nonlocal manager, client
            if client is None:
                manager = TdxClient.from_best_host(
                    heartbeat_interval=0, auto_reconnect=False, timeout=10
                )
                client = manager.__enter__()
            code, market = symbol.split(".")
            frame = client.get_finance_info(Market[market], code)
            if frame.empty:
                raise OSError("Empty finance response")
            return frame.iloc[0].to_dict()

        def retry(number, error):
            report["retries"].append(
                {"symbol": symbol, "retry": number, "error": str(error)}
            )
            close()

        return read_with_retry(
            attempt, retries=retries, delay=retry_delay, on_retry=retry
        )

    fetched_at = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
    try:
        for position, symbol in enumerate(selected):
            try:
                row = normalize_finance(symbol, read(symbol), fetched_at=fetched_at)
            except (OSError, ValueError) as exc:
                report["failed"].append({"symbol": symbol, "error": str(exc)})
                consecutive += 1
                if consecutive >= max_consecutive_failures:
                    report["remaining_block"] = selected[position + 1 :]
                    break
                continue
            rows.append(row)
            report["success"].append(symbol)
            consecutive = 0
    finally:
        close()
    with pool_lock(root, write=True):
        if not (root / "fundamentals.sqlite").exists():
            with fundamentals_connection(root, create=True, read_only=False):
                pass
        with fundamentals_connection(root, read_only=False) as conn:
            report["changed"] = write_finance_rows(conn, rows)
    report["status"] = "ok" if not report["failed"] and not report["remaining_block"] else "partial"
    return report
