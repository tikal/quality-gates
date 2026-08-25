"""Fail on a plain inline comment, an over-long marker block, or a docstring on a private function.

A baseline grandfathers what already exists, so the rule can land on a repository that does not
yet obey it. The baseline is keyed on the offending text, so moving a comment keeps it
grandfathered and editing it does not.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import re
import sys
import tokenize
from collections import Counter
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from quality_gates.discovery import DEFAULT_SOURCE_DIRECTORIES, SKIPPED_DIRECTORIES, python_files_under
from quality_gates.markers import (
    MAX_BLOCK_LINES,
    CommentLine,
    is_badge,
    is_lint_pragma,
    is_marker_header,
    marker_blocks,
)

INLINE = "inline"
LONG_BLOCK = "long-block"
MODULE_DOCSTRING = "module-docstring"
PRIVATE_DOCSTRING = "private-docstring"

REMEDIATION = "move the fact out of the code and leave a one-line pointer"

MESSAGES = {
    INLINE: "Inline comment detected",
    LONG_BLOCK: f"Marker block over {MAX_BLOCK_LINES} lines — {REMEDIATION}",
    MODULE_DOCSTRING: f"Module docstring over {MAX_BLOCK_LINES} lines — {REMEDIATION}",
    PRIVATE_DOCSTRING: "Private function/method has a docstring",
}


class Violation(NamedTuple):
    path: str
    line: int
    kind: str
    subject: str
    context: str = ""

    @property
    def fingerprint(self) -> str:
        """Identity for the baseline: the file, the kind and the offending text — never the line.

        Moving the text keeps it grandfathered. Editing it does not, so anything you touch is
        something you must bring up to the rule. `subject` is therefore the text the rule is
        about in every case — the comment, or the docstring itself. Keying a docstring on its
        filename or its function name instead would invert this: a rewritten docstring would
        stay grandfathered while a pure rename would not.
        """
        normalised = re.sub(r"\s+", " ", self.subject.lstrip("#").strip()).lower()
        digest = hashlib.sha1(normalised.encode()).hexdigest()[:12]
        return f"{self.path}\t{self.kind}\t{digest}"

    def render(self) -> str:
        """One reportable line, with the offending text truncated to stay readable."""
        subject = re.sub(r"\s+", " ", self.subject).strip()
        if len(subject) > 100:
            subject = subject[:99] + "…"
        named = f"{self.context} — " if self.context else ""
        return f"{self.path}:{self.line} - {MESSAGES[self.kind]}: {named}{subject}"


def _comment_tokens(filepath: Path) -> list[tokenize.TokenInfo]:
    with open(filepath, "rb") as handle:
        return [token for token in tokenize.tokenize(handle.readline) if token.type == tokenize.COMMENT]


def _own_line(token: tokenize.TokenInfo) -> bool:
    return token.line[: token.start[1]].strip() == ""


def check_inline_comments(filepath: Path, anchor: str) -> list[Violation]:
    """Check for inline # comments in a Python file, reporting them against `anchor`.

    Allows shebangs, encoding declarations, lint pragmas and marker blocks. A marker block is
    a TODO/FIXME/NOTE/XXX/HACK comment plus the own-line comments under it at the same column,
    so the body of a marker is part of that marker rather than a separate comment.
    """
    try:
        tokens = _comment_tokens(filepath)
    except tokenize.TokenError:
        return []

    comments = [CommentLine(t.start[0], t.start[1], t.string.strip()) for t in tokens if _own_line(t)]
    blocks = marker_blocks(comments)
    inside_block = {comment.row for block in blocks for comment in block}
    violations = []

    for token in tokens:
        comment = token.string.strip()
        line_num = token.start[0]

        if line_num <= 2 and (comment.startswith("#!") or "coding" in comment or "encoding" in comment):
            continue
        if is_lint_pragma(comment) or is_badge(comment) or is_marker_header(comment):
            continue
        if line_num in inside_block:
            continue

        violations.append(Violation(anchor, line_num, INLINE, comment))

    for block in blocks:
        if len(block) > MAX_BLOCK_LINES:
            header = block[0]
            violations.append(Violation(anchor, header.row, LONG_BLOCK, f"{len(block)} lines — {header.text}"))

    return violations


def check_inappropriate_docstrings(filepath: Path, anchor: str) -> list[Violation]:
    """Check for docstrings in inappropriate places, reporting them against `anchor`.

    Rules:
    - A module docstring may state why the file exists, up to MAX_BLOCK_LINES lines. Past that
      the fact belongs outside the code with a pointer — the same rule, and the same number, as
      a marker block.
    - NO docstrings on private methods/functions (starting with _)
    - ALLOW docstrings on classes, public methods, and public standalone functions
    """
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8"))
    except SyntaxError:
        return []

    violations = []

    module_docstring = ast.get_docstring(tree) or ""
    if len(module_docstring.splitlines()) > MAX_BLOCK_LINES:
        violations.append(Violation(anchor, 1, MODULE_DOCSTRING, module_docstring))

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        docstring = ast.get_docstring(node)
        if not docstring:
            continue
        is_dunder = node.name.startswith("__") and node.name.endswith("__")
        if node.name.startswith("_") and not is_dunder:
            violations.append(Violation(anchor, node.lineno, PRIVATE_DOCSTRING, docstring, node.name))

    return violations


@dataclass(frozen=True)
class Scan:
    """The tree a run covers, and the root every reported path is named relative to."""

    project_root: Path
    source_directories: Sequence[str] = DEFAULT_SOURCE_DIRECTORIES
    skipped_directories: Collection[str] = SKIPPED_DIRECTORIES

    def anchored(self, path: Path) -> str:
        """Identity relative to the project root, so a baseline entry means the same from any cwd."""
        resolved = path.resolve()
        try:
            return resolved.relative_to(self.project_root).as_posix()
        except ValueError:
            return resolved.as_posix()

    def still_scannable(self, relative: str) -> bool:
        """True when a path a baseline entry names is still a file this gate would read.

        An entry for a deleted or newly excluded file can never be scanned again, so keeping it
        would strand it in the baseline forever — `--update-baseline` could not reach it, and the
        stale check would not either, because a file that is never scanned is never missed.
        """
        candidate = self.project_root / relative
        return candidate.exists() and not set(self.skipped_directories).intersection(candidate.parts)

    def collect(self, paths: Sequence[Path]) -> tuple[list[Violation], set[str]]:
        """Every violation under `paths`, and the anchored name of every file that was read."""
        py_files: list[Path] = []
        for path in paths:
            py_files.extend(python_files_under(path, self.source_directories, self.skipped_directories))

        violations: list[Violation] = []
        for py_file in py_files:
            anchor = self.anchored(py_file)
            violations.extend(check_inline_comments(py_file, anchor))
            violations.extend(check_inappropriate_docstrings(py_file, anchor))
        return violations, {self.anchored(f) for f in py_files}


def read_baseline(path: Path) -> Counter:
    """The grandfathered fingerprints and their multiplicity, or an empty tally when absent."""
    if not path.exists():
        return Counter()
    entries: Counter = Counter()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        key, count = line.rsplit("\t", 1)
        entries[key] += int(count)
    return entries


def write_baseline(path: Path, violations: list[Violation], scanned: set[str], scan: Scan) -> None:
    """Rewrite the entries for the scanned files, keeping every entry outside that scope.

    `--update-baseline src` must not delete the `tests/` entries it never looked at, which a
    plain overwrite would do — a pre-commit entry scans one subtree at a time. An entry whose
    file is gone or now excluded is dropped, since no later run can ever revisit it.
    """
    kept = Counter(
        {
            key: n
            for key, n in read_baseline(path).items()
            if key.split("\t", 1)[0] not in scanned and scan.still_scannable(key.split("\t", 1)[0])
        }
    )
    kept.update(v.fingerprint for v in violations)
    body = "\n".join(f"{key}\t{count}" for key, count in sorted(kept.items()))
    path.write_text(body + "\n" if body else "", encoding="utf-8")


def report(new: list[Violation], stale: Counter, checked: int, baselined: int) -> int:
    """Print the outcome and return the exit code the gate must exit with."""
    if not new and not stale:
        grandfathered = f" ({baselined} grandfathered)" if baselined else ""
        print(
            f"✅ All {checked} Python files pass comment validation{grandfathered} scope={checked}",
            file=sys.stderr,
        )
        return 0

    if new:
        print("❌ Comment/docstring validation failed:\n", file=sys.stderr)
        for violation in sorted(new):
            print(f"  {violation.render()}", file=sys.stderr)
        print(f"\nNew violations: {len(new)}", file=sys.stderr)

    if stale:
        print(f"\n❌ {sum(stale.values())} baseline entries no longer match any violation.", file=sys.stderr)
        for key, count in sorted(stale.items()):
            print(f"  stale x{count}: {key}", file=sys.stderr)
        print("\nRun with --update-baseline to shrink the baseline.", file=sys.stderr)

    return 1


def _names(text: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in text.split(",") if part.strip())


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check for inline comments and misplaced docstrings.")
    parser.add_argument("paths", nargs="*", default=[Path(".")], type=Path)
    parser.add_argument("--baseline", type=Path, required=True, help="baseline file of grandfathered violations")
    parser.add_argument("--no-baseline", action="store_true", help="report every violation")
    parser.add_argument("--update-baseline", action="store_true", help="rewrite the baseline from current state")
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="root that every reported path is named relative to (default: current directory)",
    )
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
    arguments = parser.parse_args()
    for path in arguments.paths:
        if not path.exists():
            parser.error(f"Path {path} does not exist")
    return arguments


def main() -> int:
    """Check every path given, honouring the baseline unless told otherwise."""
    arguments = _parse_arguments()
    scan = Scan(arguments.project_root.resolve(), arguments.src_dirs, frozenset(arguments.skip_dirs))
    violations, scanned = scan.collect(arguments.paths)

    if not scanned:
        print(
            f"❌ comment validation examined 0 Python files under "
            f"{', '.join(str(p) for p in arguments.paths)} — a clean result would be meaningless",
            file=sys.stderr,
        )
        return 1

    if arguments.update_baseline:
        write_baseline(arguments.baseline, violations, scanned, scan)
        print(f"Baseline written: {len(violations)} entries → {arguments.baseline}", file=sys.stderr)
        return 0

    if arguments.no_baseline:
        return report(violations, Counter(), len(scanned), 0)

    remaining = read_baseline(arguments.baseline)
    new = []
    for violation in violations:
        if remaining[violation.fingerprint] > 0:
            remaining[violation.fingerprint] -= 1
        else:
            new.append(violation)

    stale = Counter({key: n for key, n in remaining.items() if n > 0 and key.split("\t", 1)[0] in scanned})
    return report(new, stale, len(scanned), len(violations) - len(new))


if __name__ == "__main__":
    sys.exit(main())
