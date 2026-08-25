"""Cap the TODO/FIXME/NOTE marker blocks a repository may hold.

A per-file cap alone is defeated by spreading markers across new files, so the repository total
is capped too. Both numbers come from the command line, because both are a property of the
repository under test and not of this package.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

from quality_gates.discovery import tracked_files
from quality_gates.markers import MAX_BLOCK_LINES, count_blocks_in, is_scannable

LADDER = (
    "Before adding a marker, walk the ladder: rename the symbol, put the fact in a test name "
    "or an assertion, move it to a document with a one-line pointer, then a plain comment. "
    "A NOTE is what you write when none of those can carry it."
)


class Budget(NamedTuple):
    """The two numbers a repository must supply: its total ceiling and its per-file exceptions."""

    ceiling: int
    per_file: Mapping[str, int]


def marker_counts(root: Path) -> Mapping[str, int]:
    """Marker blocks per scannable file that git tracks under `root`."""
    counts: dict[str, int] = {}
    for path in tracked_files(root):
        relative = path.relative_to(root).as_posix()
        if is_scannable(relative):
            counts[relative] = count_blocks_in(path)
    return counts


def failures(counts: Mapping[str, int], budget: Budget) -> list[str]:
    """Budget breaches, plus per-file entries that no longer earn their place.

    A per-file entry above the default cap is an exception the repository bought. It is spent
    once the file falls back under the default, and the gate says so. An entry at or below the
    default is a deliberate tightening, so it is never reported as spent.
    """
    problems = [
        f"{relative}: {count} markers, budget {allowed}"
        for relative, count in sorted(counts.items())
        if count > (allowed := budget.per_file.get(relative, MAX_BLOCK_LINES))
    ]

    total = sum(counts.values())
    if total > budget.ceiling:
        problems.append(
            f"repo total: {total} marker blocks, ceiling {budget.ceiling}. A per-file budget alone "
            "is defeated by spreading markers across new files, so the total is capped too."
        )

    spent = [
        f"{relative}: budget {allowed}, now {counts[relative]} — drop its --per-file entry"
        for relative, allowed in sorted(budget.per_file.items())
        if allowed > MAX_BLOCK_LINES and relative in counts and counts[relative] <= MAX_BLOCK_LINES
    ]
    missing = [f"{relative}: budgeted but no longer tracked" for relative in budget.per_file if relative not in counts]

    return problems + spent + missing


def _per_file_entry(text: str) -> tuple[str, int]:
    relative, separator, allowed = text.rpartition("=")
    if not separator or not relative or not allowed.isascii() or not allowed.isdigit():
        raise argparse.ArgumentTypeError(f"expected PATH=N, got {text!r}")
    return relative, int(allowed)


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cap the TODO/FIXME/NOTE marker blocks a repository may hold.")
    parser.add_argument("--ceiling", type=int, required=True, help="maximum marker blocks in the whole repository")
    parser.add_argument(
        "--per-file",
        type=_per_file_entry,
        action="append",
        default=[],
        metavar="PATH=N",
        help=f"budget for one repository-relative path, overriding the default of {MAX_BLOCK_LINES}; repeatable",
    )
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository to scan (default: current directory)")
    return parser.parse_args()


def main() -> int:
    """Scan the repository under --root and report every breach of the budget it was given."""
    arguments = _parse_arguments()
    root = arguments.root.resolve()
    try:
        counts = marker_counts(root)
    except RuntimeError as exc:
        print(f"❌ marker budget cannot read {root}: {exc}", file=sys.stderr)
        return 1

    if not counts:
        print(f"❌ marker budget scanned 0 files under {root}", file=sys.stderr)
        return 1

    problems = failures(counts, Budget(arguments.ceiling, dict(arguments.per_file)))
    if not problems:
        print(f"marker budget clean ({sum(counts.values())} markers) scope={len(counts)}")
        return 0

    print("❌ Marker budget:\n", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    print(
        f"\n{LADDER}\nA file at or above its budget may not gain another marker; any other file "
        f"may hold at most {MAX_BLOCK_LINES}. If the growth is warranted, raise the number this "
        "gate is invoked with, so the increase is reviewed rather than absorbed.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
