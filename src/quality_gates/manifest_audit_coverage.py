"""Require tracked manifests to be enrolled in a configured audit hook's file scope."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from quality_gates.discovery import tracked_files
from quality_gates.precommit_config import ConfigError, hooks_from


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Require every tracked manifest to be covered by an audit hook.")
    parser.add_argument("--manifest", action="append", required=True, metavar="BASENAME")
    parser.add_argument("--audit-hook-prefix", required=True, metavar="PREFIX")
    parser.add_argument("--exemptions", type=Path, metavar="PATH")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository to scan (default: current directory)")
    parser.add_argument("--pre-commit-config", type=Path, metavar="PATH")
    return parser.parse_args()


def _relative_path(value: str, ledger: Path, number: int) -> str:
    path = Path(value)
    if not value or path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise ValueError(f"{ledger}:{number}: exemption path must be exact and root-relative")
    return path.as_posix()


def _exemptions(path: Path | None) -> set[str]:
    if path is None:
        return set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"cannot read exemptions {path}: {exc}") from exc

    entries: set[str] = set()
    for number, line in enumerate(lines, 1):
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 2 or not fields[1].strip() or fields[0] in entries:
            raise ValueError(f"{path}:{number}: expected unique 'path<TAB>reason' exemption")
        entries.add(_relative_path(fields[0], path, number))
    return entries


def _audit_patterns(config: Path, prefix: str) -> tuple[list[re.Pattern[str]], list[str]]:
    try:
        hooks = hooks_from(config)
    except ConfigError as exc:
        return [], [str(exc)]

    patterns = []
    failures = []
    for hook_id, files, _ in hooks:
        if not hook_id.startswith(prefix):
            continue
        if files is None:
            failures.append(f"{hook_id}: audit hook is missing a files regex")
            continue
        try:
            patterns.append(re.compile(files))
        except re.error as exc:
            failures.append(f"{hook_id}: invalid files regex: {exc}")
    return patterns, failures


def main() -> int:
    """Report manifests not matched by an audit hook's configured ``files`` regular expression."""
    arguments = _arguments()
    root = arguments.root.resolve()
    config = arguments.pre_commit_config or root / ".pre-commit-config.yaml"
    config = config if config.is_absolute() else root / config

    try:
        exemption_path = arguments.exemptions
        if exemption_path is not None:
            exemption_path = exemption_path if exemption_path.is_absolute() else root / exemption_path
            if exemption_path.resolve() not in {path.resolve() for path in tracked_files(root)}:
                raise ValueError(f"exemptions must be tracked: {exemption_path}")
        exemptions = _exemptions(exemption_path)
        selected = [path for path in tracked_files(root) if path.name in arguments.manifest]
        for path in selected:
            path.read_bytes()
        manifests = {path.relative_to(root).as_posix() for path in selected}
    except (RuntimeError, ValueError) as exc:
        print(f"manifest audit coverage failed: {exc}", file=sys.stderr)
        return 1

    patterns, failures = _audit_patterns(config, arguments.audit_hook_prefix)
    if not patterns:
        failures.append(f"no audit hooks start with {arguments.audit_hook_prefix!r}")
    if not manifests:
        failures.append("scanned 0 dependency manifests; a clean result would be meaningless")
    covered = {manifest for manifest in manifests if any(pattern.search(manifest) for pattern in patterns)}
    uncovered = manifests - covered
    missing = uncovered - exemptions
    live_exemptions = exemptions & uncovered
    failures.extend(f"stale exemption: {manifest}" for manifest in sorted(exemptions - live_exemptions))
    failures.extend(f"uncovered manifest: {manifest}" for manifest in sorted(missing))
    if failures:
        print("manifest audit coverage failed:", file=sys.stderr)
        print(*(f"  {failure}" for failure in failures), sep="\n", file=sys.stderr)
        return 1

    print(f"manifest audit coverage clean scope={len(manifests)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
