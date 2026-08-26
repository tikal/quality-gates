"""Run Vulture over an explicit Python scope without allowing an empty clean result.

Vulture accepts a missing or empty directory and can then return success. This wrapper makes the
scope visible to a pre-commit consumer: every declared path must exist and at least one readable
Python file must remain after exclusions. It ignores a consumer's Vulture project configuration.
"""

from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from tempfile import TemporaryDirectory

from quality_gates.source import UnreadableSource, parse_source, read_source


def _confidence(value: str) -> int:
    confidence = int(value)
    if not 0 <= confidence <= 100:
        raise argparse.ArgumentTypeError("must be from 0 to 100")
    return confidence


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Find unused Python code with Vulture.")
    parser.add_argument(
        "--path", action="append", required=True, type=Path, metavar="PATH", help="source path to scan; repeatable"
    )
    parser.add_argument("--ignore-names", help="comma-separated Vulture name patterns to ignore")
    parser.add_argument("--exclude", help="comma-separated Vulture absolute-path patterns to exclude")
    parser.add_argument(
        "--min-confidence",
        default=80,
        type=_confidence,
        help="minimum Vulture confidence to report (default: 80)",
    )
    arguments = parser.parse_args()
    for path in arguments.path:
        if not path.exists():
            parser.error(f"path {path} does not exist")
    return arguments


def _excluded(path: Path, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatch(path.resolve().as_posix(), pattern) for pattern in patterns)


def _exclude_patterns(exclude: str | None) -> tuple[str, ...]:
    if exclude is None:
        return ()
    return tuple(
        pattern if any(character in pattern for character in "*?[") else f"*{pattern}*"
        for pattern in exclude.split(",")
    )


def _python_files(paths: Sequence[Path], exclude: str | None) -> list[Path]:
    found = set()
    for path in paths:
        candidates = [path] if path.is_file() else path.rglob("*.py")
        found.update(candidate.resolve() for candidate in candidates if candidate.suffix == ".py")
    return sorted(path for path in found if not _excluded(path, _exclude_patterns(exclude)))


def _readable(paths: Sequence[Path]) -> bool:
    for path in paths:
        try:
            parse_source(read_source(path))
        except UnreadableSource as exc:
            print(f"dead-code cannot read {path}:{exc.line}: {exc.reason}", file=sys.stderr)
            return False
    return True


def _vulture_command(arguments: argparse.Namespace, config: Path) -> list[str]:
    command = [
        "vulture",
        "--config",
        str(config),
        *(str(path) for path in arguments.path),
        "--min-confidence",
        str(arguments.min_confidence),
        "--sort-by-size",
    ]
    if arguments.ignore_names:
        command.extend(("--ignore-names", arguments.ignore_names))
    if arguments.exclude is not None:
        command.extend(("--exclude", ",".join(_exclude_patterns(arguments.exclude))))
    return command


def main() -> int:
    """Scan each declared path and preserve Vulture findings as a gate failure."""
    arguments = _parse_arguments()
    files = _python_files(arguments.path, arguments.exclude)
    if not files:
        print("dead-code scanned 0 Python files; a clean result would be meaningless", file=sys.stderr)
        return 1
    if not _readable(files):
        return 1
    try:
        with TemporaryDirectory(prefix="quality-gates-vulture-") as directory:
            config = Path(directory) / "vulture.toml"
            config.touch()
            result = subprocess.run(_vulture_command(arguments, config), check=False).returncode
    except OSError as exc:
        print(f"dead-code cannot run vulture: {exc}", file=sys.stderr)
        return 1
    if result == 0:
        print(f"dead code clean scope={len(files)}")
        return 0
    return 1 if result == 3 else result


if __name__ == "__main__":
    sys.exit(main())
