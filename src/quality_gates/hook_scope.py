"""Require every configured pre-commit hook to state its scope or an exemption."""

from __future__ import annotations

import argparse
import re
import shlex
import sys
from pathlib import Path

from quality_gates.precommit_config import ConfigError, hooks_from


def _declarations(values: list[str], option: str) -> dict[str, str]:
    declared: dict[str, str] = {}
    for value in values:
        hook_id, separator, detail = value.partition("=")
        if not separator or not hook_id or not detail.strip() or hook_id in declared:
            raise argparse.ArgumentTypeError(f"{option} expects unique ID=VALUE declarations")
        declared[hook_id] = detail
    return declared


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Require every configured pre-commit hook to report scope=N or be exempt."
    )
    parser.add_argument("--config", type=Path, default=Path(".pre-commit-config.yaml"))
    parser.add_argument("--hook-id", required=True)
    parser.add_argument("--scope-emitter", action="append", default=[])
    parser.add_argument("--exempt", action="append", default=[])
    arguments = parser.parse_args()
    try:
        emitters = _declarations(arguments.scope_emitter, "--scope-emitter")
        exemptions = _declarations(arguments.exempt, "--exempt")
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    if emitters.keys() & exemptions.keys():
        parser.error("a hook cannot be both a scope emitter and exempt")
    config = arguments.config.resolve()
    root = config.parent
    try:
        hooks = hooks_from(config)
    except ConfigError as exc:
        print(f"hook scope contract failed: {exc}", file=sys.stderr)
        return 1
    configured = {hook_id for hook_id, _, _, _ in hooks}
    entries = {hook_id: entry for hook_id, _, entry, _ in hooks}
    findings = [f"UNREGISTERED: {hook_id}" for hook_id in sorted(configured - emitters.keys() - exemptions.keys())]
    findings.extend(f"stale scope-emitter: {hook_id}" for hook_id in sorted(emitters.keys() - configured))
    findings.extend(f"stale exemption: {hook_id}" for hook_id in sorted(exemptions.keys() - configured))
    if arguments.hook_id not in emitters:
        findings.append(f"{arguments.hook_id}: auditor must report scope")
    for hook_id, emitter in emitters.items():
        candidate = root / emitter
        try:
            candidate.resolve().relative_to(root)
            source = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            findings.append(f"{hook_id}: cannot read emitter {emitter}: {exc}")
            continue
        if not re.search(r"(?m)^(?!\s*#).*scope=(?:\{|\$|%)", source):
            findings.append(f"{hook_id}: emitter missing dynamic scope= output: {emitter}")
        elif not _executes(entries.get(hook_id), emitter):
            findings.append(f"{hook_id}: hook entry does not execute emitter {emitter}")
    if findings:
        print("hook scope contract failed:", file=sys.stderr)
        print(*(f"  {finding}" for finding in findings), sep="\n", file=sys.stderr)
        return 1
    print(f"hook scope contract clean reporting={len(emitters)} exempt={len(exemptions)} scope={len(hooks)}")
    return 0


def _executes(entry: str | None, emitter: str) -> bool:
    if entry is None:
        return False
    try:
        command = shlex.split(entry)
    except ValueError:
        return False
    return command == [emitter] or len(command) > 1 and command[0].startswith("python") and command[1] == emitter


if __name__ == "__main__":
    sys.exit(main())
