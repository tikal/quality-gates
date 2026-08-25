"""What counts as a TODO/FIXME/NOTE marker, and how markers group into blocks.

Shared by the comment gate, which caps how long one block may run, and by the marker budget,
which caps how many blocks a repository may hold. Both must agree on what a marker is, so the
definition lives here once and neither gate carries its own.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import NamedTuple

from quality_gates.source import UnreadableSource, comment_tokens

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


class FileComments(NamedTuple):
    own_line: list[CommentLine]
    trailing: list[CommentLine]


def is_marker_header(text: str) -> bool:
    """True when a comment opens with a marker, rather than merely mentioning one.

    Anchored on purpose. `# NOTE: x` is a marker; `# the NOTE above` and
    `# Both axes pass. NOTE: x` are prose that happens to contain the word.

    A leading `{` is dropped so a JSX comment counts. `{/* NOTE: x */}` is the only way to
    comment inside markup, so without this a marker written there is invisible to every budget.
    """
    return MARKER_PATTERN.match(text.strip().lstrip("{")) is not None


def is_lint_pragma(text: str) -> bool:
    """True for `# noqa`, `# type: ignore` and friends, which may sit anywhere in the comment."""
    return PRAGMA_PATTERN.search(text) is not None


def is_badge(text: str) -> bool:
    """True for the `# ALLOW:` and `# TYPE:` prefixes that mark a deliberate choice.

    A badge is a machine-readable declaration, not prose, so it is exempt from the comment rules
    and from every budget.
    """
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
    """Comments that occupy a whole line, read from raw text for a language with no reader here."""
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


def python_comments(source: str) -> FileComments:
    """Own-line comments and trailing markers in Python, read from comment tokens, not from text.

    `tokenize` is exact where a text scan is not. A text scan reads marker text held in a string
    literal as a comment, and `*` in COMMENT_PREFIXES makes it read a `* NOTE:` bullet inside a
    docstring as one too. Either inflates a budget with text that is data, not a marker.

    A file the tokeniser rejects falls back to the text scan. Under-counting it would let a
    marker hide behind a syntax error, which is the escape hatch this gate exists to close.
    """
    try:
        tokens = comment_tokens(source)
    except UnreadableSource:
        return text_comments(source)

    own_line: list[CommentLine] = []
    trailing: list[CommentLine] = []
    for token in tokens:
        text = token.string.strip()
        if not token.line[: token.start[1]].strip():
            own_line.append(CommentLine(token.start[0], token.start[1], text))
        elif is_marker_header(text):
            trailing.append(CommentLine(token.start[0], token.start[1], text))
    return FileComments(own_line, trailing)


def text_comments(source: str) -> FileComments:
    """Own-line comments and trailing markers read from raw text, for a language with no reader here."""
    lines = source.splitlines()
    return FileComments(own_line_comments(lines), trailing_markers(lines))


def count_blocks_in(path: Path) -> int:
    """Marker blocks in a file, using the exact reader where the language has one."""
    source = path.read_text(encoding="utf-8", errors="replace")
    comments = python_comments(source) if path.suffix == ".py" else text_comments(source)
    return len(marker_blocks(comments.own_line)) + len(comments.trailing)


def is_scannable(relative_path: str) -> bool:
    """Files the marker rules apply to. Generated output and Markdown are out of scope.

    Markdown is excluded because the rules push long prose OUT of the code and into documents,
    so counting markers there would fight the rule the budget exists to serve.
    """
    path = Path(relative_path)
    return path.suffix in SCANNED_SUFFIXES and ".generated." not in path.name
