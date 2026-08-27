"""Reading a Python source file, where a file a gate cannot read is a violation, not a skip.

A gate that silently skips what it cannot parse is defeated by committing a file it cannot parse:
the file's comments and signatures are then hidden from the gate forever, while the file is still
counted in the scope the gate reports. Every failure to decode or parse reaches the caller as
UnreadableSource, so one reader gives every gate the same verdict on the same file.
"""

from __future__ import annotations

import ast
import io
import tokenize
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import TypeVar

UNPARSABLE = "unparsable"

READ_FAILURES = (SyntaxError, UnicodeDecodeError, ValueError, LookupError, OSError)
T = TypeVar("T")


class UnreadableSource(Exception):
    """A Python file a gate must read but cannot decode or parse."""

    def __init__(self, line: int, reason: str) -> None:
        super().__init__(reason)
        self.line = line
        self.reason = reason


def read_source(path: Path) -> str:
    """The text of a Python file, decoded through its own encoding declaration."""
    try:
        with tokenize.open(path) as handle:
            return handle.read()
    except READ_FAILURES as exc:
        raise _unreadable(exc) from exc


def parse_source(source: str) -> ast.Module:
    """The syntax tree of already-decoded Python source."""
    try:
        return ast.parse(source)
    except READ_FAILURES as exc:
        raise _unreadable(exc) from exc


def parsed_sources(paths: Iterable[Path], project_root: Path) -> Iterator[tuple[str, ast.Module | UnreadableSource]]:
    """Yield every source's stable name and parsed tree, preserving read failures."""
    for path in unique_paths(paths):
        absolute = path.absolute()
        try:
            resolved = path.resolve()
            try:
                anchor = resolved.relative_to(project_root).as_posix()
            except ValueError:
                anchor = resolved.as_posix()
        except (OSError, RuntimeError) as exc:
            yield absolute.as_posix(), UnreadableSource(1, f"{type(exc).__name__}: {exc}")
            continue
        try:
            yield anchor, parse_source(read_source(path))
        except UnreadableSource as exc:
            yield anchor, exc


def unique_paths(paths: Iterable[Path]) -> list[Path]:
    """Keep each explicitly selected filesystem path once, in discovery order."""
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        absolute = path.absolute()
        if absolute not in seen:
            seen.add(absolute)
            unique.append(path)
    return unique


def analyze_sources(
    paths: Iterable[Path],
    project_root: Path,
    analyze: Callable[[str, ast.Module], Iterable[T]],
    unreadable: Callable[[str, UnreadableSource], T],
) -> list[T]:
    """Analyze every declared source while converting unreadable files to findings."""
    findings: list[T] = []
    for anchor, result in parsed_sources(paths, project_root):
        if isinstance(result, UnreadableSource):
            findings.append(unreadable(anchor, result))
        else:
            findings.extend(analyze(anchor, result))
    return findings


def comment_tokens(source: str) -> list[tokenize.TokenInfo]:
    """Every comment token in already-decoded Python source."""
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        return [token for token in tokens if token.type == tokenize.COMMENT]
    except tokenize.TokenError as exc:
        raise UnreadableSource(_token_error_line(exc), f"TokenError: {exc.args[0]}") from exc
    except READ_FAILURES as exc:
        raise _unreadable(exc) from exc


def _unreadable(exc: Exception) -> UnreadableSource:
    detail = getattr(exc, "msg", None) or exc
    return UnreadableSource(getattr(exc, "lineno", None) or 1, f"{type(exc).__name__}: {detail}")


def _token_error_line(exc: tokenize.TokenError) -> int:
    position = exc.args[1] if len(exc.args) > 1 else None
    return position[0] if isinstance(position, tuple) and position else 1
