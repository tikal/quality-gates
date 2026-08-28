"""Validate consumer-produced immutable container assessment evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from quality_gates.container_image_enrollment import _inventory
from quality_gates.security_policy import reject_constant, reject_duplicate_key, relative_path, text

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


def _git(root: Path, arguments: list[str]) -> bytes:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    try:
        result = subprocess.run(["git", "-C", str(root), *arguments], capture_output=True, check=False, env=environment)
    except OSError as exc:
        raise RuntimeError(f"cannot read the staged index: git is unavailable ({exc})") from exc
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip() or f"git exited {result.returncode}"
        raise RuntimeError(f"cannot read the staged index: {detail}")
    return result.stdout


def _regular_file(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve()
    if root not in resolved.parents:
        raise ValueError(f"{label} must be under the repository root")
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise ValueError(f"cannot read {label} {path}: {exc}") from exc
    if not stat.S_ISREG(mode):
        raise ValueError(f"{label} must be a regular non-symlink file")
    return resolved


def _staged_file(value: Path, root: Path, label: str) -> tuple[str, bytes]:
    resolved = _regular_file(_path(value, root), root, label)
    relative = resolved.relative_to(root).as_posix()
    listing = _git(root, ["ls-files", "-s", "--", relative]).split(maxsplit=1)
    if not listing:
        raise ValueError(f"{label} must be tracked")
    if listing[0] not in {b"100644", b"100755"}:
        raise ValueError(f"{label} must be a regular non-symlink staged file")
    return relative, _git(root, ["show", f":{relative}"])


def _read_staged_json(contents: bytes, label: str, path: str) -> object:
    try:
        return json.loads(
            contents.decode("utf-8"), object_pairs_hook=reject_duplicate_key, parse_constant=reject_constant
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"cannot parse {label} {path}: {type(exc).__name__}: {exc}") from exc


def _enrollment_graph(root: Path, enrollment: str, inventory: str) -> None:
    paths = [path.decode("utf-8") for path in _git(root, ["ls-files", "-z"]).split(b"\0") if path]
    with tempfile.TemporaryDirectory() as directory:
        snapshot = Path(directory)
        _git(snapshot, ["init", "-q"])
        for path in paths:
            target = snapshot / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(_git(root, ["show", f":{path}"]))
        _git(snapshot, ["add", "--all"])
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "quality_gates.container_image_enrollment",
                "--root",
                str(snapshot),
                "--ledger",
                enrollment,
                "--inventory",
                inventory,
            ],
            capture_output=True,
            check=False,
        )
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise ValueError(f"container image enrollment graph is invalid: {detail}")


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


def _raw_evidence(value: object, root: Path, tracked: set[str], number: int) -> None:
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        raise ValueError(f"scan {number} raw_evidence must contain exactly path and sha256")
    relative = relative_path(value["path"], f"scan {number} raw evidence path")
    path = root / relative
    resolved = _regular_file(path, root, f"scan {number} raw evidence")
    if relative in tracked:
        raise ValueError(f"scan {number} raw evidence must be an untracked file under the repository root")
    expected = text(value["sha256"], f"scan {number} raw evidence sha256")
    if not SHA256.fullmatch(expected):
        raise ValueError(f"scan {number} raw evidence sha256 must be lowercase SHA-256")
    if _sha256(resolved, f"scan {number} raw evidence") != expected:
        raise ValueError(f"scan {number} raw evidence SHA-256 does not match")


def _scans(
    value: object, identifiers: set[str], root: Path, tracked: set[str], freshness: tuple[datetime, timedelta]
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
    try:
        as_of = _timestamp(arguments.as_of, "--as-of")
        if arguments.max_age_hours <= 0:
            raise ValueError("--max-age-hours must be positive")
        enrollment_path, enrollment_bytes = _staged_file(arguments.enrollment, root, "container image enrollment")
        inventory_path, inventory_bytes = _staged_file(arguments.inventory, root, "container image inventory")
        exceptions_path, exceptions_bytes = _staged_file(arguments.exceptions, root, "container image exceptions")
        tracked = {path.decode("utf-8") for path in _git(root, ["ls-files", "-z"]).split(b"\0") if path}
        report_relative = relative_path(arguments.report.as_posix(), "report path")
        report = _regular_file(root / report_relative, root, "report")
        if report_relative in tracked:
            raise ValueError("report must be untracked evidence")
        identifiers = _inventory(_read_staged_json(inventory_bytes, "container image inventory", inventory_path))
        _enrollment_graph(root, enrollment_path, inventory_path)
        enrollment_sha256 = hashlib.sha256(enrollment_bytes).hexdigest()
        evidence = _read_staged_json(report.read_bytes(), "container immutable assessment", report_relative)
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
        known = _exceptions(_read_staged_json(exceptions_bytes, "container image exceptions", exceptions_path))
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
