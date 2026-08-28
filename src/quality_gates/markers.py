"""Define marker blocks once for the comment and marker-budget gates.

Python tokenization is exact. The other scanned source languages use bundled Tree-sitter grammars,
so comments are syntax nodes rather than patterns that can match source data.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

from tree_sitter_language_pack import get_parser

from quality_gates.source import UnreadableSource, comment_tokens

MARKER_PATTERN = re.compile(r"(?:#|//|/\*|\*)\s*(?:TODO|FIXME|NOTE|HACK|XXX)\b")
BADGE_PATTERN = re.compile(r"^#\s*(?:ALLOW:|TYPE:)", re.IGNORECASE)
PRAGMA_PATTERN = re.compile(r"#\s*(?:noqa|type:\s*ignore|pyright:|mypy:|ruff:|nosec)\b", re.IGNORECASE)
MAX_BLOCK_LINES = 10


class CommentLine(NamedTuple):
    row: int
    column: int
    text: str


class FileComments(NamedTuple):
    own_line: list[CommentLine]
    trailing: list[CommentLine]


class _MarkerUnreadableSource(UnreadableSource):
    """A source error and the conservative marker count available before it."""

    def __init__(self, line: int, reason: str, marker_count: int = 0) -> None:
        super().__init__(line, reason)
        self.marker_count = marker_count


def is_marker_header(text: str) -> bool:
    """True when a comment opens with a marker rather than merely mentioning one."""
    return MARKER_PATTERN.match(text.strip().lstrip("{")) is not None


def is_lint_pragma(text: str) -> bool:
    """True for a lint pragma, which is not ordinary comment prose."""
    return PRAGMA_PATTERN.search(text) is not None


def is_badge(text: str) -> bool:
    """True for a machine-readable ALLOW or TYPE badge."""
    return BADGE_PATTERN.match(text.strip()) is not None


def marker_blocks(comments: Sequence[CommentLine]) -> list[list[CommentLine]]:
    """Group a marker header with direct, aligned comment body lines."""
    blocks: list[list[CommentLine]] = []
    for comment in comments:
        if is_marker_header(comment.text):
            blocks.append([comment])
        elif blocks and blocks[-1][-1].row == comment.row - 1 and blocks[-1][-1].column == comment.column:
            blocks[-1].append(comment)
    return blocks


def python_comments(source: str) -> FileComments:
    """Read Python comment tokens and preserve a conservative count if tokenization fails."""
    try:
        tokens = comment_tokens(source)
    except UnreadableSource as exc:
        fallback = _python_fallback_comments(source)
        count = len(marker_blocks(fallback.own_line)) + len(fallback.trailing)
        raise _MarkerUnreadableSource(exc.line, exc.reason, count) from exc

    comments = FileComments([], [])
    for token in tokens:
        text = token.string.strip()
        if not token.line[: token.start[1]].strip():
            comments.own_line.append(CommentLine(token.start[0], token.start[1], text))
        elif is_marker_header(text):
            comments.trailing.append(CommentLine(token.start[0], token.start[1], text))
    return comments


def _python_fallback_comments(source: str) -> FileComments:
    comments = FileComments([], [])
    for row, line in enumerate(source.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            comments.own_line.append(CommentLine(row, len(line) - len(stripped), stripped))
            continue
        for match in MARKER_PATTERN.finditer(line):
            if match.group().startswith("#") and line[: match.start()].strip():
                comments.trailing.append(CommentLine(row, match.start(), line[match.start() :].strip()))
    return comments


def _add_line_comment(comments: FileComments, row: int, column: int, text: str, has_code: bool) -> None:
    comment = CommentLine(row, column, text.strip())
    if has_code:
        if is_marker_header(comment.text):
            comments.trailing.append(comment)
        return
    comments.own_line.append(comment)


def _add_block_comment(comments: FileComments, row: int, column: int, text: str, has_code: bool) -> None:
    lines = text.splitlines()
    if not lines:
        return
    if has_code:
        _add_line_comment(comments, row, column, lines[0], True)
        for offset, line in enumerate(lines[1:], start=1):
            if line.strip():
                comments.own_line.append(CommentLine(row + offset, len(line) - len(line.lstrip()), line.strip()))
        return
    for offset, line in enumerate(lines):
        if line.strip():
            line_column = column if offset == 0 else len(line) - len(line.lstrip())
            comments.own_line.append(CommentLine(row + offset, line_column, line.strip()))


def _tree_sitter_comments(source: str, language: str) -> FileComments:
    source_bytes = source.encode("utf-8")
    root = get_parser(language).parse(source_bytes).root_node
    if root.has_error:
        raise _MarkerUnreadableSource(_error_line(root), f"{language} syntax error")
    if language == "bash" and _has_node(root, "heredoc_redirect"):
        raise _MarkerUnreadableSource(1, "bash heredoc parsing is unsupported")

    comments = FileComments([], [])
    for node in _comment_nodes(root):
        text = source_bytes[node.start_byte : node.end_byte].decode("utf-8")
        line_start = source_bytes.rfind(b"\n", 0, node.start_byte) + 1
        has_code = bool(source_bytes[line_start : node.start_byte].strip())
        if "\n" in text:
            _add_block_comment(comments, node.start_point.row + 1, node.start_point.column, text, has_code)
        else:
            _add_line_comment(comments, node.start_point.row + 1, node.start_point.column, text, has_code)
    return comments


def _comment_nodes(root: object) -> list[object]:
    nodes = [root]
    comments = []
    while nodes:
        node = nodes.pop()
        if node.type == "comment":
            comments.append(node)
        else:
            nodes.extend(reversed(node.children))
    return comments


def _error_line(root: object) -> int:
    nodes = [root]
    while nodes:
        node = nodes.pop()
        if node.is_error or node.is_missing:
            return node.start_point.row + 1
        nodes.extend(reversed(node.children))
    return 1


def _has_node(root: object, node_type: str) -> bool:
    nodes = [root]
    while nodes:
        node = nodes.pop()
        if node.type == node_type:
            return True
        nodes.extend(reversed(node.children))
    return False


def _javascript_comments(source: str) -> FileComments:
    return _tree_sitter_comments(source, "javascript")


def _typescript_comments(source: str) -> FileComments:
    return _tree_sitter_comments(source, "typescript")


def _tsx_comments(source: str) -> FileComments:
    return _tree_sitter_comments(source, "tsx")


def _go_comments(source: str) -> FileComments:
    return _tree_sitter_comments(source, "go")


def _shell_comments(source: str) -> FileComments:
    return _tree_sitter_comments(source, "bash")


COMMENT_READERS = {
    ".py": python_comments,
    ".ts": _typescript_comments,
    ".tsx": _tsx_comments,
    ".js": _javascript_comments,
    ".jsx": _tsx_comments,
    ".mjs": _javascript_comments,
    ".go": _go_comments,
    ".sh": _shell_comments,
}

SCANNED_SUFFIXES = frozenset(COMMENT_READERS)


def count_blocks_in(path: Path) -> int:
    """Marker blocks in a file, using the reader declared for its suffix."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise _MarkerUnreadableSource(1, f"{type(exc).__name__}: {exc}") from exc
    try:
        comments = COMMENT_READERS[path.suffix](source)
    except KeyError as exc:
        raise ValueError(f"no comment reader for {path.suffix}") from exc
    return len(marker_blocks(comments.own_line)) + len(comments.trailing)


def marker_headers(source: str, suffix: str) -> list[CommentLine]:
    """Marker header comments from already-decoded source text."""
    try:
        if suffix == ".py":
            ast.parse(source)
        comments = COMMENT_READERS[suffix](source)
    except (KeyError, SyntaxError) as exc:
        raise ValueError(f"no comment reader for {suffix}") from exc
    return [*(block[0] for block in marker_blocks(comments.own_line)), *comments.trailing]


def is_scannable(relative_path: str) -> bool:
    """True for declared non-generated source files that the marker budget reads."""
    path = Path(relative_path)
    return path.suffix in SCANNED_SUFFIXES and ".generated." not in path.name
