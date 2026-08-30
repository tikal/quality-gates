"""Shared staged-source operations for marker policy gates."""

from __future__ import annotations

import os
import subprocess
from collections import Counter
from pathlib import Path

from quality_gates.discovery import SKIPPED_DIRECTORIES, is_in_skipped_directory
from quality_gates.markers import is_scannable, marker_headers
from quality_gates.source import UnreadableSource


def git(root: Path, arguments: list[str]) -> bytes:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    result = subprocess.run(["git", "-C", str(root), *arguments], capture_output=True, env=env)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", "replace").strip() or "git command failed")
    return result.stdout


def headers(root: Path, revision: str, path: str) -> Counter[str]:
    try:
        reference = f":{path}" if revision == ":" else f"{revision}:{path}"
        content = git(root, ["show", reference]).decode("utf-8")
        return Counter(header.text.strip() for header in marker_headers(content, Path(path).suffix))
    except (RuntimeError, UnicodeDecodeError, UnreadableSource, ValueError) as exc:
        raise RuntimeError(f"{path}:{revision}: {exc}") from exc


def staged_paths(root: Path) -> list[tuple[str, str]]:
    entries = git(root, ["diff", "--cached", "--no-renames", "--name-status", "-z"]).split(b"\0")
    entries.pop()
    if len(entries) % 2:
        raise RuntimeError("git diff returned an incomplete staged file status")
    return [
        (status.decode("utf-8"), path.decode("utf-8")) for status, path in zip(entries[::2], entries[1::2], strict=True)
    ]


def tracked_paths(root: Path) -> list[str]:
    entries = git(root, ["ls-files", "-z"]).split(b"\0")
    entries.pop()
    return [path.decode("utf-8") for path in entries]


def eligible(path: str) -> bool:
    return is_scannable(path) and not is_in_skipped_directory(Path(path), SKIPPED_DIRECTORIES)
