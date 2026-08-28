#!/bin/bash
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TEMPORARY="$(mktemp -d)"
HOOK_REPOSITORY="$TEMPORARY/hook-repository"
CONSUMER="$TEMPORARY/consumer"
trap 'rm -rf "$TEMPORARY"' EXIT

run_hook() {
    (
        cd "$CONSUMER"
        uv run --isolated --with pre-commit==4.6.0 pre-commit run "$1" --all-files
    )
}

expect_pass() {
    local label="$1" hook="$2" output
    if output="$(run_hook "$hook" 2>&1)"; then
        printf '  ok %s\n' "$label"
    else
        printf '  FAIL %s\n%s\n' "$label" "$output" >&2
        exit 1
    fi
}

expect_failure() {
    local label="$1" hook="$2" message="$3" output
    if output="$(run_hook "$hook" 2>&1)"; then
        printf '  FAIL %s accepted invalid input\n' "$label" >&2
        exit 1
    elif [[ "$output" == *"$message"* ]]; then
        printf '  ok %s\n' "$label"
    else
        printf '  FAIL %s reported an unexpected failure\n%s\n' "$label" "$output" >&2
        exit 1
    fi
}

mkdir -p "$HOOK_REPOSITORY"
git -C "$ROOT" archive --format=tar HEAD | tar -xf - -C "$HOOK_REPOSITORY"
git -C "$ROOT" diff --binary HEAD | git -C "$HOOK_REPOSITORY" apply --allow-empty
while IFS= read -r -d '' path; do
    mkdir -p "$HOOK_REPOSITORY/$(dirname "$path")"
    cp -p "$ROOT/$path" "$HOOK_REPOSITORY/$path"
done < <(git -C "$ROOT" ls-files --others --exclude-standard -z)
git -C "$HOOK_REPOSITORY" init -q
git -C "$HOOK_REPOSITORY" config user.email test@example.com
git -C "$HOOK_REPOSITORY" config user.name test
git -C "$HOOK_REPOSITORY" add -A
git -C "$HOOK_REPOSITORY" commit -qm package

mkdir -p "$CONSUMER"
git -C "$CONSUMER" init -q
git -C "$CONSUMER" config user.email test@example.com
git -C "$CONSUMER" config user.name test
printf 'repos:\n  - repo: file://%s\n    rev: %s\n    hooks:\n      - id: check-inline-comments\n        args: [--no-baseline, inline.py]\n      - id: dict-param-check\n        args: [--baseline, .quality/dict-params.txt, dict.py]\n      - id: check-marker-budget\n        args: [--ceiling, "1"]\n      - id: check-marker-preservation\n      - id: check-generated-artifact-freshness\n        args: [--artifact, generated/output.txt, --, python, tools/generate.py]\n      - id: check-dead-code\n        args: [--path, dead.py, --min-confidence, "80"]\n      - id: check-forbidden-mocks\n        args: [--factory-location, tests/factories.py, mocks.py]\n      - id: check-pytest-describe\n        args: [test_shape.py]\n      - id: check-hook-scope-contract\n        args: [--config, scope-contract.yaml, --hook-id, check-hook-scope-contract, --scope-emitter, check-hook-scope-contract=hooks/scope-emitter.py]\n      - id: check-manifest-audit-coverage\n        args: [--manifest, pyproject.toml, --audit-hook-prefix, dependency-audit-, --pre-commit-config, audit-hooks.yaml]\n      - id: check-dockerfile-enrollment\n        args: [--ledger, .quality/dockerfile-enrollment.json]\n      - id: check-downloaded-asset-enrollment\n        args: [--ledger, .quality/assets.json, --candidate-file-regex, ".*[.]sh$", --download-site-regex, "https://example[.]test/[^ ]+"]\n      - id: check-vulnerability-exceptions\n        args: [--report, audit.json, --ledger, .quality/vulnerabilities.json]\n      - id: check-container-image-cves\n        args: [--inventory, .quality/images.json, --report, image-report.json, --exceptions, .quality/image-cve-exceptions.json]\n      - id: check-base-image-eol\n        args: [--policy, .quality/base-images.json, --as-of, "2026-08-28"]\n      - id: check-duplication\n' \
    "$HOOK_REPOSITORY" "$(git -C "$HOOK_REPOSITORY" rev-parse HEAD)" > "$CONSUMER/.pre-commit-config.yaml"
