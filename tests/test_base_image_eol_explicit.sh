#!/bin/bash
set -eu

. "$(dirname "$0")/test_helpers.sh"
ROOT="$TEST_PACKAGE_ROOT"
test_setup_repository
TEMPORARY="$TEST_TEMPORARY"
mkdir -p "$TEMPORARY/.quality"
printf 'FROM python:3.11-slim\n' > "$TEMPORARY/Dockerfile"
printf '%s' '{"version":1,"runtimes":[{"image":"python","product":"python","cycle":"major.minor"}],"lifecycles":[{"product":"python","cycle":"3.11","eol":"2027-10-24"}]}' > "$TEMPORARY/.quality/base-images.json"
git -C "$TEMPORARY" add .
git -C "$TEMPORARY" commit -qm fixture

if output="$(PATH="$ROOT/.venv/bin:$PATH" check-base-image-eol --root "$TEMPORARY" --policy .quality/base-images.json 2>&1)"; then
    printf 'FAIL: base-image EOL accepted an implicit assessment date\n%s\n' "$output" >&2
    exit 1
elif [[ "$output" != *"--as-of"* ]]; then
    printf 'FAIL: base-image EOL did not require --as-of\n%s\n' "$output" >&2
    exit 1
fi

printf 'ok explicit base-image assessment time is required\n'
