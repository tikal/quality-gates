"""Fail on a dict annotation in a public parameter or return type.

A dict carries no contract, so a public signature that takes or returns one hides its shape from
every caller. A dataclass, a TypedDict or a BaseModel states it. A deliberate exception carries
the badge `# ALLOW: dict-param` or `# ALLOW: dict-return`.
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Collection, Sequence
from pathlib import Path
from typing import NamedTuple

from quality_gates.discovery import DEFAULT_SOURCE_DIRECTORIES, SKIPPED_DIRECTORIES, python_files_under

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

DICT_NAMES = ("dict", "Dict")


class Finding(NamedTuple):
    path: str
    line: int
    col: int
    func: str
    param: str


def _is_dict_type(ann: ast.AST) -> bool:
    if isinstance(ann, ast.Name) and ann.id in DICT_NAMES:
        return True
    if isinstance(ann, ast.Subscript) and isinstance(ann.value, ast.Name):
        if ann.value.id in DICT_NAMES:
            return True
        if ann.value.id in WRAPPER_TYPES:
            if isinstance(ann.slice, ast.Tuple):
                return any(_is_dict_type(el) for el in ann.slice.elts)
            return _is_dict_type(ann.slice)
    if isinstance(ann, ast.BinOp) and isinstance(ann.op, ast.BitOr):
        return _is_dict_type(ann.left) or _is_dict_type(ann.right)
    return False


def _annotation_text(ann: ast.AST) -> str:
    if isinstance(ann, ast.Name):
        return ann.id
    if isinstance(ann, ast.Subscript):
        if isinstance(ann.slice, ast.Tuple):
            args = ", ".join(_annotation_text(el) for el in ann.slice.elts)
        else:
            args = _annotation_text(ann.slice)
        return f"{_annotation_text(ann.value)}[{args}]"
    if isinstance(ann, ast.BinOp) and isinstance(ann.op, ast.BitOr):
        return f"{_annotation_text(ann.left)} | {_annotation_text(ann.right)}"
    if isinstance(ann, ast.Constant) and ann.value is None:
        return "None"
    if isinstance(ann, ast.Attribute):
        return f"{_annotation_text(ann.value)}.{ann.attr}"
    return "..."


def _has_badge(line: str, badge: str) -> bool:
    return f"# ALLOW: {badge}" in line or f"# ALLOW:{badge}" in line


class DictParamAnalyzer(ast.NodeVisitor):
    """Collects every dict annotation in a public signature that carries no ALLOW badge.

    The badge is read from the line where the ANNOTATION ends, not from the `def` line, so a
    signature split over several lines can badge the parameter the rule is actually about.
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
        self.findings.append(Finding(self.path, node.lineno, arg.col_offset + 1, node.name, subject))

    def _check_return(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if not node.returns or not _is_dict_type(node.returns):
            return
        if _has_badge(self._badge_line(node.returns), "dict-return"):
            return
        subject = f"-> {_annotation_text(node.returns)}"
        self.findings.append(Finding(self.path, node.lineno, node.returns.col_offset + 1, node.name, subject))

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if node.name.startswith("_"):
            self.generic_visit(node)
            return
        for arg in node.args.args + node.args.kwonlyargs + node.args.posonlyargs:
            self._check_argument(node, arg)
        self._check_return(node)
        self.generic_visit(node)


def analyze_file(file_path: Path) -> list[Finding]:
    """Every dict annotation in a public signature in one file. Unparsable files yield nothing."""
    try:
        source = file_path.read_text()
        tree = ast.parse(source, filename=str(file_path))
    except (SyntaxError, UnicodeDecodeError):
        return []

    analyzer = DictParamAnalyzer(str(file_path), source.splitlines())
    analyzer.visit(tree)
    return analyzer.findings


def _names(text: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in text.split(",") if part.strip())


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check for dict annotations in public signatures.")
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument(
        "--src-dirs",
        type=_names,
        default=DEFAULT_SOURCE_DIRECTORIES,
        help="comma-separated directories to descend into when a path is a project root",
    )
    parser.add_argument(
        "--skip-dirs",
        type=_names,
        default=tuple(sorted(SKIPPED_DIRECTORIES)),
        help="comma-separated directory names to skip anywhere in a path",
    )
    return parser.parse_args()


def _scan(paths: Sequence[Path], src_dirs: Sequence[str], skip_dirs: Collection[str]) -> list[Path]:
    return [found for path in paths for found in python_files_under(path, src_dirs, skip_dirs)]


def main() -> int:
    """Check every path given and report each public signature that takes or returns a dict."""
    arguments = _parse_arguments()
    for path in arguments.paths:
        if not path.exists():
            print(f"Path {path} does not exist", file=sys.stderr)
            return 1

    scanned = _scan(arguments.paths, arguments.src_dirs, frozenset(arguments.skip_dirs))
    if not scanned:
        named = ", ".join(str(path) for path in arguments.paths)
        print(
            f"dict-param-check found 0 Python files under {named} — a clean result would be meaningless",
            file=sys.stderr,
        )
        return 1

    findings = [finding for py_file in scanned for finding in analyze_file(py_file)]
    if findings:
        print("Dict type annotations found in public method parameters and return types:")
        print("Consider using @dataclass, TypedDict or BaseModel (or add # ALLOW: dict-param / # ALLOW: dict-return)\n")
        for found in sorted(findings):
            print(f"  {found.path}:{found.line}:{found.col} {found.func}({found.param})")
        return 1

    print(f"No dict type annotations in public signatures across {len(scanned)} Python files scope={len(scanned)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
