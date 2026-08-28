"""Enforce an explicit pytest-describe test hierarchy."""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import NamedTuple

from quality_gates.source import UnreadableSource, analyze_sources, unique_paths

SCENARIO_PREFIXES = ("when_", "with_", "without_", "and_")
PRECONDITION_PREFIXES = ("given_", "for_")
TEST_PREFIXES = ("test_", "it_")
CONDITION_INFIXES = ("_when_", "_with_", "_without_", "_and_", "_given_", "_for_")


class Finding(NamedTuple):
    path: str
    line: int
    col: int
    description: str


def _kind(node: ast.stmt) -> str | None:
    if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        return None
    if node.name.startswith("describe_"):
        return "describe"
    if node.name.startswith(SCENARIO_PREFIXES):
        return "scenario"
    if node.name.startswith(PRECONDITION_PREFIXES):
        return "precondition"
    if node.name.startswith(TEST_PREFIXES):
        return "test"
    return None


def _children(body: list[ast.stmt]) -> dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]]:
    result: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {
        "describe": [],
        "scenario": [],
        "precondition": [],
        "test": [],
    }
    for node in body:
        kind = _kind(node)
        if kind is not None:
            result[kind].append(node)
    return result


class DescribeAnalyzer:
    """Collect hierarchy violations from one parsed pytest-describe test module."""

    def __init__(
        self,
        path: str,
        condition_infixes: tuple[str, ...] = CONDITION_INFIXES,
    ) -> None:
        self.path = path
        self.condition_infixes = condition_infixes
        self.findings: list[Finding] = []

    def analyze(self, tree: ast.Module) -> list[Finding]:
        children = _children(tree.body)
        for kind in ("test", "scenario", "precondition"):
            for node in children[kind]:
                self._add(node, f"top-level {kind} block {node.name} must be inside a describe_* block")
        for node in children["describe"]:
            self._check_conditions(node)
            self._describe(node)
        return self.findings

    def _describe(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        children = _children(node.body)
        if not any(children.values()):
            self._add(node, f"{node.name} is empty (no nested pytest-describe blocks or tests)")
        if children["scenario"] and children["test"]:
            self._add(node, f"{node.name} contains both scenario_* and test_* blocks")
        self._visit_children(children, ("describe", "precondition", "scenario", "test"))

    def _precondition(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        children = _children(node.body)
        for child in children["describe"]:
            self._add(child, f"describe_* inside precondition {node.name} is not allowed")
        for child in children["precondition"]:
            self._add(child, f"precondition_* inside precondition {node.name} is not allowed")
        if not children["test"] and not children["scenario"]:
            self._add(node, f"{node.name} has no test_* or nested scenario_* blocks")
        self._visit_children(children, ("scenario", "test"))

    def _scenario(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        children = _children(node.body)
        for child in children["describe"]:
            self._add(child, f"describe_* inside scenario {node.name} is not allowed")
        if not children["test"] and not children["scenario"] and not children["precondition"]:
            self._add(node, f"{node.name} has no test_*, nested scenario_*, or precondition_* blocks")
        self._visit_children(children, ("precondition", "scenario", "test"))

    def _test(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for child in node.body:
            if _kind(child) is not None:
                self._add(
                    child,
                    f"test function {node.name} contains nested function {child.name}; tests must be leaves",
                )

    def _visit_children(
        self,
        children: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]],
        kinds: tuple[str, ...],
    ) -> None:
        for kind in kinds:
            for child in children[kind]:
                self._check_conditions(child)
                getattr(self, f"_{kind}")(child)

    def _check_conditions(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for infix in self.condition_infixes:
            if infix in node.name:
                self._add(node, f"{node.name} embeds '{infix}'; use a nested block instead")

    def _add(self, node: ast.AST, description: str) -> None:
        self.findings.append(Finding(self.path, node.lineno, node.col_offset + 1, description))


def _test_files_under(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix == ".py" and (path.name == "conftest.py" or path.name.startswith("test_")) else []
    return sorted([*path.rglob("test_*.py"), *path.rglob("conftest.py")])


def _analyze_tree(
    anchor: str,
    tree: ast.Module,
    condition_infixes: tuple[str, ...] = CONDITION_INFIXES,
) -> list[Finding]:
    return DescribeAnalyzer(anchor, condition_infixes).analyze(tree)


def _unreadable_finding(anchor: str, exc: UnreadableSource) -> Finding:
    return Finding(anchor, exc.line, 1, f"File cannot be read: {exc.reason}")


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enforce an opinionated pytest-describe test hierarchy.")
    parser.add_argument("paths", nargs="*", default=[Path(".")], type=Path)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="root that every reported path is named relative to (default: current directory)",
    )
    parser.add_argument(
        "--condition-infix",
        action="append",
        default=None,
        help="condition-name infix to reject; replaces the default infixes when supplied (repeatable)",
    )
    arguments = parser.parse_args()
    for path in arguments.paths:
        if not path.exists():
            parser.error(f"Path {path} does not exist")
    return arguments


def main() -> int:
    """Check named test paths against the pytest-describe policy."""
    arguments = _parse_arguments()
    scanned_by_path = [(path, _test_files_under(path)) for path in arguments.paths]
    empty_paths = [path for path, files in scanned_by_path if not files]
    if empty_paths:
        named = ", ".join(str(path) for path in empty_paths)
        print(
            f"pytest-describe scanned 0 Python test files under {named}; a clean result would be meaningless",
            file=sys.stderr,
        )
        return 1
    scanned = unique_paths(file for _, files in scanned_by_path for file in files)
    condition_infixes = tuple(arguments.condition_infix or CONDITION_INFIXES)

    findings = analyze_sources(
        scanned,
        arguments.project_root.resolve(),
        lambda anchor, tree: _analyze_tree(anchor, tree, condition_infixes),
        _unreadable_finding,
    )
    if not findings:
        print(f"pytest-describe hierarchy is valid across {len(scanned)} Python test files scope={len(scanned)}")
        return 0

    print("pytest-describe hierarchy violations:", file=sys.stderr)
    for finding in sorted(findings):
        print(f"  {finding.path}:{finding.line}:{finding.col} {finding.description}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
