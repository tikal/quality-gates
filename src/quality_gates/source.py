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
from pathlib import Path

UNPARSABLE = "unparsable"

READ_FAILURES = (SyntaxError, UnicodeDecodeError, ValueError, LookupError, OSError)


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
