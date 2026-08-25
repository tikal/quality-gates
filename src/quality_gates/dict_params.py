"""Fail on a dict annotation in a public parameter or return type.

A dict carries no contract, so a public signature that takes or returns one hides its shape from
every caller. A dataclass, a TypedDict or a BaseModel states it. A deliberate exception carries
the badge `# ALLOW: dict-param` or `# ALLOW: dict-return` on the line where the annotation ends.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from collections.abc import Collection, Sequence
from pathlib import Path
from typing import NamedTuple

from quality_gates.discovery import DEFAULT_SOURCE_DIRECTORIES, SKIPPED_DIRECTORIES, python_files_under
from quality_gates.source import UnreadableSource, parse_source, read_source

WRAPPER_TYPES = {
    "Optional",
    "list",
    "List",
    "set",
    "Set",
    "tuple",
    "Tuple",
    "Sequence",
    "Iterable",
    "Collection",
    "frozenset",
    "FrozenSet",
}

DICT_NAMES = ("dict", "Dict", "Mapping", "MutableMapping")
QUALIFIED_DICT_NAMES = {
    "builtins.dict",
    "typing.Dict",
    "typing.Mapping",
    "typing.MutableMapping",
    "collections.abc.Mapping",
    "collections.abc.MutableMapping",
}

BADGE_PATTERN = re.compile(r"#\s*ALLOW:\s*([\w-]+)")

BADGE_ADVICE = (
    "Consider using @dataclass, TypedDict or BaseModel. To keep one, put `# ALLOW: dict-param` or "
    "`# ALLOW: dict-return` ON THE LINE WHERE THE ANNOTATION ENDS."
)


class Finding(NamedTuple):
    path: str
    line: int
    col: int
    func: str
    param: str


class Unreadable(NamedTuple):
    path: str
    line: int
    reason: str


class Report(NamedTuple):
    findings: list[Finding]
    unreadable: list[Unreadable]


def _head_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _qualified_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        value = _qualified_name(node.value)
        return f"{value}.{node.attr}" if value else ""
    return ""


def _is_dict_name(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id in DICT_NAMES
    return _qualified_name(node) in QUALIFIED_DICT_NAMES


def _is_dict_subscript(ann: ast.Subscript) -> bool:
    head = _head_name(ann.value)
    if _is_dict_name(ann.value):
        return True
    if head not in WRAPPER_TYPES:
        return False
    if isinstance(ann.slice, ast.Tuple):
        return any(_is_dict_type(element) for element in ann.slice.elts)
    return _is_dict_type(ann.slice)


def _is_quoted_dict(text: str) -> bool:
    try:
        return _is_dict_type(ast.parse(text, mode="eval").body)
    except (SyntaxError, ValueError):
        return False


def _is_dict_type(ann: ast.AST) -> bool:
    if isinstance(ann, ast.Name | ast.Attribute):
        return _is_dict_name(ann)
    if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
        return _is_quoted_dict(ann.value)
    if isinstance(ann, ast.Subscript):
        return _is_dict_subscript(ann)
    if isinstance(ann, ast.BinOp) and isinstance(ann.op, ast.BitOr):
        return _is_dict_type(ann.left) or _is_dict_type(ann.right)
    return False


def _annotation_text(ann: ast.AST) -> str:
    if isinstance(ann, ast.Name):
        return ann.id
    if isinstance(ann, ast.Subscript):
        if isinstance(ann.slice, ast.Tuple):
            args = ", ".join(_annotation_text(element) for element in ann.slice.elts)
        else:
            args = _annotation_text(ann.slice)
        return f"{_annotation_text(ann.value)}[{args}]"
    if isinstance(ann, ast.BinOp) and isinstance(ann.op, ast.BitOr):
        return f"{_annotation_text(ann.left)} | {_annotation_text(ann.right)}"
    if isinstance(ann, ast.Constant):
        return "None" if ann.value is None else repr(ann.value)
    if isinstance(ann, ast.Attribute):
        return f"{_annotation_text(ann.value)}.{ann.attr}"
    return "..."


def _has_badge(line: str, badge: str) -> bool:
    return any(match.group(1) == badge for match in BADGE_PATTERN.finditer(line))


def _star_arguments(args: ast.arguments) -> list[ast.arg]:
    return [arg for arg in (args.vararg, args.kwarg) if arg is not None]


class DictParamAnalyzer(ast.NodeVisitor):
    """Collects every dict annotation in a public signature that carries no ALLOW badge.

    The badge is read from the line where the ANNOTATION ends, not from the `def` line, so a
    signature split over several lines can badge the parameter the rule is actually about. A
    one-line signature therefore has one badge line, and one badge there exempts every dict
    parameter on it.
    """

    def __init__(self, path: str, source_lines: list[str]) -> None:
        self.path = path
        self.source_lines = source_lines
        self.findings: list[Finding] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Visit a synchronous function definition."""
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Visit an asynchronous function definition."""
        self._visit_function(node)

    def _badge_line(self, ann: ast.expr) -> str:
        row = ann.end_lineno or 0
        return self.source_lines[row - 1] if 0 < row <= len(self.source_lines) else ""

    def _check_argument(self, node: ast.FunctionDef | ast.AsyncFunctionDef, arg: ast.arg) -> None:
        if not arg.annotation or not _is_dict_type(arg.annotation):
            return
        if _has_badge(self._badge_line(arg.annotation), "dict-param"):
            return
        subject = f"{arg.arg}: {_annotation_text(arg.annotation)}"
        self.findings.append(Finding(self.path, arg.lineno, arg.col_offset + 1, node.name, subject))

    def _check_return(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if not node.returns or not _is_dict_type(node.returns):
            return
        if _has_badge(self._badge_line(node.returns), "dict-return"):
            return
        subject = f"-> {_annotation_text(node.returns)}"
        self.findings.append(Finding(self.path, node.returns.lineno, node.returns.col_offset + 1, node.name, subject))

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if node.name.startswith("_"):
            self.generic_visit(node)
            return
        arguments = node.args
        for arg in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs, *_star_arguments(arguments)]:
            self._check_argument(node, arg)
        self._check_return(node)
        self.generic_visit(node)


def analyze_file(file_path: Path) -> list[Finding]:
    """Every dict annotation in a public signature in one file.

    Raises UnreadableSource when the file cannot be decoded or parsed. A file this gate cannot
    read is a violation for the caller to report, never a file the gate quietly passes.
    """
    source = read_source(file_path)
    analyzer = DictParamAnalyzer(str(file_path), source.splitlines())
    analyzer.visit(parse_source(source))
    return analyzer.findings


def analyze(paths: Sequence[Path]) -> Report:
    """Every dict finding across `paths`, plus every file that could not be read at all."""
    findings: list[Finding] = []
    unreadable: list[Unreadable] = []
    for file_path in paths:
        try:
            findings.extend(analyze_file(file_path))
        except UnreadableSource as exc:
            unreadable.append(Unreadable(str(file_path), exc.line, exc.reason))
    return Report(findings, unreadable)


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check for dict annotations in public signatures.")
    parser.add_argument("paths", nargs="*", default=[Path(".")], type=Path)
    parser.add_argument(
        "--src-dir",
        action="append",
        default=[],
        metavar="NAME",
        help=f"an extra directory to descend into at a project root, added to {DEFAULT_SOURCE_DIRECTORIES}; repeatable",
    )
    parser.add_argument(
        "--skip-dir",
        action="append",
        default=[],
        metavar="NAME",
        help="an extra directory name to skip anywhere in a path, added to the defaults; repeatable",
    )
    return parser.parse_args()


def _scan(paths: Sequence[Path], src_dirs: Sequence[str], skip_dirs: Collection[str]) -> list[Path]:
    return [found for path in paths for found in python_files_under(path, src_dirs, skip_dirs)]


def _print_failures(report: Report) -> int:
    if report.findings:
        print("Dict type annotations found in public method parameters and return types:")
        print(f"{BADGE_ADVICE}\n")
        for found in sorted(report.findings):
            print(f"  {found.path}:{found.line}:{found.col} {found.func}({found.param})")
    if report.unreadable:
        print(f"\n{len(report.unreadable)} file(s) could not be read. A file a gate cannot read is a violation:")
        for entry in sorted(report.unreadable):
            print(f"  {entry.path}:{entry.line} - File cannot be read: {entry.reason}")
    return 1


def main() -> int:
    """Check every path given and report each public signature that takes or returns a dict."""
    arguments = _parse_arguments()
    for path in arguments.paths:
        if not path.exists():
            print(f"Path {path} does not exist", file=sys.stderr)
            return 1

    scanned = _scan(
        arguments.paths,
        (*DEFAULT_SOURCE_DIRECTORIES, *arguments.src_dir),
        SKIPPED_DIRECTORIES | frozenset(arguments.skip_dir),
    )
    if not scanned:
        named = ", ".join(str(path) for path in arguments.paths)
        print(
            f"dict-param-check found 0 Python files under {named} — a clean result would be meaningless",
            file=sys.stderr,
        )
        return 1

    report = analyze(scanned)
    if report.findings or report.unreadable:
        return _print_failures(report)

    print(f"No dict type annotations in public signatures across {len(scanned)} Python files scope={len(scanned)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
