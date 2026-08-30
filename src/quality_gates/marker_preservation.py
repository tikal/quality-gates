"""Fail when the staged index removes a tracked source marker header."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from quality_gates.marker_changes import eligible, headers, staged_paths, tracked_paths


def main() -> int:
    parser = argparse.ArgumentParser(description="Prevent staged source changes from deleting marker headers.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    arguments = parser.parse_args()
    root = arguments.root.resolve()
    try:
        changed = staged_paths(root)
    except (RuntimeError, UnicodeDecodeError) as exc:
        print(f"marker preservation failed: {exc}", file=sys.stderr)
        return 1
    changed = [(status, path) for status, path in changed if eligible(path)]
    if not changed:
        try:
            changed = [("M", path) for path in tracked_paths(root) if eligible(path)]
        except (RuntimeError, UnicodeDecodeError) as exc:
            print(f"marker preservation failed: {exc}", file=sys.stderr)
            return 1
        if not changed:
            print(
                "marker preservation scanned 0 tracked source files; a clean result would be meaningless",
                file=sys.stderr,
            )
            return 1
    findings: list[str] = []
    for status, path in changed:
        try:
            before = Counter() if status == "A" else headers(root, "HEAD", path)
        except RuntimeError as exc:
            findings.append(str(exc))
            continue
        try:
            after = Counter() if status == "D" else headers(root, ":", path)
        except RuntimeError as exc:
            findings.append(str(exc))
            continue
        for header, count in (before - after).items():
            findings.extend(f"{path}: removed marker {header}" for _ in range(count))
    if findings:
        print("marker preservation failed:", file=sys.stderr)
        print(*(f"  {finding}" for finding in findings), sep="\n", file=sys.stderr)
        return 1
    print(f"marker preservation clean scope={len(changed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
