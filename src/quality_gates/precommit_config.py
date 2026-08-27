"""Read the small, auditable pre-commit YAML subset used by enrollment gates."""

from __future__ import annotations

from pathlib import Path


class ConfigError(Exception):
    """A pre-commit configuration cannot be safely inventoried."""


def hooks_from(path: Path) -> list[tuple[str, str | None, str | None]]:  # noqa: C901
    """Return configured hook IDs, optional files patterns, and entries."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    hooks: list[tuple[str, str | None, str | None]] = []
    current: str | None = None
    files: str | None = None
    entry: str | None = None
    for number, raw in enumerate(lines, 1):
        line = _without_comment(raw).rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- id:"):
            if current is not None:
                hooks.append((current, files, entry))
            current = _scalar(stripped.removeprefix("- id:"), path, number)
            files = None
            entry = None
        elif current is not None and stripped.startswith("files:"):
            files = _scalar(stripped.removeprefix("files:"), path, number)
        elif current is not None and stripped.startswith("entry:"):
            entry = _scalar(stripped.removeprefix("entry:"), path, number)
    if current is not None:
        hooks.append((current, files, entry))
    if not hooks:
        raise ConfigError(f"{path}: no hook declarations found")
    if len({hook_id for hook_id, _, _ in hooks}) != len(hooks):
        raise ConfigError(f"{path}: duplicate hook id")
    return hooks


def _scalar(value: str, path: Path, number: int) -> str:
    value = value.strip()
    if len(value) > 1 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    if not value or value[0] in "[{|>&*!":
        raise ConfigError(f"{path}:{number}: id and files must use a non-empty plain scalar")
    return value


def _without_comment(value: str) -> str:
    quote = ""
    for index, character in enumerate(value):
        if character in "'\"":
            quote = "" if quote == character else character if not quote else quote
        elif character == "#" and not quote and (index == 0 or value[index - 1].isspace()):
            return value[:index]
    return value
