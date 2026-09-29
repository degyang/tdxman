"""Protocol-specific candidate and locally ranked TDX endpoint management."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SETTINGS = Path(__file__).resolve().parents[2] / "settings"
ALLIP_PATH = _SETTINGS / "allip.json"
BESTIP_PATH = _SETTINGS / "bestip.json"
_PROTOCOLS = {"standard", "mac", "ex"}


def _read(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid endpoint configuration: {path}") from exc


def candidates(protocol: str, *, dialect: str | None = None) -> list[dict[str, Any]]:
    """Return de-duplicated static candidates for one wire protocol."""
    if protocol not in _PROTOCOLS:
        raise ValueError(f"Unsupported protocol: {protocol}")
    payload = _read(ALLIP_PATH)
    if payload.get("schema_version") != 1:
        raise ValueError("allip.json schema_version must be 1")
    seen: set[tuple[str, int]] = set()
    result = []
    for item in payload.get("endpoints", []):
        host, port = item.get("host"), item.get("port")
        protocols = item.get("protocols", [])
        endpoint_dialect = item.get("dialect", "standard")
        if (
            not isinstance(host, str)
            or not isinstance(port, int)
            or not 1 <= port <= 65535
            or protocol not in protocols
            or (protocol == "ex" and dialect is not None and endpoint_dialect != dialect)
            or (host, port) in seen
        ):
            continue
        seen.add((host, port))
        result.append(
            {
                "host": host,
                "port": port,
                "name": item.get("name"),
                "protocol": protocol,
                "dialect": endpoint_dialect if protocol == "ex" else None,
            }
        )
    if not result:
        raise ValueError(f"allip.json has no {protocol} candidates")
    return result


def ranked(protocol: str, *, dialect: str | None = None) -> list[dict[str, Any]]:
    """Return device-local verified endpoints, preserving the recorded rank."""
    if protocol not in _PROTOCOLS:
        raise ValueError(f"Unsupported protocol: {protocol}")
    payload = _read(BESTIP_PATH)
    groups = payload.get("groups", {})
    values = groups.get(protocol, []) if isinstance(groups, dict) else []
    result = []
    seen: set[tuple[str, int]] = set()
    for item in values:
        host, port = item.get("host"), item.get("port")
        if not isinstance(host, str) or not isinstance(port, int) or (host, port) in seen:
            continue
        if protocol == "ex" and dialect is not None and item.get("dialect", "standard") != dialect:
            continue
        seen.add((host, port))
        result.append(dict(item))
    return result


def preferred(protocol: str, *, dialect: str | None = None) -> list[dict[str, Any]]:
    """Use local verified rank first, then static candidates not already ranked."""
    ranked_items = ranked(protocol, dialect=dialect)
    seen = {(item["host"], item["port"]) for item in ranked_items}
    remaining = [
        item
        for item in candidates(protocol, dialect=dialect)
        if (item["host"], item["port"]) not in seen
    ]
    return ranked_items + remaining


def save_rankings(groups: dict[str, list[dict[str, Any]]]) -> None:
    """Atomically replace all successful group rankings on this device."""
    invalid = set(groups) - _PROTOCOLS
    if invalid:
        raise ValueError(f"Unsupported ranking groups: {sorted(invalid)}")
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
        "groups": groups,
    }
    BESTIP_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = BESTIP_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
    temporary.replace(BESTIP_PATH)
