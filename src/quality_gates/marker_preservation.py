"""Fail when the staged index removes a tracked source marker header."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

from quality_gates.discovery import SKIPPED_DIRECTORIES, is_in_skipped_directory
from quality_gates.markers import is_scannable, marker_headers
from quality_gates.source import UnreadableSource


def _git(root: Path, arguments: list[str]) -> bytes:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    result = subprocess.run(["git", "-C", str(root), *arguments], capture_output=True, env=env)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", "replace").strip() or "git command failed")
    return result.stdout


def _headers(root: Path, revision: str, path: str) -> Counter[str]:
    try:
        reference = f":{path}" if revision == ":" else f"{revision}:{path}"
        content = _git(root, ["show", reference]).decode("utf-8")
        return Counter(header.text.strip() for header in marker_headers(content, Path(path).suffix))
    except (RuntimeError, UnicodeDecodeError, UnreadableSource, ValueError) as exc:
        raise RuntimeError(f"{path}:{revision}: {exc}") from exc


def _staged_paths(root: Path) -> list[tuple[str, str]]:
    entries = _git(root, ["diff", "--cached", "--no-renames", "--name-status", "-z"]).split(b"\0")
    entries.pop()
    if len(entries) % 2:
        raise RuntimeError("git diff returned an incomplete staged file status")
    return [
        (status.decode("utf-8"), path.decode("utf-8")) for status, path in zip(entries[::2], entries[1::2], strict=True)
    ]


def _tracked_paths(root: Path) -> list[str]:
    entries = _git(root, ["ls-files", "-z"]).split(b"\0")
    entries.pop()
    return [path.decode("utf-8") for path in entries]


def _eligible(path: str) -> bool:
    return is_scannable(path) and not is_in_skipped_directory(Path(path), SKIPPED_DIRECTORIES)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prevent staged source changes from deleting marker headers.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    arguments = parser.parse_args()
    root = arguments.root.resolve()
    try:
        changed = _staged_paths(root)
    except (RuntimeError, UnicodeDecodeError) as exc:
        print(f"marker preservation failed: {exc}", file=sys.stderr)
        return 1
    changed = [(status, path) for status, path in changed if _eligible(path)]
    if not changed:
        try:
            changed = [("M", path) for path in _tracked_paths(root) if _eligible(path)]
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
            before = Counter() if status == "A" else _headers(root, "HEAD", path)
        except RuntimeError as exc:
            findings.append(str(exc))
            continue
        try:
            after = Counter() if status == "D" else _headers(root, ":", path)
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
