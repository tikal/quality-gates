# quality-gates

Repository-agnostic pre-commit hooks. A gate must state the policy that it enforces without
hard-coding a consumer repository.

## Commands

- Install: `uv venv && uv pip install --python .venv/bin/python -e .`
- Test: `PATH="$PWD/.venv/bin:$PATH" bash tests/run_tests.sh`
- Lint: `uvx ruff@0.15.5 check --no-fix .`
- Format check: `uvx ruff@0.15.5 format --check .`

## Structure

- `src/quality_gates/` holds the hook implementations and shared scan logic.
- `tests/run_tests.sh` is the integration test runner.
- `.pre-commit-hooks.yaml` is the public hook interface.
- `README.md` is the public policy and command-line contract.

## Gate Contract

- Keep repository-specific values as command-line arguments.
- Keep the runtime dependency-free unless a gate needs a new dependency.
- A scan of zero files must fail.
- A file that a gate cannot read or parse must fail the gate.
- A clean run must print `scope=N` for the files it read.
- Do not silently narrow a scan or skip an input file.
- Update `README.md`, hook metadata, and tests when a public option or behavior changes.

## Development

- Reuse `discovery.py`, `source.py`, and `markers.py` for shared behavior.
- Keep each gate independent from the implementation of another gate.
- Preserve the installed hook ids and their default arguments unless a breaking change is deliberate.
- Prefer the Python standard library. This package has no runtime dependencies.
- Use Python 3.11+ and the Ruff settings in `pyproject.toml`.

## TDD

Do not write production code before a failing test.

1. Add the smallest failing case to `tests/run_tests.sh`.
2. Run the script and confirm the expected failure.
3. Make the smallest implementation change.
4. Run the script, Ruff check, and the format check.
5. Refactor only while all checks pass.

Each gate needs a test for the breach it catches and the matching clean case. A test must verify
the exit code and, when useful, the reported path or reason.
