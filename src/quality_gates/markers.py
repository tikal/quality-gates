from __future__ import annotations

import io
import re
import tokenize
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import NamedTuple

MARKER_PATTERN = re.compile(r"(?:#|//|/\*|\*)\s*(?:TODO|FIXME|NOTE|HACK|XXX)\b")

BADGE_PATTERN = re.compile(r"^#\s*(?:ALLOW:|TYPE:)", re.IGNORECASE)

PRAGMA_PATTERN = re.compile(r"#\s*(?:noqa|type:\s*ignore|pyright:|mypy:|ruff:|nosec)\b", re.IGNORECASE)

COMMENT_PREFIXES = ("#", "//", "/*", "*", "{/*")

SCANNED_SUFFIXES = frozenset({".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".go", ".sh"})

MAX_BLOCK_LINES = 10


class CommentLine(NamedTuple):
    row: int
    column: int
    text: str


def is_marker_header(text: str) -> bool:
    """True when a comment opens with a marker, rather than merely mentioning one.

    Anchored on purpose. `# NOTE: x` is a marker; `# the NOTE above` and
    `# Both axes pass. NOTE: x` are prose that happens to contain the word.

    The preservation hook matches the same pattern unanchored, so it guards a marker this
    reads as prose. That is deliberate: the hook protects anything that looks like a marker,
    while the gate only credits one that leads its comment.

    A leading `{` is dropped so a JSX comment counts. `{/* NOTE: x */}` is the frontend's
    only way to comment inside markup, and the hook's unanchored match already guards those
    — so without this they were protected from removal but invisible to every budget.
    """
    return MARKER_PATTERN.match(text.strip().lstrip("{")) is not None


def is_lint_pragma(text: str) -> bool:
    """True for `# noqa`, `# type: ignore` and friends, which may sit anywhere in the comment."""
    return PRAGMA_PATTERN.search(text) is not None


def is_badge(text: str) -> bool:
    """True for the `# ALLOW:` / `# TYPE:` prefixes the repo uses to mark a deliberate choice."""
    return BADGE_PATTERN.match(text.strip()) is not None


def marker_blocks(comments: Sequence[CommentLine]) -> list[list[CommentLine]]:
    """Group comments into marker blocks: a marker line plus the body directly under it.

    A body line must sit on the next row at the same column, which stops an unrelated comment
    further down from being absorbed. Comments outside any block are dropped.
    """
    blocks: list[list[CommentLine]] = []
    for comment in comments:
        if is_marker_header(comment.text):
            blocks.append([comment])
        elif blocks and blocks[-1][-1].row == comment.row - 1 and blocks[-1][-1].column == comment.column:
            blocks[-1].append(comment)
    return blocks


def own_line_comments(lines: Iterable[str]) -> list[CommentLine]:
    """Comments that occupy a whole line, in any language this repo uses."""
    return [
        CommentLine(row, len(line) - len(line.lstrip()), line.strip())
        for row, line in enumerate(lines, start=1)
        if line.strip().startswith(COMMENT_PREFIXES)
    ]


def trailing_markers(lines: Iterable[str]) -> list[CommentLine]:
    """Markers that sit after code on the same line, as in `x = 1  # NOTE: y`.

    Each is its own block: a trailing marker opens nothing, because the line below it belongs
    to the code, not to the comment. Counting these closes an escape hatch — without them, a
    marker moved to the end of the line above disappears from every budget.
    """
    found = []
    for row, line in enumerate(lines, start=1):
        if line.strip().startswith(COMMENT_PREFIXES):
            continue
        match = MARKER_PATTERN.search(line)
        if match and line[: match.start()].strip():
            found.append(CommentLine(row, match.start(), line[match.start() :].strip()))
    return found


def python_trailing_markers(source: str) -> list[CommentLine]:
    """Trailing markers in Python, read from real comment tokens rather than from text.

    `tokenize` is exact where a regex is not: it never mistakes `edit("# NOTE: x")` for a
    comment. That matters because the repo keeps marker text in string literals as test
    fixtures, and a text scan counts 45 of them in one file.
    """
    found = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type != tokenize.COMMENT:
                continue
            if token.line[: token.start[1]].strip() and is_marker_header(token.string.strip()):
                found.append(CommentLine(token.start[0], token.start[1], token.string.strip()))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return []
    return found


def count_blocks(lines: Iterable[str]) -> int:
    """Marker blocks in a file. This is the repo's canonical marker count."""
    rows = list(lines)
    return len(marker_blocks(own_line_comments(rows))) + len(trailing_markers(rows))


def may_trail_code(source: str) -> bool:
    """Whether any marker in `source` has code before it on its line.

    A cheap superset of python_trailing_markers, used to skip tokenize where it cannot find
    anything. tokenize only yields a comment token preceded by code when the raw line has
    non-blank text before the marker, so a file this rejects has no trailing marker to find.
    Worth the indirection: only 15 of 484 tracked .py files reach the exact reader, and the
    other 469 were paying 900ms per commit to be told they hold nothing.
    """
    return any(
        source[source.rfind("\n", 0, match.start()) + 1 : match.start()].strip()
        for match in MARKER_PATTERN.finditer(source)
    )


def count_blocks_in(path: Path) -> int:
    """Marker blocks in a file, using the exact reader where the language has one."""
    source = path.read_text(encoding="utf-8", errors="replace")
    lines = source.splitlines()
    if path.suffix == ".py":
        trailing = python_trailing_markers(source) if may_trail_code(source) else []
    else:
        trailing = trailing_markers(lines)
    return len(marker_blocks(own_line_comments(lines))) + len(trailing)


def is_scannable(relative_path: str) -> bool:
    """Files the marker rules apply to. Generated output and Markdown are out of scope.

    Markdown is excluded because the ladder pushes long prose INTO docs/, so counting it
    there would fight the rule it exists to serve.
    """
    path = Path(relative_path)
    return path.suffix in SCANNED_SUFFIXES and ".generated." not in path.name
