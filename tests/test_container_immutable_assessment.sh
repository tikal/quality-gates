#!/bin/bash
set -eu

. "$(dirname "$0")/test_helpers.sh"
ROOT="$TEST_PACKAGE_ROOT"
test_setup_repository
TEMPORARY="$TEST_TEMPORARY"
mkdir -p "$TEMPORARY/.quality" "$TEMPORARY/evidence"
printf '%s' '{"version":1,"images":[{"id":"api","reference":"registry.example/api:1"}]}' > "$TEMPORARY/.quality/images.json"
printf '%s' '{"version":1,"dockerfiles":[{"path":"Dockerfile","classification":"build"}],"image_sources":[{"id":"api","dockerfile":"Dockerfile"}]}' > "$TEMPORARY/.quality/container-image-enrollment.json"
printf 'raw scanner output\n' > "$TEMPORARY/evidence/api.json"
git -C "$TEMPORARY" add .quality
git -C "$TEMPORARY" commit -qm policy

enrollment_sha256="$(shasum -a 256 "$TEMPORARY/.quality/container-image-enrollment.json" | cut -d ' ' -f 1)"
raw_sha256="$(shasum -a 256 "$TEMPORARY/evidence/api.json" | cut -d ' ' -f 1)"
printf '%s' "{\"version\":2,\"enrollment_sha256\":\"$enrollment_sha256\",\"scans\":[{\"image_id\":\"api\",\"artifact_digest\":\"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"scanned_at\":\"2026-08-28T11:00:00Z\",\"raw_evidence\":{\"path\":\"evidence/api.json\",\"sha256\":\"$raw_sha256\"}}],\"findings\":[]}" > "$TEMPORARY/assessment.json"
printf '%s' '{"version":2,"exceptions":[]}' > "$TEMPORARY/.quality/container-image-exceptions.json"
git -C "$TEMPORARY" add .quality/container-image-exceptions.json

expect() {
    local label="$1" wanted="$2"; shift 2
    local output status
    if output="$(PYTHONPATH="$ROOT/src" python -m quality_gates.container_immutable_assessment "$@" 2>&1)"; then
        status=0
    else
        status=$?
    fi
    if [ "$status" != "$wanted" ]; then
        printf 'FAIL: %s: exit %s, wanted %s\n%s\n' "$label" "$status" "$wanted" "$output" >&2
        exit 1
    fi
    printf 'ok %s\n' "$label"
}

assessment() {
    printf '%s' "$1" > "$TEMPORARY/assessment.json"
}

run_assessment() {
    expect "$1" "$2" --root "$TEMPORARY" --enrollment .quality/container-image-enrollment.json \
        --inventory .quality/images.json --report assessment.json --exceptions .quality/container-image-exceptions.json \
        --as-of 2026-08-28T12:00:00Z --max-age-hours 2
}

run_assessment 'a fresh immutable assessment succeeds' 0

assessment "{\"version\":2,\"enrollment_sha256\":\"$enrollment_sha256\",\"scans\":[{\"image_id\":\"api\",\"artifact_digest\":\"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"scanned_at\":\"2026-08-28T11:00:00Z\",\"raw_evidence\":{\"path\":\"evidence/api.json\",\"sha256\":\"$raw_sha256\"}}],\"findings\":[{\"id\":\"CVE-1\",\"image_id\":\"api\",\"artifact_digest\":\"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"severity\":\"HIGH\",\"package\":\"pkg\",\"installed\":\"1\",\"fixes\":[\"2\"]}]}"
run_assessment 'an unhandled fixable high finding fails' 1

printf '%s' '{"version":2,"exceptions":[{"id":"CVE-1","image_id":"api","artifact_digest":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","rationale":"Reviewed."}]}' > "$TEMPORARY/.quality/container-image-exceptions.json"
git -C "$TEMPORARY" add .quality/container-image-exceptions.json
run_assessment 'an exception for a fixable finding fails' 1

assessment "{\"version\":2,\"enrollment_sha256\":\"$enrollment_sha256\",\"scans\":[{\"image_id\":\"api\",\"artifact_digest\":\"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"scanned_at\":\"2026-08-28T11:00:00Z\",\"raw_evidence\":{\"path\":\"evidence/api.json\",\"sha256\":\"$raw_sha256\"}}],\"findings\":[]}"
run_assessment 'a stale exception fails' 1

printf 'ok immutable container assessment validation\n'
