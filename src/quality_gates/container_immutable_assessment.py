"""Validate consumer-produced immutable container assessment evidence."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from quality_gates.container_image_enrollment import _inventory, _ledger
from quality_gates.discovery import tracked_files
from quality_gates.security_policy import read_json, relative_path, text

SHA256 = re.compile(r"[0-9a-f]{64}\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
SEVERITIES = frozenset(("LOW", "MEDIUM", "HIGH", "CRITICAL"))


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate fresh immutable container assessment evidence.")
    parser.add_argument("--enrollment", required=True, type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--exceptions", required=True, type=Path)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--max-age-hours", required=True, type=int)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _path(value: Path, root: Path) -> Path:
    return value if value.is_absolute() else root / value


def _timestamp(value: object, label: str) -> datetime:
    timestamp = text(value, label)
    try:
        return datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise ValueError(f"{label} must be a UTC timestamp in YYYY-MM-DDTHH:MM:SSZ form") from exc


def _sha256(path: Path, label: str) -> str:
    try:
        with path.open("rb") as evidence:
            return hashlib.file_digest(evidence, "sha256").hexdigest()
    except OSError as exc:
        raise ValueError(f"cannot read {label} {path}: {exc}") from exc


def _digest(value: object, label: str) -> str:
    digest = text(value, label)
    if not DIGEST.fullmatch(digest):
        raise ValueError(f"{label} must be an immutable sha256 digest")
    return digest


def _raw_evidence(value: object, root: Path, tracked: set[Path], number: int) -> None:
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        raise ValueError(f"scan {number} raw_evidence must contain exactly path and sha256")
    path = root / relative_path(value["path"], f"scan {number} raw evidence path")
    resolved = path.resolve()
    if root not in resolved.parents or resolved in tracked:
        raise ValueError(f"scan {number} raw evidence must be an untracked file under the repository root")
    expected = text(value["sha256"], f"scan {number} raw evidence sha256")
    if not SHA256.fullmatch(expected):
        raise ValueError(f"scan {number} raw evidence sha256 must be lowercase SHA-256")
    if _sha256(resolved, f"scan {number} raw evidence") != expected:
        raise ValueError(f"scan {number} raw evidence SHA-256 does not match")


def _scans(
    value: object, identifiers: set[str], root: Path, tracked: set[Path], freshness: tuple[datetime, timedelta]
) -> dict[str, str]:
    if not isinstance(value, list):
        raise ValueError("report scans must be a list")
    scans: dict[str, str] = {}
    for number, item in enumerate(value, 1):
        if not isinstance(item, dict) or set(item) != {"image_id", "artifact_digest", "scanned_at", "raw_evidence"}:
            raise ValueError(f"scan {number} is invalid")
        identifier = text(item["image_id"], f"scan {number} image_id")
        if identifier not in identifiers:
            raise ValueError(f"scan {number} references unenrolled image ID {identifier}")
        if identifier in scans:
            raise ValueError(f"duplicate scan for enrolled image ID {identifier}")
        scanned_at = _timestamp(item["scanned_at"], f"scan {number} scanned_at")
        age = freshness[0] - scanned_at
        if age < timedelta() or age > freshness[1]:
            raise ValueError(f"scan {number} is not fresh as of the requested timestamp")
        _raw_evidence(item["raw_evidence"], root, tracked, number)
        scans[identifier] = _digest(item["artifact_digest"], f"scan {number} artifact_digest")
    if set(scans) != identifiers:
        raise ValueError("report must contain one fresh scan for every enrolled image ID")
    return scans


def _exceptions(value: object) -> set[tuple[str, str, str]]:
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "exceptions"}
        or type(value["version"]) is not int
        or value["version"] != 2
        or not isinstance(value["exceptions"], list)
    ):
        raise ValueError("exceptions must contain exactly version 2 and exceptions")
    exceptions: set[tuple[str, str, str]] = set()
    for number, item in enumerate(value["exceptions"], 1):
        if not isinstance(item, dict) or set(item) != {"id", "image_id", "artifact_digest", "rationale"}:
            raise ValueError(f"exception {number} is invalid")
        key = (
            text(item["id"], f"exception {number} id"),
            text(item["image_id"], f"exception {number} image_id"),
            _digest(item["artifact_digest"], f"exception {number} artifact_digest"),
        )
        text(item["rationale"], f"exception {number} rationale")
        if key in exceptions:
            raise ValueError(f"duplicate exception {key[0]} for {key[1]}")
        exceptions.add(key)
    return exceptions


def _finding(value: object, scans: dict[str, str], number: int) -> tuple[tuple[str, str, str], str, list[object]]:
    required = {"id", "image_id", "artifact_digest", "severity", "package", "installed", "fixes"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError(f"finding {number} is invalid")
    identifier = text(value["id"], f"finding {number} id")
    image_id = text(value["image_id"], f"finding {number} image_id")
    digest = _digest(value["artifact_digest"], f"finding {number} artifact_digest")
    if scans.get(image_id) != digest:
        raise ValueError(f"finding {number} is not bound to its image scan artifact")
    severity = text(value["severity"], f"finding {number} severity")
    if severity not in SEVERITIES:
        raise ValueError(f"finding {number} severity must be LOW, MEDIUM, HIGH, or CRITICAL")
    text(value["package"], f"finding {number} package")
    text(value["installed"], f"finding {number} installed")
    if not isinstance(value["fixes"], list):
        raise ValueError(f"finding {number} fixes must be a list")
    for fix in value["fixes"]:
        text(fix, f"finding {number} fix")
    return (identifier, image_id, digest), severity, value["fixes"]


def _findings(
    value: object, scans: dict[str, str], exceptions: set[tuple[str, str, str]]
) -> tuple[list[str], set[tuple[str, str, str]]]:
    if not isinstance(value, list):
        raise ValueError("report findings must be a list")
    failures: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for number, item in enumerate(value, 1):
        key, severity, fixes = _finding(item, scans, number)
        if severity in {"HIGH", "CRITICAL"} and key in exceptions:
            seen.add(key)
            if fixes:
                failures.append(f"accepted fixable CVE: {key[0]} ({key[1]})")
        elif severity in {"HIGH", "CRITICAL"} and fixes:
            failures.append(f"unhandled fixable CVE: {key[0]} ({key[1]})")
    return failures, seen


def main() -> int:
    """Assess supplied evidence only; scanner execution remains consumer-owned."""
    arguments = _arguments()
    root = arguments.root.resolve()
    enrollment = _path(arguments.enrollment, root)
    inventory = _path(arguments.inventory, root)
    report = _path(arguments.report, root)
    exceptions = _path(arguments.exceptions, root)
    try:
        as_of = _timestamp(arguments.as_of, "--as-of")
        if arguments.max_age_hours <= 0:
            raise ValueError("--max-age-hours must be positive")
        tracked = {path.resolve() for path in tracked_files(root)}
        if {enrollment.resolve(), inventory.resolve(), exceptions.resolve()} - tracked:
            raise ValueError("enrollment, inventory, and exceptions must be tracked")
        if report.resolve() in tracked:
            raise ValueError("report must be untracked evidence")
        identifiers = _inventory(read_json(inventory, "container image inventory"))
        _ledger(read_json(enrollment, "container image enrollment"))
        enrollment_sha256 = _sha256(enrollment, "container image enrollment")
        evidence = read_json(report, "container immutable assessment")
        required = {"version", "enrollment_sha256", "scans", "findings"}
        if (
            not isinstance(evidence, dict)
            or set(evidence) != required
            or type(evidence["version"]) is not int
            or evidence["version"] != 2
        ):
            raise ValueError("report must contain exactly version 2, enrollment_sha256, scans, and findings")
        if text(evidence["enrollment_sha256"], "report enrollment_sha256") != enrollment_sha256:
            raise ValueError("report enrollment_sha256 does not match the enrollment file")
        scans = _scans(evidence["scans"], identifiers, root, tracked, (as_of, timedelta(hours=arguments.max_age_hours)))
        known = _exceptions(read_json(exceptions, "container image exceptions"))
        failures, seen = _findings(evidence["findings"], scans, known)
        for identifier, image_id, _ in sorted(known - seen):
            failures.append(f"stale exception: {identifier} ({image_id})")
        scope = len(scans)
    except (OSError, RuntimeError, ValueError) as exc:
        failures, scope = [str(exc)], 0
    if failures:
        print("container immutable assessment failed:", *[f"  {item}" for item in failures], sep="\n", file=sys.stderr)
        return 1
    print(f"container immutable assessment clean scope={scope}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
