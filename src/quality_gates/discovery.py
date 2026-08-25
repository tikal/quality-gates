"""Which files a gate reads: git's index for a whole repository, the filesystem for a named path.

Shared so the marker budget does not depend on the comment gate, and the dict-parameter gate does
not depend on either. Every gate must see the same tree for the same arguments.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Collection, Sequence
from pathlib import Path

SKIPPED_DIRECTORIES = frozenset({"venv", ".venv", "node_modules", "__pycache__", "_generated"})

DEFAULT_SOURCE_DIRECTORIES = ("src", "tests")

PROJECT_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg")


def tracked_files(root: Path) -> list[Path]:
    """Every file git tracks under `root`, discovered from the index rather than the filesystem.

    The index is correct by construction: no hand-maintained skip list can drift, and a nested
    checkout another tool left behind is invisible. Staged-but-uncommitted files are included, so
    a file added in this commit is gated at once.

    GIT_DIR and GIT_INDEX_FILE OVERRIDE `-C`, and pre-commit exports both while a hook runs, so
    they are stripped. Without that, a scan of a fixture tree reads the REAL repository index, the
    gate passes or fails on the wrong tree, and a fixture that is deliberately not a repository
    silently finds one. An index entry whose file is gone is dropped, because a caller opens what
    this yields and a vanished path must read as a stale ledger entry rather than a crash.
    """
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    try:
        listing = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--deduplicate"],
            capture_output=True,
            text=True,
            env=env,
        )
    except OSError as exc:
        raise RuntimeError(f"cannot list tracked files under {root}: git is unavailable ({exc})") from exc
    if listing.returncode != 0:
        detail = listing.stderr.strip() or f"git ls-files exited {listing.returncode}"
        raise RuntimeError(f"cannot list tracked files under {root}: {detail}")
    return [path for rel in listing.stdout.split("\0") if rel and (path := root / rel).exists()]


def is_project_root(path: Path) -> bool:
    """True when a directory declares a Python project, so its source directories are meaningful.

    Narrowing a scan to `src` and `tests` is only ever right for a project root. Applied to any
    directory that happens to hold a child of that name, it drops every other file in silence and
    the gate reports a clean tree it never read.
    """
    return any((path / marker).is_file() for marker in PROJECT_MARKERS)


def python_files_under(
    path: Path,
    source_directories: Sequence[str] = DEFAULT_SOURCE_DIRECTORIES,
    skipped_directories: Collection[str] = SKIPPED_DIRECTORIES,
) -> list[Path]:
    """Python files to gate. A project root means its source directories; anything else is itself."""
    if path.is_file():
        return [path] if path.suffix == ".py" else []

    found: list[Path] = []
    if is_project_root(path):
        found.extend(sorted(path.glob("*.py")))
        for name in source_directories:
            found.extend(sorted((path / name).rglob("*.py")))
    else:
        found.extend(sorted(path.rglob("*.py")))

    skipped = set(skipped_directories)
    return [candidate for candidate in found if not skipped.intersection(candidate.parts)]
