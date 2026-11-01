"""Explicit local exclusions, retained with the base data across directory refreshes."""

import json
from pathlib import Path


def invalid_symbols(root):
    path = Path(root) / "invalid-securities.json"
    return set(json.loads(path.read_text())) if path.exists() else set()


def enforce_invalid(conn, root):
    for symbol in sorted(invalid_symbols(root)):
        conn.execute(
            "UPDATE securities SET active=false,updated_at=current_timestamp "
            "WHERE symbol=? AND active",
            [symbol],
        )
