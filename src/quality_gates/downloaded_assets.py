"""Require reviewed enrollment for direct external asset acquisition sites."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from quality_gates.discovery import tracked_files
from quality_gates.security_policy import read_json, relative_path, text

KINDS = frozenset(("sha256", "signature", "repository-signature", "digest", "unverified", "ignore"))


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Require direct downloaded assets to have reviewed verification enrollment."
    )
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--candidate-file-regex", action="append", required=True, metavar="REGEX")
    parser.add_argument("--download-site-regex", action="append", required=True, metavar="REGEX")
    parser.add_argument("--allow-kind", action="append", default=[], choices=sorted(KINDS))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _records(value: object) -> dict[tuple[str, str], str]:
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "assets"}
        or value["version"] != 1
        or type(value["version"]) is not int
    ):
        raise ValueError("ledger must contain exactly version 1 and assets")
    if not isinstance(value["assets"], list):
        raise ValueError("ledger assets must be a list")
    records: dict[tuple[str, str], str] = {}
    for number, item in enumerate(value["assets"], 1):
        if not isinstance(item, dict):
            raise ValueError(f"asset {number} must be an object")
        kind = item.get("kind")
        expected = {"path", "selector", "kind"}
        if kind in {"unverified", "ignore"}:
            expected.add("rationale")
        if set(item) != expected or kind not in KINDS:
            raise ValueError(f"asset {number} has an invalid verification record")
        key = (relative_path(item["path"], f"asset {number} path"), text(item["selector"], f"asset {number} selector"))
        if key in records:
            raise ValueError(f"duplicate asset record for {key[0]}: {key[1]}")
        if "rationale" in item:
            text(item["rationale"], f"asset {number} rationale")
        records[key] = kind
    return records


def main() -> int:
    """Check every configured acquisition match against a tracked enrollment ledger."""
    arguments = _arguments()
    root = arguments.root.resolve()
    ledger = arguments.ledger if arguments.ledger.is_absolute() else root / arguments.ledger
    try:
        candidates = [re.compile(pattern) for pattern in arguments.candidate_file_regex]
        patterns = [re.compile(pattern) for pattern in arguments.download_site_regex]
        paths = tracked_files(root)
        if ledger.resolve() not in {path.resolve() for path in paths}:
            raise ValueError(f"ledger is not tracked: {ledger}")
        records = _records(read_json(ledger, "asset ledger"))
        sites: set[tuple[str, str]] = set()
        for path in paths:
            relative = path.relative_to(root).as_posix()
            if any(pattern.search(relative) for pattern in candidates):
                source = path.read_text(encoding="utf-8")
                for pattern in patterns:
                    sites.update((relative, match.group(0)) for match in pattern.finditer(source))
        if not sites:
            raise ValueError("scanned 0 downloaded asset sites; a clean result would be meaningless")
        invalid = [
            f"unverified asset: {path}: {selector}"
            for path, selector in sorted(sites)
            if records.get((path, selector)) in {"unverified", "ignore"}
            and records[(path, selector)] not in arguments.allow_kind
        ]
        missing = sorted(sites - set(records))
        stale = sorted(set(records) - sites)
        failures = (
            invalid
            + [f"unclassified asset: {path}: {selector}" for path, selector in missing]
            + [f"stale asset: {path}: {selector}" for path, selector in stale]
        )
    except (OSError, RuntimeError, UnicodeDecodeError, ValueError, re.error) as exc:
        failures = [str(exc)]
        sites = set()
    if failures:
        print("downloaded asset enrollment failed:", *[f"  {item}" for item in failures], sep="\n", file=sys.stderr)
        return 1
    print(f"downloaded asset enrollment clean scope={len(sites)}")
    return 0
