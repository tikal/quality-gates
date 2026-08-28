"""Strict JSON and path helpers for consumer-owned security policy files."""

from __future__ import annotations

import json
from pathlib import Path


def reject_constant(value: str) -> None:
    """Reject non-standard JSON numbers instead of silently accepting them."""
    raise ValueError(f"invalid JSON constant {value}")


def reject_duplicate_key(pairs: list[tuple[str, object]]) -> dict[str, object]:  # ALLOW: dict-return
    """Reject duplicate keys because JSON otherwise discards reviewed policy."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def read_json(path: Path, label: str) -> object:
    """Read strict UTF-8 JSON with an actionable policy error."""
    try:
        return json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicate_key, parse_constant=reject_constant
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"cannot parse {label} {path}: {type(exc).__name__}: {exc}") from exc


def relative_path(value: object, label: str) -> str:
    """Validate a root-relative portable policy path."""
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"{label} must be a nonblank repository-relative POSIX path")
    path = Path(value)
    if path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise ValueError(f"{label} must be a nonblank repository-relative POSIX path")
    return path.as_posix()


def text(value: object, label: str) -> str:
    """Validate required policy prose and identifiers."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonblank string")
    return value


def versioned_list(value: object, key: str, label: str) -> list[object]:
    """Validate the common version-one JSON collection envelope."""
    if (
        not isinstance(value, dict)
        or set(value) != {"version", key}
        or type(value["version"]) is not int
        or value["version"] != 1
        or not isinstance(value[key], list)
    ):
        raise ValueError(f"{label} must contain exactly version 1 and {key}")
    return value[key]
