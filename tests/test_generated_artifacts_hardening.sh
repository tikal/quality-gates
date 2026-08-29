#!/bin/bash
set -u

PASS=0
FAIL=0
. "$(dirname "$0")/test_helpers.sh"
ROOT="$TEST_PACKAGE_ROOT"
test_setup_repository
WORK="$TEST_TEMPORARY"

expect() {
    local label="$1" want="$2"; shift 2
    local out rc
    out="$("$@" 2>&1)"; rc=$?
    if [ "$rc" = "$want" ]; then
        PASS=$((PASS + 1)); printf 'ok %s\n' "$label"
    else
        FAIL=$((FAIL + 1)); printf 'FAIL %s: exit %s, wanted %s\n%s\n' "$label" "$rc" "$want" "$out"
    fi
}

expect_says() {
    local label="$1" needle="$2" want="$3"; shift 3
    local out rc
    out="$("$@" 2>&1)"; rc=$?
    if [ "$rc" = "$want" ] && [[ "$out" == *"$needle"* ]]; then
        PASS=$((PASS + 1)); printf 'ok %s\n' "$label"
    else
        FAIL=$((FAIL + 1)); printf 'FAIL %s: exit %s, wanted %s, missing %s\n%s\n' "$label" "$rc" "$want" "$needle" "$out"
    fi
}

printf 'payload\n' > "$WORK/input.txt"
printf 'payload\n' > "$WORK/output.txt"
mkdir "$WORK/tools"
printf '%s\n' '#!/bin/sh' \
    'test "${REQUIRED_ENV-}" = expected || exit 12' \
    'printf "payload\\n" > "$QUALITY_GATES_OUTPUT_DIR/output.txt"' \
    'if [ "${MODE-}" = executable ]; then chmod 755 "$QUALITY_GATES_OUTPUT_DIR/output.txt"; fi' \
    'if [ "${EXTRA_OUTPUT-}" = yes ]; then printf extra > "$QUALITY_GATES_OUTPUT_DIR/extra.txt"; fi' \
    'if [ "${OUTPUT_SYMLINK-}" = yes ]; then rm "$QUALITY_GATES_OUTPUT_DIR/output.txt"; ln -s "$PWD/input.txt" "$QUALITY_GATES_OUTPUT_DIR/output.txt"; fi' \
    > "$WORK/tools/generate"
chmod +x "$WORK/tools/generate"
git -C "$WORK" add .
git -C "$WORK" commit -qm initial

gate() {
    PYTHONPATH="$ROOT/src" python -m quality_gates.generated_artifacts --root "$WORK" --artifact output.txt "$@"
}

export REQUIRED_ENV=expected
expect "a staged executable generator remains executable in its snapshot" 0 \
    gate -- ./tools/generate
printf 'working tree is stale\n' > "$WORK/output.txt"
expect "strict generation reads the staged snapshot rather than the working tree" 0 \
    gate --clear-env --inherit-env REQUIRED_ENV --reject-extra-outputs -- ./tools/generate
expect_says "a bounded timeout terminates the generator" "generator timed out" 1 \
    gate --timeout-seconds 1 -- /bin/sh -c 'sleep 2'
expect "a nonpositive timeout is rejected" 2 \
    gate --timeout-seconds 0 -- ./tools/generate
expect "a timeout above the bounded maximum is rejected" 2 \
    gate --timeout-seconds 301 -- ./tools/generate
expect_says "a generated executable mode differs from staged mode" "staged mode differs" 1 \
    gate --env MODE=executable -- ./tools/generate
expect_says "a generated artifact symlink is rejected" "must not traverse a symlink" 1 \
    gate --env OUTPUT_SYMLINK=yes -- ./tools/generate
expect "extra generated outputs are accepted by default" 0 \
    gate --env EXTRA_OUTPUT=yes -- ./tools/generate
expect_says "extra generated outputs can be rejected explicitly" "extra generated output" 1 \
    gate --env EXTRA_OUTPUT=yes --reject-extra-outputs -- ./tools/generate
expect_says "a clear environment removes inherited variables" "generator failed" 1 \
    gate --clear-env -- ./tools/generate
expect "an explicit environment variable is available with a clear environment" 0 \
    gate --clear-env --env REQUIRED_ENV=expected -- ./tools/generate
expect "an inherited named variable is available with a clear environment" 0 \
    gate --clear-env --inherit-env REQUIRED_ENV -- ./tools/generate

printf '%s passed, %s failed\n' "$PASS" "$FAIL"
test "$FAIL" = 0
