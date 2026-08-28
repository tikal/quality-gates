"""Check Dockerfile runtime base-image lifecycles from a tracked offline policy."""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

from quality_gates.discovery import tracked_files
from quality_gates.security_policy import read_json, text

FROM = re.compile(r"^\s*FROM\s+(?:--[^\s]+\s+)*(?P<reference>[^\s]+)", re.MULTILINE | re.IGNORECASE)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fail Dockerfile runtime bases whose tracked lifecycle data is end-of-life."
    )
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument(
        "--as-of",
        required=True,
        type=dt.date.fromisoformat,
        help="assessment date in YYYY-MM-DD form",
    )
    parser.add_argument("--warning-days", type=int, default=120)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _cycle(tag: str, mode: str) -> str | None:
    match = re.match(r"(\d+)(?:\.(\d+))?", tag)
    if match is None or (mode == "major.minor" and match.group(2) is None):
        return None
    return match.group(1) if mode == "major" else f"{match.group(1)}.{match.group(2)}"


def _runtimes(value: list[object]) -> dict[str, tuple[str, str]]:
    runtimes: dict[str, tuple[str, str]] = {}
    for number, item in enumerate(value, 1):
        if not isinstance(item, dict) or set(item) != {"image", "product", "cycle"}:
            raise ValueError(f"runtime {number} is invalid")
        image = text(item["image"], f"runtime {number} image")
        runtime = (text(item["product"], f"runtime {number} product"), text(item["cycle"], f"runtime {number} cycle"))
        if runtime[1] not in {"major", "major.minor"}:
            raise ValueError(f"runtime {number} cycle must be major or major.minor")
        if image in runtimes:
            raise ValueError(f"duplicate runtime image {image}")
        runtimes[image] = runtime
    if not runtimes:
        raise ValueError("policy has no runtimes")
    return runtimes


def _lifecycles(value: list[object]) -> dict[tuple[str, str], dt.date]:
    lifecycles: dict[tuple[str, str], dt.date] = {}
    for number, item in enumerate(value, 1):
        if not isinstance(item, dict) or set(item) != {"product", "cycle", "eol"}:
            raise ValueError(f"lifecycle {number} is invalid")
        key = (text(item["product"], f"lifecycle {number} product"), text(item["cycle"], f"lifecycle {number} cycle"))
        if key in lifecycles:
            raise ValueError(f"duplicate lifecycle {key[0]} {key[1]}")
        lifecycles[key] = dt.date.fromisoformat(text(item["eol"], f"lifecycle {number} eol"))
    if not lifecycles:
        raise ValueError("policy has no lifecycle data")
    return lifecycles


def _policy(value: object) -> tuple[dict[str, tuple[str, str]], dict[tuple[str, str], dt.date]]:
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "runtimes", "lifecycles"}
        or value["version"] != 1
        or type(value["version"]) is not int
        or not isinstance(value["runtimes"], list)
        or not isinstance(value["lifecycles"], list)
    ):
        raise ValueError("policy must contain exactly version 1, runtimes, and lifecycles")
    return _runtimes(value["runtimes"]), _lifecycles(value["lifecycles"])


def _image_and_tag(reference: str) -> tuple[str, str | None]:
    image = reference.split("@", 1)[0]
    component = image.rsplit("/", 1)[-1]
    if ":" not in component:
        return image, None
    name, tag = image.rsplit(":", 1)
    return name, tag


def _scan_dockerfiles(
    paths: list[Path],
    root: Path,
    runtimes: dict[str, tuple[str, str]],
    lifecycles: dict[tuple[str, str], dt.date],
    arguments: argparse.Namespace,
) -> tuple[list[str], list[str], int]:
    failures: list[str] = []
    warnings: list[str] = []
    scope = 0
    for path in paths:
        if not path.name.startswith("Dockerfile"):
            continue
        relative = path.relative_to(root).as_posix()
        source = path.read_text(encoding="utf-8").replace("\\\n", " ")
        for reference in FROM.findall(source):
            image, tag = _image_and_tag(reference)
            if image not in runtimes:
                continue
            product, mode = runtimes[image]
            cycle = _cycle(tag, mode) if tag is not None else None
            if cycle is None or (product, cycle) not in lifecycles:
                failures.append(f"unknown lifecycle: {relative}: {reference}")
                continue
            scope += 1
            eol = lifecycles[(product, cycle)]
            if eol < arguments.as_of:
                failures.append(f"end-of-life base image: {relative}: {reference} ended {eol}")
            elif eol <= arguments.as_of + dt.timedelta(days=arguments.warning_days):
                warnings.append(f"base image nearing end of life: {relative}: {reference} ends {eol}")
    return failures, warnings, scope


def main() -> int:
    """Check all tracked Dockerfile runtime stages against explicit lifecycle data."""
    arguments = _arguments()
    root = arguments.root.resolve()
    policy_path = arguments.policy if arguments.policy.is_absolute() else root / arguments.policy
    try:
        paths = tracked_files(root)
        if policy_path.resolve() not in {path.resolve() for path in paths}:
            raise ValueError(f"policy is not tracked: {policy_path}")
        runtimes, lifecycles = _policy(read_json(policy_path, "base image policy"))
        failures, warnings, scope = _scan_dockerfiles(paths, root, runtimes, lifecycles, arguments)
        if not scope:
            failures.append("scanned 0 runtime base images; a clean result would be meaningless")
    except (OSError, RuntimeError, ValueError) as exc:
        failures, warnings, scope = [str(exc)], [], 0
    for warning in warnings:
        print(warning, file=sys.stderr)
    if failures:
        print("base image EOL scan failed:", *[f"  {item}" for item in failures], sep="\n", file=sys.stderr)
        return 1
    print(f"base image EOL scan clean scope={scope}")
    return 0
