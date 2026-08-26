"""Store violation debt without tying a baseline format to one quality gate.

The baseline format is a public compatibility surface. Its fingerprint identifies a path, a gate
kind, and normalized offending text, never a line number. This lets a violation move while still
making a new or changed violation fail.
"""

from __future__ import annotations

import argparse
import hashlib
import re
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

Violation = TypeVar("Violation")


class BaselineError(ValueError):
    """A baseline file that cannot be read as one entry per line."""


@dataclass(frozen=True)
class Scope:
    """The root and excluded directories that define live baseline entries."""

    project_root: Path
    skipped_directories: Collection[str]


def fingerprint(path: str, kind: str, subject: str) -> str:
    """Return the stable identity of a violation without its source line number."""
    normalised = re.sub(r"\s+", " ", subject.lstrip("#").strip()).lower()
    digest = hashlib.sha1(normalised.encode()).hexdigest()[:12]
    return f"{path}\t{kind}\t{digest}"


def add_arguments(parser: argparse.ArgumentParser, *, required: bool) -> None:
    """Add baseline selection and maintenance options to a gate parser."""
    grandfathering = parser.add_mutually_exclusive_group(required=required)
    grandfathering.add_argument("--baseline", type=Path, help="baseline file of grandfathered violations")
    grandfathering.add_argument(
        "--no-baseline", action="store_true", help="report every violation, grandfathering none"
    )
    baseline_actions = parser.add_mutually_exclusive_group()
    baseline_actions.add_argument(
        "--update-baseline", action="store_true", help="rewrite the baseline from current state"
    )
    baseline_actions.add_argument(
        "--shrink-baseline",
        action="store_true",
        help="remove fixed violations from the baseline without adding current violations",
    )


def validate_arguments(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> None:
    """Reject baseline writes when no named baseline can receive them."""
    if (arguments.update_baseline or arguments.shrink_baseline) and arguments.baseline is None:
        parser.error("a baseline write needs --baseline, so it cannot be used with --no-baseline")


def read(path: Path, kinds: Collection[str]) -> Counter[str]:
    """Return grandfathered fingerprints, preserving their multiplicity."""
    if not path.exists():
        return Counter()
    entries: Counter[str] = Counter()
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip() or line.startswith("#"):
            continue
        if line.count("\t") != 3:
            raise BaselineError(f"baseline {path}:{lineno}: expected 'path<TAB>kind<TAB>digest<TAB>count'")
        entry_path, kind, digest, count = line.split("\t")
        if (
            not entry_path
            or "\0" in entry_path
            or any(part in {".", ".."} for part in Path(entry_path).parts)
            or kind not in kinds
            or re.fullmatch(r"[0-9a-f]{12}", digest) is None
            or re.fullmatch(r"[1-9][0-9]*", count) is None
        ):
            raise BaselineError(f"baseline {path}:{lineno}: expected 'path<TAB>kind<TAB>digest<TAB>count'")
        entries[f"{entry_path}\t{kind}\t{digest}"] += int(count)
    return entries


def classify(
    violations: Sequence[Violation], baseline: Counter[str], identity: Callable[[Violation], str]
) -> tuple[list[Violation], Counter[str]]:
    """Separate ungrandfathered violations from the baseline entries left unmatched."""
    remaining = baseline.copy()
    new = []
    for violation in violations:
        if remaining[identity(violation)] > 0:
            remaining[identity(violation)] -= 1
        else:
            new.append(violation)
    return new, remaining


def still_scannable(scope: Scope, relative: str) -> bool:
    """Return whether a baseline path is still eligible for a future scan."""
    candidate = scope.project_root / relative
    return candidate.exists() and not set(scope.skipped_directories).intersection(candidate.parts)


def write(
    path: Path,
    fingerprints: Iterable[str],
    scanned: set[str],
    scope: Scope,
    kinds: Collection[str],
) -> None:
    """Rewrite entries for scanned files while retaining live entries outside that scope."""
    kept = Counter(
        {
            key: count
            for key, count in read(path, kinds).items()
            if key.split("\t", 1)[0] not in scanned and still_scannable(scope, key.split("\t", 1)[0])
        }
    )
    kept.update(fingerprints)
    _write(path, kept)


def shrink(
    path: Path,
    baseline: Counter[str],
    remaining: Counter[str],
    scanned: set[str],
    scope: Scope,
) -> None:
    """Delete repaired entries while retaining live entries outside the scanned scope."""
    kept: Counter[str] = Counter()
    for key, count in baseline.items():
        entry_path = key.split("\t", 1)[0]
        if entry_path in scanned and count > remaining[key]:
            kept[key] = count - remaining[key]
        elif entry_path not in scanned and still_scannable(scope, entry_path):
            kept[key] = count
    _write(path, kept)


def stale(remaining: Counter[str], scanned: set[str]) -> Counter[str]:
    """Return unmatched entries in files that this run scanned."""
    return Counter({key: count for key, count in remaining.items() if count > 0 and key.split("\t", 1)[0] in scanned})


def _write(path: Path, entries: Counter[str]) -> None:
    body = "\n".join(f"{key}\t{count}" for key, count in sorted(entries.items()))
    path.write_text(body + "\n" if body else "", encoding="utf-8")