mkdir -p "$CONSUMER/.quality" "$CONSUMER/hooks" "$CONSUMER/tools" "$CONSUMER/generated"
printf 'dict.py\tdict-param\t76e94348139b\t1\n' > "$CONSUMER/.quality/dict-params.txt"
printf 'VALUE = 1\n' > "$CONSUMER/inline.py"
printf 'payload\n' > "$CONSUMER/input.txt"
printf 'import os; from pathlib import Path; root = Path(os.environ["QUALITY_GATES_OUTPUT_DIR"]); root.joinpath("generated/output.txt").parent.mkdir(parents=True, exist_ok=True); root.joinpath("generated/output.txt").write_bytes(Path("input.txt").read_bytes())\n' > "$CONSUMER/tools/generate.py"
printf 'payload\n' > "$CONSUMER/generated/output.txt"
printf '# NOTE: preserve this fact\nVALUE = 1\n' > "$CONSUMER/marker.py"
printf 'def public(value: dict) -> int:\n    return 1\n' > "$CONSUMER/dict.py"
printf 'def used() -> int:\n    return 1\n\nused()\n' > "$CONSUMER/dead.py"
printf 'VALUE = 1\n' > "$CONSUMER/mocks.py"
printf 'def describe_shape():\n    def test_is_valid():\n        assert True\n' > "$CONSUMER/test_shape.py"
printf '[project]\nname = "consumer"\nversion = "0.0.0"\nrequires-python = ">=3.11"\n\n[tool.vulture]\nexclude = ["dead.py"]\n' > "$CONSUMER/pyproject.toml"
printf 'repos:\n  - repo: local\n    hooks:\n      - id: check-hook-scope-contract\n        entry: python hooks/scope-emitter.py\n' > "$CONSUMER/scope-contract.yaml"
printf 'print(f"scope={1}")\n' > "$CONSUMER/hooks/scope-emitter.py"
printf 'repos:\n  - repo: local\n    hooks:\n      - id: dependency-audit-python\n        files: ^pyproject\\.toml$\n' > "$CONSUMER/audit-hooks.yaml"
printf 'curl -fsS https://example.test/tool.tar.gz -o tool.tar.gz\n' > "$CONSUMER/download.sh"
printf '{"version":1,"assets":[{"path":"download.sh","selector":"https://example.test/tool.tar.gz","kind":"sha256"}]}' \
    > "$CONSUMER/.quality/assets.json"
printf '{"version":1,"scanned_units":1,"tool":"scanner","target":"api","findings":[]}' > "$CONSUMER/audit.json"
printf '{"version":1,"exceptions":[]}' > "$CONSUMER/.quality/vulnerabilities.json"
printf '{"version":1,"images":[{"id":"api","reference":"registry.example/api:1"}]}' \
    > "$CONSUMER/.quality/images.json"
printf '{"version":1,"scanned_units":1,"scanned_images":["registry.example/api:1"],"findings":[]}' > "$CONSUMER/image-report.json"
printf '{"version":1,"exceptions":[]}' > "$CONSUMER/.quality/image-cve-exceptions.json"
printf 'FROM python:3.11-slim\n' > "$CONSUMER/Dockerfile"
printf '{"version": 1, "dockerfiles": [{"path": "Dockerfile", "classification": "build"}]}' \
    > "$CONSUMER/.quality/dockerfile-enrollment.json"
printf '{"version":1,"runtimes":[{"image":"python","product":"python","cycle":"major.minor"}],"lifecycles":[{"product":"python","cycle":"3.11","eol":"2027-10-24"}]}' \
    > "$CONSUMER/.quality/base-images.json"
git -C "$CONSUMER" add -A
git -C "$CONSUMER" commit -qm clean

(
    cd "$CONSUMER"
    uv run --isolated --with pre-commit==4.6.0 pre-commit install
)

