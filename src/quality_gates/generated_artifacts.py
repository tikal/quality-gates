"""Verify deterministic generated artifacts against staged Git index bytes."""

from __future__ import annotations

import argparse
import os
import signal
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


def _timeout(value: str) -> int:
    try:
        seconds = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be an integer from 1 to 300 seconds") from exc
    if not 1 <= seconds <= 300:
        raise argparse.ArgumentTypeError("timeout must be an integer from 1 to 300 seconds")
    return seconds


def _environment(value: str) -> tuple[str, str]:
    key, separator, setting = value.partition("=")
    if not separator or not key or "\x00" in value:
        raise argparse.ArgumentTypeError("environment values must use NAME=VALUE")
    return key, setting


def _environment_name(value: str) -> str:
    if not value or "=" in value or "\x00" in value:
        raise argparse.ArgumentTypeError("environment names must be nonempty names without =")
    return value


def _run(command: list[str], snapshot: Path, output: Path, timeout: int, env: dict[str, str]) -> None:
    env["QUALITY_GATES_OUTPUT_DIR"] = str(output)
    env["PWD"] = str(snapshot)
    process = subprocess.Popen(
        command,
        cwd=snapshot,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        _, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            _, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            _, stderr = process.communicate()
        raise RuntimeError(f"generator timed out: {stderr.decode('utf-8', 'replace').strip()}") from None
    result = subprocess.CompletedProcess(command, process.returncode, stderr=stderr)
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip() or f"exited {result.returncode}"
        raise RuntimeError(f"generator failed: {detail}")


def _index_entry(root: Path, artifact: str) -> tuple[bytes, int]:
    mode = _git(root, ["ls-files", "-s", "--", artifact]).stdout.split(maxsplit=1)[0]
    if mode != b"100644" and mode != b"100755":
        raise RuntimeError(f"{artifact}: staged artifact must be a regular file")
    return _git(root, ["show", f":{artifact}"]).stdout, int(mode, 8) & 0o777


def _generated_entry(output: Path, artifact: str) -> tuple[bytes, int]:
    path = output
    for part in Path(artifact).parts:
        path /= part
        if path.exists() and path.is_symlink():
            raise RuntimeError(f"{artifact}: generated output path must not traverse a symlink")
    if not stat.S_ISREG(path.lstat().st_mode):
        raise RuntimeError(f"{artifact}: generated output must be a regular file")
    return path.read_bytes(), stat.S_IMODE(path.lstat().st_mode)


def _snapshot(root: Path, destination: Path, artifacts: list[str]) -> None:
    paths = [path.decode("utf-8") for path in _git(root, ["ls-files", "-z"]).stdout.split(b"\0") if path]
    for path in paths:
        if path in artifacts:
            continue
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        contents, mode = _index_entry(root, path)
        target.write_bytes(contents)
        target.chmod(mode)


def _extra_outputs(output: Path, artifacts: set[str]) -> list[str]:
    extras = []
    for path in output.rglob("*"):
        relative = path.relative_to(output).as_posix()
        if relative in artifacts or (path.is_dir() and not path.is_symlink()):
            continue
        extras.append(relative)
    return extras


def main() -> int:  # noqa: C901
    parser = argparse.ArgumentParser(description="Verify deterministic generated artifacts against the staged index.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--artifact", action="append", type=_path, required=True)
    parser.add_argument(
        "--timeout-seconds",
        type=_timeout,
        default=30,
        help="maximum generator runtime in seconds (default: 30; range: 1-300)",
    )
    parser.add_argument("--reject-extra-outputs", action="store_true")
    parser.add_argument("--clear-env", action="store_true")
    parser.add_argument("--env", action="append", type=_environment, default=[])
    parser.add_argument("--inherit-env", action="append", type=_environment_name, default=[])
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
    environment = (
        {} if arguments.clear_env else {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    )
    environment.update(
        {key: os.environ[key] for key in arguments.inherit_env if key in os.environ and not key.startswith("GIT_")}
    )
    environment.update({key: value for key, value in arguments.env if not key.startswith("GIT_")})
    findings: list[str] = []
    try:
        for artifact in arguments.artifact:
            _git(root, ["ls-files", "--error-unmatch", "--", artifact])
        staged = {artifact: _index_entry(root, artifact) for artifact in arguments.artifact}
        with tempfile.TemporaryDirectory() as temporary:
            outputs = []
            for name in ("first", "second"):
                snapshot = Path(temporary) / f"index-{name}"
                snapshot.mkdir()
                _snapshot(root, snapshot, arguments.artifact)
                output = Path(temporary) / name
                output.mkdir()
                _run(command, snapshot, output, arguments.timeout_seconds, environment.copy())
                outputs.append(output)
            if arguments.reject_extra_outputs:
                artifacts = set(arguments.artifact)
                for output in outputs:
                    findings.extend(f"{path}: extra generated output" for path in _extra_outputs(output, artifacts))
            for artifact in arguments.artifact:
                generated = [_generated_entry(output, artifact) for output in outputs]
                if generated[0] != generated[1]:
                    findings.append(f"{artifact}: generator output is nondeterministic")
                elif staged[artifact][0] != generated[0][0]:
                    findings.append(f"{artifact}: staged bytes differ from generated output")
                elif staged[artifact][1] != generated[0][1]:
                    findings.append(f"{artifact}: staged mode differs from generated output")
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
