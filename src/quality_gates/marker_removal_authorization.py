"""Authorize exact staged marker removals with commit-message trailers."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path

from quality_gates.marker_preservation import _eligible, _headers, _staged_paths, _tracked_paths

_TRAILER = "Marker-Removal: "


def _changed_paths(root: Path) -> list[tuple[str, str]]:
    changed = [(status, path) for status, path in _staged_paths(root) if _eligible(path)]
    return changed or [("M", path) for path in _tracked_paths(root) if _eligible(path)]


def _removals(root: Path, changed: list[tuple[str, str]]) -> Counter[tuple[str, str]]:
    removals: Counter[tuple[str, str]] = Counter()
    for status, path in changed:
        before = Counter() if status == "A" else _headers(root, "HEAD", path)
        after = Counter() if status == "D" else _headers(root, ":", path)
        removals.update({(path, header): count for header, count in (before - after).items()})
    return removals


def _trailers(message: Path) -> Counter[tuple[str, str]]:
    try:
        lines = message.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"cannot read commit message {message}: {exc}") from exc
    while lines and not lines[-1].strip():
        lines.pop()
    trailer_start = next((index + 1 for index in range(len(lines) - 1, -1, -1) if not lines[index].strip()), 0)
    authorizations: Counter[tuple[str, str]] = Counter()
    for line_number, line in enumerate(lines[trailer_start:], start=trailer_start + 1):
        if not line.startswith(_TRAILER):
            continue
        try:
            value = re.sub(r'"\s+\|', '"|', line.removeprefix(_TRAILER))
            fields = next(csv.reader([value], delimiter="|", skipinitialspace=True, strict=True))
        except csv.Error as exc:
            raise RuntimeError(f"{message}:{line_number}: invalid Marker-Removal quoting: {exc}") from exc
        fields = [field.strip() for field in fields]
        if len(fields) != 3 or not all(field.strip() for field in fields):
            raise RuntimeError(f"{message}:{line_number}: Marker-Removal requires PATH | HEADER | RATIONALE")
        path, header, _ = fields
        authorizations[(path, header)] += 1
    return authorizations


def _format(entries: Counter[tuple[str, str]], label: str) -> list[str]:
    return [f"{label}: {path}: {header}" for (path, header), count in sorted(entries.items()) for _ in range(count)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Require commit-message authorization for staged marker removals.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("commit_message", type=Path)
    arguments = parser.parse_args()
    root = arguments.root.resolve()
    try:
        changed = _changed_paths(root)
        if not changed:
            raise RuntimeError("scanned 0 tracked source files; a clean result would be meaningless")
        removals = _removals(root, changed)
        authorizations = _trailers(arguments.commit_message)
    except RuntimeError as exc:
        print(f"marker removal authorization failed: {exc}", file=sys.stderr)
        return 1
    findings = _format(removals - authorizations, "missing Marker-Removal")
    findings.extend(_format(authorizations - removals, "stale Marker-Removal"))
    if findings:
        print("marker removal authorization failed:", file=sys.stderr)
        print(*(f"  {finding}" for finding in findings), sep="\n", file=sys.stderr)
        return 1
    print(f"marker removal authorization clean scope={len(changed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