echo '== packaged pre-commit hooks =='
expect_pass "check-inline-comments allows clean source" check-inline-comments
expect_pass "dict-param-check grandfathers an existing annotation" dict-param-check
expect_pass "check-marker-budget allows the committed marker" check-marker-budget
expect_pass "check-generated-artifact-freshness allows a clean staged artifact" check-generated-artifact-freshness
expect_pass "check-dead-code allows used code" check-dead-code
expect_pass "check-forbidden-mocks allows clean source" check-forbidden-mocks
expect_pass "check-pytest-describe allows valid test nesting" check-pytest-describe
expect_pass "check-hook-scope-contract allows a classified scope contract" check-hook-scope-contract
expect_pass "check-manifest-audit-coverage allows an enrolled manifest" check-manifest-audit-coverage
expect_pass "check-dockerfile-enrollment allows an enrolled Dockerfile" check-dockerfile-enrollment
expect_pass "check-downloaded-asset-enrollment allows an enrolled asset" check-downloaded-asset-enrollment
expect_pass "check-vulnerability-exceptions allows a clean scanner report" check-vulnerability-exceptions
expect_pass "check-container-image-cves allows a clean scanner report" check-container-image-cves
expect_pass "check-base-image-eol allows a supported base image" check-base-image-eol
expect_pass "check-duplication allows distinct source" check-duplication

printf '# NOTE: preserve this fact\nVALUE = 2\n' > "$CONSUMER/marker.py"
git -C "$CONSUMER" add marker.py
expect_pass "check-marker-preservation allows a staged marker-preserving edit" check-marker-preservation

printf 'VALUE = 2\n' > "$CONSUMER/marker.py"
git -C "$CONSUMER" add marker.py
expect_failure "check-marker-preservation rejects a staged marker removal" check-marker-preservation "marker preservation failed:"

printf 'stale payload\n' > "$CONSUMER/input.txt"
git -C "$CONSUMER" add input.txt
expect_failure "check-generated-artifact-freshness rejects a stale staged artifact" check-generated-artifact-freshness "generated/output.txt: staged bytes differ from generated output"

printf 'VALUE = 1  # plain comment\n' > "$CONSUMER/inline.py"
git -C "$CONSUMER" add inline.py
expect_failure "check-inline-comments rejects a plain comment" check-inline-comments "Inline comment detected"

printf 'def public(value: dict) -> int:\n    return 1\n\ndef added(value: dict) -> int:\n    return 1\n' > "$CONSUMER/dict.py"
git -C "$CONSUMER" add dict.py
expect_failure "dict-param-check rejects a new dict signature" dict-param-check "added(value: dict)"

printf '# TODO: remove this marker\n# FIXME: remove this marker too\n' > "$CONSUMER/marker.py"
git -C "$CONSUMER" add marker.py
expect_failure "check-marker-budget rejects an additional marker" check-marker-budget "repo total: 2 marker blocks"

printf 'def used() -> int:\n    return 1\n    unreachable = 2\n\nused()\n' > "$CONSUMER/dead.py"
git -C "$CONSUMER" add dead.py
expect_failure "consumer Vulture configuration cannot narrow dead-code scope" check-dead-code "unreachable code"

printf 'from unittest.mock import Mock\n\ndef test_service():\n    service = Mock()\n    assert service\n' > "$CONSUMER/mocks.py"
git -C "$CONSUMER" add mocks.py
expect_failure "check-forbidden-mocks rejects a mock constructor" check-forbidden-mocks "Mock(...)"

printf 'def test_at_module_level():\n    assert True\n' > "$CONSUMER/test_shape.py"
git -C "$CONSUMER" add test_shape.py
expect_failure "check-pytest-describe rejects a top-level test" check-pytest-describe "top-level test"

printf 'repos:\n  - repo: local\n    hooks:\n      - id: check-hook-scope-contract\n        entry: python hooks/scope-emitter.py\n      - id: unclassified-hook\n        entry: python unclassified.py\n' > "$CONSUMER/scope-contract.yaml"
git -C "$CONSUMER" add scope-contract.yaml
expect_failure "check-hook-scope-contract rejects an unclassified hook" check-hook-scope-contract "UNREGISTERED: unclassified-hook"

