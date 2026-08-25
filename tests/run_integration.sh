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
printf 'repos:\n  - repo: file://%s\n    rev: %s\n    hooks:\n      - id: check-inline-comments\n        args: [--no-baseline, inline.py]\n      - id: dict-param-check\n        args: [dict.py]\n      - id: check-marker-budget\n      - id: check-duplication\n' \
    "$HOOK_REPOSITORY" "$(git -C "$HOOK_REPOSITORY" rev-parse HEAD)" > "$CONSUMER/.pre-commit-config.yaml"
printf 'VALUE = 1\n' > "$CONSUMER/inline.py"
printf 'def public(value: int) -> int:\n    return value\n' > "$CONSUMER/dict.py"
git -C "$CONSUMER" add -A
git -C "$CONSUMER" commit -qm clean

(
    cd "$CONSUMER"
    uv run --isolated --with pre-commit==4.6.0 pre-commit install
)

echo '== packaged pre-commit hooks =='
expect_pass "check-inline-comments allows clean source" check-inline-comments
expect_pass "dict-param-check allows a typed signature" dict-param-check
expect_pass "check-marker-budget allows no markers" check-marker-budget
expect_pass "check-duplication allows distinct source" check-duplication

printf 'VALUE = 1  # plain comment\n' > "$CONSUMER/inline.py"
git -C "$CONSUMER" add inline.py
expect_failure "check-inline-comments rejects a plain comment" check-inline-comments "Inline comment detected"

printf 'def public(value: dict) -> int:\n    return 1\n' > "$CONSUMER/dict.py"
git -C "$CONSUMER" add dict.py
expect_failure "dict-param-check rejects a dict parameter" dict-param-check "value: dict"

printf '# TODO: remove this marker\n' > "$CONSUMER/marker.py"
git -C "$CONSUMER" add marker.py
expect_failure "check-marker-budget rejects a marker" check-marker-budget "repo total: 1 marker blocks"

printf 'def alpha():\n    value_01 = 1\n    value_02 = 2\n    value_03 = 3\n    value_04 = 4\n    value_05 = 5\n    value_06 = 6\n    value_07 = 7\n    value_08 = 8\n    value_09 = 9\n    value_10 = 10\n    value_11 = 11\n    value_12 = 12\n    value_13 = 13\n    value_14 = 14\n    value_15 = 15\n    return value_01 + value_02 + value_03 + value_04 + value_05 + value_06 + value_07 + value_08 + value_09 + value_10 + value_11 + value_12 + value_13 + value_14 + value_15\n' > "$CONSUMER/clone_a.py"
printf 'def beta():\n    value_01 = 1\n    value_02 = 2\n    value_03 = 3\n    value_04 = 4\n    value_05 = 5\n    value_06 = 6\n    value_07 = 7\n    value_08 = 8\n    value_09 = 9\n    value_10 = 10\n    value_11 = 11\n    value_12 = 12\n    value_13 = 13\n    value_14 = 14\n    value_15 = 15\n    return value_01 + value_02 + value_03 + value_04 + value_05 + value_06 + value_07 + value_08 + value_09 + value_10 + value_11 + value_12 + value_13 + value_14 + value_15\n' > "$CONSUMER/clone_b.py"
git -C "$CONSUMER" add clone_a.py clone_b.py
expect_failure "check-duplication rejects a clone" check-duplication "Top duplication opportunities"
