"""Validate a tracked Dockerfile-to-container-inventory enrollment graph."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from quality_gates.discovery import tracked_files
from quality_gates.security_policy import read_json, relative_path, text

CLASSIFICATIONS = frozenset(("build", "pull", "ignore"))


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate explicit Dockerfile-to-image inventory enrollment.")
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _dockerfiles(paths: list[Path], root: Path) -> tuple[set[str], list[str]]:
    dockerfiles: set[str] = set()
    failures: list[str] = []
    for path in paths:
        if path.name.startswith("Dockerfile"):
            relative = path.relative_to(root).as_posix()
            try:
                path.read_bytes()
            except OSError as exc:
                failures.append(f"cannot read Dockerfile {relative}: {exc}")
            else:
                dockerfiles.add(relative)
    return dockerfiles, failures


def _inventory(value: object) -> set[str]:
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "images"}
        or type(value["version"]) is not int
        or value["version"] != 1
        or not isinstance(value["images"], list)
    ):
        raise ValueError("inventory must contain exactly version 1 and images")
    identifiers: set[str] = set()
    for number, item in enumerate(value["images"], 1):
        if not isinstance(item, dict) or set(item) != {"id", "reference"}:
            raise ValueError(f"inventory image {number} must contain exactly id and reference")
        identifier = text(item["id"], f"inventory image {number} id")
        text(item["reference"], f"inventory image {number} reference")
        if identifier in identifiers:
            raise ValueError(f"duplicate inventory ID {identifier}")
        identifiers.add(identifier)
    if not identifiers:
        raise ValueError("inventory has 0 image IDs; a clean result would be meaningless")
    return identifiers


def _ledger(value: object) -> tuple[dict[str, str], dict[str, str | None]]:
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "dockerfiles", "image_sources"}
        or type(value["version"]) is not int
        or value["version"] != 1
        or not isinstance(value["dockerfiles"], list)
        or not isinstance(value["image_sources"], list)
    ):
        raise ValueError("ledger must contain exactly version 1, dockerfiles, and image_sources")
    classifications: dict[str, str] = {}
    for number, item in enumerate(value["dockerfiles"], 1):
        path, classification = _dockerfile_record(item, number)
        if path in classifications:
            raise ValueError(f"duplicate Dockerfile classification for {path}")
        classifications[path] = classification
    sources: dict[str, str | None] = {}
    for number, item in enumerate(value["image_sources"], 1):
        identifier, source = _image_source(item, number)
        if identifier in sources:
            raise ValueError(f"duplicate inventory mapping for {identifier}")
        sources[identifier] = source
    return classifications, sources


def _dockerfile_record(value: object, number: int) -> tuple[str, str]:
    if not isinstance(value, dict):
        raise ValueError(f"Dockerfile record {number} must be an object")
    classification = value.get("classification")
    expected = {"path", "classification"}
    if classification == "ignore":
        expected.add("rationale")
    if set(value) != expected or classification not in CLASSIFICATIONS:
        raise ValueError(f"Dockerfile record {number} has an invalid classification")
    path = relative_path(value["path"], f"Dockerfile record {number} path")
    if classification == "ignore":
        text(value["rationale"], f"Dockerfile record {number} rationale")
    return path, classification


def _image_source(value: object, number: int) -> tuple[str, str | None]:
    if not isinstance(value, dict):
        raise ValueError(f"image source {number} must be an object")
    identifier = text(value.get("id"), f"image source {number} id")
    if set(value) == {"id", "dockerfile"}:
        return identifier, relative_path(value["dockerfile"], f"image source {number} Dockerfile")
    if set(value) == {"id", "external", "rationale"}:
        text(value["external"], f"image source {number} external source")
        text(value["rationale"], f"image source {number} rationale")
        return identifier, None
    raise ValueError(f"image source {number} must map to one Dockerfile or a rationale-backed external source")


def main() -> int:
    """Validate consumer-owned declarations without building, pulling, or scanning images."""
    arguments = _arguments()
    root = arguments.root.resolve()
    ledger = arguments.ledger if arguments.ledger.is_absolute() else root / arguments.ledger
    inventory = arguments.inventory if arguments.inventory.is_absolute() else root / arguments.inventory
    try:
        paths = tracked_files(root)
        tracked = {path.resolve() for path in paths}
        if ledger.resolve() not in tracked or inventory.resolve() not in tracked:
            raise ValueError("ledger and inventory must be tracked")
        dockerfiles, failures = _dockerfiles(paths, root)
        if not dockerfiles:
            raise ValueError("scanned 0 Dockerfiles; a clean result would be meaningless")
        if failures:
            raise ValueError("; ".join(failures))
        identifiers = _inventory(read_json(inventory, "container image inventory"))
        classifications, sources = _ledger(read_json(ledger, "container image enrollment ledger"))
        failures = [f"unclassified Dockerfile: {path}" for path in sorted(dockerfiles - set(classifications))]
        failures.extend(
            f"stale Dockerfile classification: {path}" for path in sorted(set(classifications) - dockerfiles)
        )
        failures.extend(
            f"unmapped non-ignored Dockerfile: {path}"
            for path, kind in sorted(classifications.items())
            if kind != "ignore" and path not in sources.values()
        )
        failures.extend(f"orphan inventory mapping: {identifier}" for identifier in sorted(set(sources) - identifiers))
        failures.extend(f"missing inventory mapping: {identifier}" for identifier in sorted(identifiers - set(sources)))
        for identifier, path in sorted(sources.items()):
            if path is not None and path not in classifications:
                failures.append(f"image {identifier} maps to unknown Dockerfile: {path}")
            elif path is not None and classifications[path] == "ignore":
                failures.append(f"image {identifier} maps to ignored Dockerfile: {path}")
    except (OSError, RuntimeError, ValueError) as exc:
        failures = [str(exc)]
        dockerfiles = set()
    if failures:
        print("container image enrollment failed:", *[f"  {item}" for item in failures], sep="\n", file=sys.stderr)
        return 1
    print(f"container image enrollment clean scope={len(dockerfiles)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