mkdir "$CONSUMER/nested"
printf '[project]\nname = "uncovered"\nversion = "0.0.0"\n' > "$CONSUMER/nested/pyproject.toml"
git -C "$CONSUMER" add nested/pyproject.toml
expect_failure "check-manifest-audit-coverage rejects an uncovered manifest" check-manifest-audit-coverage "uncovered manifest: nested/pyproject.toml"

printf 'FROM scratch\n' > "$CONSUMER/Dockerfile.dev"
git -C "$CONSUMER" add Dockerfile.dev
expect_failure "check-dockerfile-enrollment rejects an unclassified Dockerfile" check-dockerfile-enrollment "Dockerfile.dev"

printf 'curl -fsS https://example.test/tool.tar.gz -o tool.tar.gz\ncurl -fsS https://example.test/other.tar.gz -o other.tar.gz\n' > "$CONSUMER/download.sh"
git -C "$CONSUMER" add download.sh
expect_failure "check-downloaded-asset-enrollment rejects an unclassified asset" check-downloaded-asset-enrollment "unclassified asset: download.sh: https://example.test/other.tar.gz"

printf '{"version":1,"scanned_units":1,"tool":"scanner","target":"api","findings":[{"id":"CVE-1","aliases":[],"subject":"pkg@1","blocking":true,"fixes":["2"]}]}' \
    > "$CONSUMER/audit.json"
git -C "$CONSUMER" add audit.json
expect_failure "check-vulnerability-exceptions rejects an unhandled fixable vulnerability" check-vulnerability-exceptions "unhandled vulnerability: CVE-1 (pkg@1)"

printf '{"version":1,"scanned_units":1,"scanned_images":["registry.example/api:1"],"findings":[{"id":"CVE-1","image":"registry.example/api:1","severity":"HIGH","package":"pkg","installed":"1","fixes":["2"]}]}' \
    > "$CONSUMER/image-report.json"
git -C "$CONSUMER" add image-report.json
expect_failure "check-container-image-cves rejects an unhandled fixable CVE" check-container-image-cves "unhandled fixable CVE: CVE-1 (registry.example/api:1)"

printf 'FROM python:3.8-slim\n' > "$CONSUMER/Dockerfile"
printf '{"version":1,"runtimes":[{"image":"python","product":"python","cycle":"major.minor"}],"lifecycles":[{"product":"python","cycle":"3.8","eol":"2024-10-07"}]}' \
    > "$CONSUMER/.quality/base-images.json"
git -C "$CONSUMER" add Dockerfile .quality/base-images.json
expect_failure "check-base-image-eol rejects an end-of-life base image" check-base-image-eol "end-of-life base image: Dockerfile: python:3.8-slim ended 2024-10-07"

printf 'def alpha():\n    value_01 = 1\n    value_02 = 2\n    value_03 = 3\n    value_04 = 4\n    value_05 = 5\n    value_06 = 6\n    value_07 = 7\n    value_08 = 8\n    value_09 = 9\n    value_10 = 10\n    value_11 = 11\n    value_12 = 12\n    value_13 = 13\n    value_14 = 14\n    value_15 = 15\n    return value_01 + value_02 + value_03 + value_04 + value_05 + value_06 + value_07 + value_08 + value_09 + value_10 + value_11 + value_12 + value_13 + value_14 + value_15\n' > "$CONSUMER/clone_a.py"
printf 'def beta():\n    value_01 = 1\n    value_02 = 2\n    value_03 = 3\n    value_04 = 4\n    value_05 = 5\n    value_06 = 6\n    value_07 = 7\n    value_08 = 8\n    value_09 = 9\n    value_10 = 10\n    value_11 = 11\n    value_12 = 12\n    value_13 = 13\n    value_14 = 14\n    value_15 = 15\n    return value_01 + value_02 + value_03 + value_04 + value_05 + value_06 + value_07 + value_08 + value_09 + value_10 + value_11 + value_12 + value_13 + value_14 + value_15\n' > "$CONSUMER/clone_b.py"
git -C "$CONSUMER" add clone_a.py clone_b.py
expect_failure "check-duplication rejects a clone" check-duplication "Top duplication opportunities"
