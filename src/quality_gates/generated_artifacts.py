"""Verify deterministic generated artifacts against staged Git index bytes."""

from __future__ import annotations

import argparse
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


def _git(root: Path, arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    result = subprocess.run(["git", "-C", str(root), *arguments], capture_output=True, env=env, **kwargs)
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip() or "git command failed"
        raise RuntimeError(detail)
    return result


def _path(value: str) -> str:
    path = Path(value)
    if not value or path.is_absolute() or "\\" in value or any(part in {"", ".", ".."} for part in value.split("/")):
        raise argparse.ArgumentTypeError("artifact must be an exact root-relative POSIX path")
    return path.as_posix()


def _run(command: list[str], snapshot: Path, output: Path) -> None:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["QUALITY_GATES_OUTPUT_DIR"] = str(output)
    env["PWD"] = str(snapshot)
    result = subprocess.run(command, cwd=snapshot, env=env, capture_output=True, timeout=30)
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip() or f"exited {result.returncode}"
        raise RuntimeError(f"generator failed: {detail}")


def _index_blob(root: Path, artifact: str) -> bytes:
    mode = _git(root, ["ls-files", "-s", "--", artifact]).stdout.split(maxsplit=1)[0]
    if mode != b"100644" and mode != b"100755":
        raise RuntimeError(f"{artifact}: staged artifact must be a regular file")
    return _git(root, ["show", f":{artifact}"]).stdout


def _generated_blob(output: Path, artifact: str) -> bytes:
    path = output
    for part in Path(artifact).parts:
        path /= part
        if path.exists() and path.is_symlink():
            raise RuntimeError(f"{artifact}: generated output path must not traverse a symlink")
    if not stat.S_ISREG(path.lstat().st_mode):
        raise RuntimeError(f"{artifact}: generated output must be a regular file")
    return path.read_bytes()


def main() -> int:  # noqa: C901
    parser = argparse.ArgumentParser(description="Verify deterministic generated artifacts against the staged index.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--artifact", action="append", type=_path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    if "--" not in sys.argv:
        parser.error("a generator argv is required after --")
    arguments = parser.parse_args()
    command = arguments.command[1:] if arguments.command[:1] == ["--"] else arguments.command
    if not command:
        parser.error("a generator argv is required after --")
    if len(set(arguments.artifact)) != len(arguments.artifact):
        parser.error("--artifact values must be unique")
    root = arguments.root.resolve()
    findings: list[str] = []
    try:
        for artifact in arguments.artifact:
            _git(root, ["ls-files", "--error-unmatch", "--", artifact])
        staged = {artifact: _index_blob(root, artifact) for artifact in arguments.artifact}
        with tempfile.TemporaryDirectory() as temporary:
            outputs = []
            for name in ("first", "second"):
                snapshot = Path(temporary) / f"index-{name}"
                snapshot.mkdir()
                _git(root, ["checkout-index", "--all", f"--prefix={snapshot}/"])
                output = Path(temporary) / name
                output.mkdir()
                _run(command, snapshot, output)
                outputs.append(output)
            for artifact in arguments.artifact:
                generated = [_generated_blob(output, artifact) for output in outputs]
                if generated[0] != generated[1]:
                    findings.append(f"{artifact}: generator output is nondeterministic")
                elif staged[artifact] != generated[0]:
                    findings.append(f"{artifact}: staged bytes differ from generated output")
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        findings.append(str(exc))
    if findings:
        print("generated artifact freshness failed:", file=sys.stderr)
        print(*(f"  {finding}" for finding in findings), sep="\n", file=sys.stderr)
        return 1
    print(f"generated artifact freshness clean scope={len(arguments.artifact)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
