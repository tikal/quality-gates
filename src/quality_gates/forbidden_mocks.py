"""Fail on test mocking patterns when a repository explicitly opts into the policy.

The configured factory location is remediation text, not package policy. Test-function nesting
rules stay with repositories that use that convention, because this gate only bans mock use.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import NamedTuple

from quality_gates.discovery import (
    DEFAULT_SOURCE_DIRECTORIES,
    SKIPPED_DIRECTORIES,
    add_scope_arguments,
    python_files_under,
)
from quality_gates.source import UnreadableSource, analyze_sources, unique_paths

MOCK_CONSTRUCTORS = frozenset(("Mock", "MagicMock", "AsyncMock"))


class Finding(NamedTuple):
    path: str
    line: int
    col: int
    description: str


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _function_arguments(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.arg]:
    arguments = node.args
    return [
        *arguments.posonlyargs,
        *arguments.args,
        *arguments.kwonlyargs,
        *(argument for argument in (arguments.vararg, arguments.kwarg) if argument is not None),
    ]


class MockAnalyzer(ast.NodeVisitor):
    """Collect every banned mock pattern from one parsed Python file."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.findings: list[Finding] = []
        self.decorator_calls: set[int] = set()
        self.mock_constructors = set(MOCK_CONSTRUCTORS)
        self.patch_names = {"patch"}
        self.mock_modules: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "unittest.mock":
                self.mock_modules.add(alias.asname or "unittest")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "unittest" and any(alias.name == "mock" for alias in node.names):
            self.mock_modules.update(alias.asname or alias.name for alias in node.names if alias.name == "mock")
        elif node.module == "unittest.mock":
            for alias in node.names:
                local_name = alias.asname or alias.name
                if alias.name in MOCK_CONSTRUCTORS:
                    self.mock_constructors.add(local_name)
                elif alias.name == "patch":
                    self.patch_names.add(local_name)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Call(self, node: ast.Call) -> None:
        if id(node) not in self.decorator_calls:
            name = _call_name(node.func)
            if name in self.mock_constructors:
                self._add(node, f"{name}(...)")
            elif self._is_patch(node.func):
                self._add(node, "patch(...)")
        self.generic_visit(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if self._is_patch(target):
                self._add(decorator, "patch decorator")
                if isinstance(decorator, ast.Call):
                    self.decorator_calls.add(id(decorator))
        for argument in _function_arguments(node):
            if argument.arg == "monkeypatch":
                self._add(argument, "monkeypatch parameter")
        self.generic_visit(node)

    def _add(self, node: ast.AST, description: str) -> None:
        self.findings.append(Finding(self.path, node.lineno, node.col_offset + 1, description))

    def _is_patch(self, node: ast.expr) -> bool:
        if isinstance(node, ast.Name):
            return node.id in self.patch_names
        parts = _attribute_parts(node)
        module_patch = parts[:2] == ("unittest", "mock") or (parts and parts[0] in self.mock_modules)
        return (
            len(parts) > 1
            and ((parts[0] in self.patch_names) or module_patch)
            and (parts[0] in self.patch_names or "patch" in parts)
        )


def _analyze_tree(anchor: str, tree: ast.Module) -> list[Finding]:
    analyzer = MockAnalyzer(anchor)
    analyzer.visit(tree)
    return analyzer.findings


def _unreadable_finding(anchor: str, exc: UnreadableSource) -> Finding:
    return Finding(anchor, exc.line, 1, f"File cannot be read: {exc.reason}")


def _attribute_parts(node: ast.expr) -> tuple[str, ...]:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Attribute):
        return (*_attribute_parts(node.value), node.attr)
    return ()


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Forbid test mocking patterns in an explicit Python source scope.")
    parser.add_argument("paths", nargs="*", default=[Path(".")], type=Path)
    parser.add_argument(
        "--factory-location",
        required=True,
        metavar="PATH",
        help="repository location of the approved test-double factory",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="root that every reported path is named relative to (default: current directory)",
    )
    add_scope_arguments(parser)
    arguments = parser.parse_args()
    for path in arguments.paths:
        if not path.exists():
            parser.error(f"Path {path} does not exist")
    return arguments


def main() -> int:
    """Check each named path and direct repairs to the configured test-double factory."""
    arguments = _parse_arguments()
    scanned: list[Path] = []
    for path in arguments.paths:
        scanned.extend(
            python_files_under(
                path,
                (*DEFAULT_SOURCE_DIRECTORIES, *arguments.src_dir),
                SKIPPED_DIRECTORIES | frozenset(arguments.skip_dir),
            )
        )
    scanned = unique_paths(scanned)
    if not scanned:
        named = ", ".join(str(path) for path in arguments.paths)
        print(
            f"forbidden-mocks scanned 0 Python files under {named}; a clean result would be meaningless",
            file=sys.stderr,
        )
        return 1

    findings = analyze_sources(scanned, arguments.project_root.resolve(), _analyze_tree, _unreadable_finding)
    if not findings:
        print(f"No forbidden mock use across {len(scanned)} Python files scope={len(scanned)}")
        return 0

    print("Forbidden mock use found:", file=sys.stderr)
    print(f"Use the approved test-double factory at {arguments.factory_location} instead.", file=sys.stderr)
    for finding in sorted(findings):
        print(f"  {finding.path}:{finding.line}:{finding.col} {finding.description}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
