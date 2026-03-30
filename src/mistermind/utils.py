"""
General-purpose utility functions shared across the engine.

These are small, side-effect-free helpers that don't belong to any single
domain module (parsing, scoring, state, etc.).
"""

from __future__ import annotations

import base64
import copy
import datetime as dt
import json
from pathlib import Path
from typing import Any

from mistermind.constants import (
    PACKAGE_ASSETS_ROOT,
    PACKAGE_CONFIG_ROOT,
    PROJECT_ROOT,
)


def resolve_runtime_path(path_str: str) -> Path:
    """Resolve assets/config paths from repo root, with packaged fallback."""
    candidate = Path(path_str)
    if candidate.is_absolute():
        return candidate

    project_path = PROJECT_ROOT / candidate
    if project_path.exists():
        return project_path

    parts = candidate.parts
    if len(parts) >= 2 and parts[0] == "assets":
        return PACKAGE_ASSETS_ROOT.joinpath(*parts[1:])
    if len(parts) >= 2 and parts[0] == "config":
        return PACKAGE_CONFIG_ROOT.joinpath(*parts[1:])
    return project_path


def merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _as_int(value: Any, fallback: int, minimum: int) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError):
        return fallback
    return out if out >= minimum else fallback


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso_utc(value: str | None) -> dt.datetime | None:
    if value is None:
        return None
    value = value.strip()
    if not value or value == "0":
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.UTC)
    except ValueError:
        return None


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def b64url_decode(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(value + padding)


def canonical_json(data: dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def issue_room_key(repo: str, issue_number: int) -> str:
    return f"issue:{repo}#{issue_number}"
