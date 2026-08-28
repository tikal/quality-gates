"""Apply CVE exception policy to consumer-produced container scanner reports."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from quality_gates.discovery import tracked_files
from quality_gates.security_policy import read_json, text, versioned_list


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fail new fixable container CVEs and stale image-scoped exceptions.")
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--exceptions", required=True, type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _images(value: object) -> set[str]:
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "images"}
        or value["version"] != 1
        or type(value["version"]) is not int
        or not isinstance(value["images"], list)
    ):
        raise ValueError("inventory must contain exactly version 1 and images")
    images: set[str] = set()
    for number, item in enumerate(value["images"], 1):
        if not isinstance(item, dict) or set(item) != {"id", "reference"}:
            raise ValueError(f"image {number} must contain exactly id and reference")
        reference = text(item["reference"], f"image {number} reference")
        text(item["id"], f"image {number} id")
        if reference in images:
            raise ValueError(f"duplicate image reference {reference}")
        images.add(reference)
    if not images:
        raise ValueError("inventory has 0 images; a clean result would be meaningless")
    return images


def _report(value: object, images: set[str]) -> tuple[int, list[object]]:
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "scanned_units", "scanned_images", "findings"}
        or value["version"] != 1
        or type(value["version"]) is not int
        or type(value["scanned_units"]) is not int
        or value["scanned_units"] < 1
        or not isinstance(value["scanned_images"], list)
        or not isinstance(value["findings"], list)
    ):
        raise ValueError("report must contain version 1, positive scanned_units, scanned_images, and findings")
    scanned_images: set[str] = set()
    for number, image in enumerate(value["scanned_images"], 1):
        reference = text(image, f"scanned image {number}")
        if reference in scanned_images:
            raise ValueError(f"duplicate scanned image reference {reference}")
        scanned_images.add(reference)
    if value["scanned_units"] != len(scanned_images) or scanned_images != images:
        raise ValueError("report scanned images must exactly match the enrolled image references")
    return value["scanned_units"], value["findings"]


def _exceptions(value: object) -> set[tuple[str, str]]:
    allowed: set[tuple[str, str]] = set()
    for number, item in enumerate(versioned_list(value, "exceptions", "exceptions"), 1):
        if not isinstance(item, dict) or set(item) != {"id", "image", "rationale"}:
            raise ValueError(f"exception {number} is invalid")
        key = (text(item["id"], f"exception {number} id"), text(item["image"], f"exception {number} image"))
        text(item["rationale"], f"exception {number} rationale")
        if key in allowed:
            raise ValueError(f"duplicate exception {key[0]} for {key[1]}")
        allowed.add(key)
    return allowed


def _evaluate_findings(
    findings: list[object], images: set[str], allowed: set[tuple[str, str]]
) -> tuple[list[str], set[tuple[str, str]]]:
    seen: set[tuple[str, str]] = set()
    failures: list[str] = []
    for number, item in enumerate(findings, 1):
        if not isinstance(item, dict) or set(item) != {"id", "image", "severity", "package", "installed", "fixes"}:
            raise ValueError(f"finding {number} is invalid")
        identifier = text(item["id"], f"finding {number} id")
        image = text(item["image"], f"finding {number} image")
        if image not in images:
            raise ValueError(f"finding {number} references unenrolled image {image}")
        severity = text(item["severity"], f"finding {number} severity")
        if severity not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}:
            raise ValueError(f"finding {number} severity must be LOW, MEDIUM, HIGH, or CRITICAL")
        text(item["package"], f"finding {number} package")
        text(item["installed"], f"finding {number} installed")
        if not isinstance(item["fixes"], list):
            raise ValueError(f"finding {number} fixes must be a list")
        for fix in item["fixes"]:
            text(fix, f"finding {number} fix")
        key = (identifier, image)
        requires_exception = severity in {"HIGH", "CRITICAL"} and bool(item["fixes"])
        if requires_exception and key in allowed:
            seen.add(key)
        elif requires_exception:
            failures.append(f"unhandled fixable CVE: {identifier} ({image})")
    return failures, seen


def main() -> int:
    """Evaluate normalized scanner output while leaving scanner execution to the consumer."""
    arguments = _arguments()
    root = arguments.root.resolve()
    paths = {path.resolve() for path in tracked_files(root)}
    inventory = arguments.inventory if arguments.inventory.is_absolute() else root / arguments.inventory
    report = arguments.report if arguments.report.is_absolute() else root / arguments.report
    exceptions = arguments.exceptions if arguments.exceptions.is_absolute() else root / arguments.exceptions
    try:
        if inventory.resolve() not in paths or exceptions.resolve() not in paths:
            raise ValueError("inventory and exceptions must be tracked")
        images = _images(read_json(inventory, "image inventory"))
        scope, findings = _report(read_json(report, "image report"), images)
        allowed = _exceptions(read_json(exceptions, "image exceptions"))
        failures, seen = _evaluate_findings(findings, images, allowed)
        failures.extend(f"stale exception: {identifier} ({image})" for identifier, image in sorted(allowed - seen))
    except (OSError, RuntimeError, ValueError) as exc:
        failures, scope = [str(exc)], 0
    if failures:
        print("container image CVE scan failed:", *[f"  {item}" for item in failures], sep="\n", file=sys.stderr)
        return 1
    print(f"container image CVE scan clean scope={scope}")
    return 0
