"""Require every tracked Dockerfile to have a reviewed classification in a tracked ledger."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from quality_gates.discovery import tracked_files

CLASSIFICATIONS = frozenset(("pull", "build", "ignore"))


class Record(NamedTuple):
    """One valid Dockerfile enrollment record."""

    path: str
    classification: str


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Require every tracked Dockerfile to be classified in a JSON ledger.")
    parser.add_argument(
        "--ledger", type=Path, required=True, metavar="PATH", help="repository ledger of Dockerfile classifications"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository to scan (default: current directory)")
    return parser.parse_args()


def _dockerfiles(paths: list[Path], root: Path) -> tuple[set[str], list[str]]:
    dockerfiles: set[str] = set()
    unreadable: list[str] = []
    for path in paths:
        if path.name.startswith("Dockerfile"):
            relative = path.relative_to(root).as_posix()
            try:
                path.read_bytes()
            except OSError as exc:
                unreadable.append(f"{relative}: cannot read: {exc}")
            else:
                dockerfiles.add(relative)
    return dockerfiles, unreadable


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant {value}")


def _reject_duplicate_key(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _read_ledger(path: Path) -> object:
    try:
        raw = path.read_bytes()
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_key,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"cannot parse ledger: {type(exc).__name__}: {exc}") from exc


def _records(ledger: object) -> tuple[list[Record], list[str]]:
    if not isinstance(ledger, dict) or set(ledger) != {"version", "dockerfiles"}:
        return [], ["ledger must be an object with exactly version and dockerfiles"]
    if type(ledger["version"]) is not int or ledger["version"] != 1:
        return [], ["ledger version must be integer 1"]
    values = ledger["dockerfiles"]
    if not isinstance(values, list):
        return [], ["ledger dockerfiles must be a list"]

    records: list[Record] = []
    invalid: list[str] = []
    for number, value in enumerate(values, 1):
        record, reason = _record(value)
        if reason:
            invalid.append(f"record {number}: {reason}")
        elif record is not None:
            records.append(record)
    duplicates = {record.path for record in records if sum(item.path == record.path for item in records) > 1}
    invalid.extend(f"duplicate record for {path}" for path in sorted(duplicates))
    return records, invalid


def _record(value: object) -> tuple[Record | None, str | None]:
    if not isinstance(value, dict):
        return None, "must be an object"
    classification = value.get("classification")
    expected = {"path", "classification"}
    if classification == "ignore":
        expected.add("rationale")
    if set(value) != expected:
        return None, f"must contain exactly {', '.join(sorted(expected))}"
    path = value.get("path")
    if not isinstance(path, str) or not path.strip() or Path(path).is_absolute() or "\\" in path:
        return None, "path must be a nonblank repository-relative POSIX path"
    if not isinstance(classification, str) or classification not in CLASSIFICATIONS:
        return None, "classification must be pull, build, or ignore"
    if classification == "ignore" and (not isinstance(value["rationale"], str) or not value["rationale"].strip()):
        return None, "ignore records require a nonblank rationale"
    return Record(path, classification), None


def _report(title: str, findings: list[str]) -> None:
    if findings:
        print(f"Dockerfile enrollment {title}:", file=sys.stderr)
        for finding in findings:
            print(f"  - {finding}", file=sys.stderr)


def _main(root: Path, ledger_path: Path, list_files: Callable[[Path], list[Path]] = tracked_files) -> int:
    try:
        paths = list_files(root)
    except RuntimeError as exc:
        print(f"Dockerfile enrollment cannot list {root}: {exc}", file=sys.stderr)
        return 1

    tracked = {path.resolve() for path in paths}
    if ledger_path.resolve() not in tracked:
        print(f"Dockerfile enrollment invalid: ledger is not tracked: {ledger_path}", file=sys.stderr)
        return 1

    dockerfiles, unreadable = _dockerfiles(paths, root)
    if not dockerfiles:
        print(f"Dockerfile enrollment scanned 0 Dockerfiles under {root}", file=sys.stderr)
        return 1
    if unreadable:
        _report("invalid", unreadable)
        return 1

    try:
        ledger = _read_ledger(ledger_path)
    except ValueError as exc:
        _report("invalid", [str(exc)])
        return 1
    records, invalid = _records(ledger)
    classified = {record.path for record in records}
    _report("invalid", invalid)
    _report("unclassified", sorted(dockerfiles - classified))
    _report("stale", sorted(classified - dockerfiles))
    if invalid or dockerfiles != classified:
        return 1
    print(f"Dockerfile enrollment clean scope={len(dockerfiles)}")
    return 0


def main() -> int:
    """Check tracked Dockerfiles against the required enrollment ledger."""
    arguments = _parse_arguments()
    root = arguments.root.resolve()
    ledger = arguments.ledger if arguments.ledger.is_absolute() else root / arguments.ledger
    return _main(root, ledger)


if __name__ == "__main__":
    sys.exit(main())
