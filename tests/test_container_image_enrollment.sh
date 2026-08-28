#!/bin/bash
set -eu

. "$(dirname "$0")/test_helpers.sh"
ROOT="$TEST_PACKAGE_ROOT"
test_setup_repository
TEMPORARY="$TEST_TEMPORARY"
mkdir -p "$TEMPORARY/.quality"
printf 'FROM scratch\n' > "$TEMPORARY/Dockerfile.api"
printf 'FROM scratch\n' > "$TEMPORARY/Dockerfile.worker"
printf 'FROM scratch\n' > "$TEMPORARY/Dockerfile.dev"
printf '%s' '{"version":1,"images":[{"id":"api","reference":"registry.example/api:1"},{"id":"worker","reference":"registry.example/worker:1"},{"id":"docs","reference":"registry.example/docs:1"}]}' > "$TEMPORARY/.quality/images.json"
git -C "$TEMPORARY" add .
git -C "$TEMPORARY" commit -qm fixture

write_ledger() {
    printf '%s' "$1" > "$TEMPORARY/.quality/container-image-enrollment.json"
    git -C "$TEMPORARY" add .quality/container-image-enrollment.json
}

expect() {
    local label="$1" wanted="$2"; shift 2
    local output status
    if output="$(PYTHONPATH="$ROOT/src" python -m quality_gates.container_image_enrollment "$@" 2>&1)"; then
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

clean='{"version":1,"dockerfiles":[{"path":"Dockerfile.api","classification":"build"},{"path":"Dockerfile.worker","classification":"pull"},{"path":"Dockerfile.dev","classification":"ignore","rationale":"Development-only image."}],"image_sources":[{"id":"api","dockerfile":"Dockerfile.api"},{"id":"worker","dockerfile":"Dockerfile.worker"},{"id":"docs","external":"registry.example/docs:1","rationale":"Published by the documentation service."}]}'
write_ledger "$clean"
expect 'complete Dockerfile and inventory coverage succeeds' 0 --root "$TEMPORARY" --ledger .quality/container-image-enrollment.json --inventory .quality/images.json

write_ledger '{"version":1,"dockerfiles":[{"path":"Dockerfile.api","classification":"build"},{"path":"Dockerfile.worker","classification":"pull"}],"image_sources":[{"id":"api","dockerfile":"Dockerfile.api"},{"id":"worker","dockerfile":"Dockerfile.worker"},{"id":"docs","external":"registry.example/docs:1","rationale":"Published externally."}]}'
expect 'an unclassified tracked Dockerfile fails' 1 --root "$TEMPORARY" --ledger .quality/container-image-enrollment.json --inventory .quality/images.json

write_ledger '{"version":1,"dockerfiles":[{"path":"Dockerfile.api","classification":"build"},{"path":"Dockerfile.worker","classification":"pull"},{"path":"Dockerfile.dev","classification":"ignore","rationale":"Development-only image."},{"path":"Dockerfile.stale","classification":"build"}],"image_sources":[{"id":"api","dockerfile":"Dockerfile.api"},{"id":"worker","dockerfile":"Dockerfile.worker"},{"id":"docs","external":"registry.example/docs:1","rationale":"Published externally."}]}'
expect 'a stale Dockerfile classification fails' 1 --root "$TEMPORARY" --ledger .quality/container-image-enrollment.json --inventory .quality/images.json

write_ledger '{"version":1,"dockerfiles":[{"path":"Dockerfile.api","classification":"build"},{"path":"Dockerfile.api","classification":"pull"},{"path":"Dockerfile.worker","classification":"pull"},{"path":"Dockerfile.dev","classification":"ignore","rationale":"Development-only image."}],"image_sources":[{"id":"api","dockerfile":"Dockerfile.api"},{"id":"worker","dockerfile":"Dockerfile.worker"},{"id":"docs","external":"registry.example/docs:1","rationale":"Published externally."}]}'
expect 'a duplicate Dockerfile classification fails' 1 --root "$TEMPORARY" --ledger .quality/container-image-enrollment.json --inventory .quality/images.json

write_ledger '{"version":1,"dockerfiles":[{"path":"Dockerfile.api","classification":"build"},{"path":"Dockerfile.worker","classification":"pull"},{"path":"Dockerfile.dev","classification":"ignore","rationale":"Development-only image."}],"image_sources":[{"id":"api","dockerfile":"Dockerfile.api"},{"id":"worker","dockerfile":"Dockerfile.worker"},{"id":"docs","external":"registry.example/docs:1","rationale":"Published externally."},{"id":"docs","external":"registry.example/docs:2","rationale":"Also published externally."}]}'
expect 'a duplicate inventory mapping fails' 1 --root "$TEMPORARY" --ledger .quality/container-image-enrollment.json --inventory .quality/images.json

write_ledger '{"version":1,"dockerfiles":[{"path":"Dockerfile.api","classification":"build"},{"path":"Dockerfile.worker","classification":"pull"},{"path":"Dockerfile.dev","classification":"ignore","rationale":"Development-only image."}],"image_sources":[{"id":"api","dockerfile":"Dockerfile.api"},{"id":"worker","dockerfile":"Dockerfile.worker"}]}'
expect 'a missing inventory mapping fails' 1 --root "$TEMPORARY" --ledger .quality/container-image-enrollment.json --inventory .quality/images.json

write_ledger '{"version":1,"dockerfiles":[{"path":"Dockerfile.api","classification":"build"},{"path":"Dockerfile.worker","classification":"pull"},{"path":"Dockerfile.dev","classification":"ignore","rationale":"Development-only image."}],"image_sources":[{"id":"api","external":"registry.example/api:1","rationale":"Published externally."},{"id":"worker","dockerfile":"Dockerfile.worker"},{"id":"docs","external":"registry.example/docs:1","rationale":"Published externally."}]}'
expect 'a non-ignored Dockerfile without an image edge fails' 1 --root "$TEMPORARY" --ledger .quality/container-image-enrollment.json --inventory .quality/images.json

write_ledger '{"version":1,"dockerfiles":[{"path":"Dockerfile.api","classification":"build"},{"path":"Dockerfile.worker","classification":"pull"},{"path":"Dockerfile.dev","classification":"ignore","rationale":"Development-only image."}],"image_sources":[{"id":"api","dockerfile":"Dockerfile.api"},{"id":"worker","dockerfile":"Dockerfile.worker"},{"id":"docs","external":"registry.example/docs:1","rationale":"Published externally."},{"id":"orphan","external":"registry.example/orphan:1","rationale":"Published externally."}]}'
expect 'an orphan inventory mapping fails' 1 --root "$TEMPORARY" --ledger .quality/container-image-enrollment.json --inventory .quality/images.json

write_ledger '{"version":1,"dockerfiles":[{"path":"Dockerfile.api","classification":"build"},{"path":"Dockerfile.worker","classification":"pull"},{"path":"Dockerfile.dev","classification":"ignore","rationale":"Development-only image."}],"image_sources":[{"id":"api","dockerfile":"Dockerfile.api"},{"id":"worker","dockerfile":"Dockerfile.worker"},{"id":"docs","dockerfile":"Dockerfile.dev"}]}'
expect 'an image mapped to an ignored Dockerfile fails' 1 --root "$TEMPORARY" --ledger .quality/container-image-enrollment.json --inventory .quality/images.json

write_ledger '{"version":1,"dockerfiles":[{"path":"Dockerfile.api","classification":"build"},{"path":"Dockerfile.worker","classification":"pull"},{"path":"Dockerfile.dev","classification":"ignore","rationale":"Development-only image."}],"image_sources":[{"id":"api","dockerfile":"Dockerfile.api"},{"id":"worker","dockerfile":"Dockerfile.worker"},{"id":"docs","dockerfile":"Dockerfile.unknown"}]}'
expect 'an image mapped to an unknown Dockerfile fails' 1 --root "$TEMPORARY" --ledger .quality/container-image-enrollment.json --inventory .quality/images.json

write_ledger '{"version":1,"dockerfiles":[{"path":"Dockerfile.api","classification":"build"},{"path":"Dockerfile.worker","classification":"pull"},{"path":"Dockerfile.dev","classification":"ignore","rationale":"Development-only image."}],"image_sources":[{"id":"api","dockerfile":"Dockerfile.api"},{"id":"worker","dockerfile":"Dockerfile.worker"},{"id":"docs","external":"registry.example/docs:1"}]}'
expect 'an external image without rationale fails' 1 --root "$TEMPORARY" --ledger .quality/container-image-enrollment.json --inventory .quality/images.json

write_ledger '{"version":1,"dockerfiles":[{"path":"Dockerfile.api","classification":"build"},{"path":"Dockerfile.worker","classification":"pull"},{"path":"Dockerfile.dev","classification":"ignore","rationale":"Development-only image."}],"image_sources":[{"id":"api","dockerfile":"Dockerfile.api"},{"id":"worker","dockerfile":"Dockerfile.worker"},{"id":"docs","external":"registry.example/docs:1","rationale":"Published externally."}],"extra":true}'
expect 'an unknown ledger field fails' 1 --root "$TEMPORARY" --ledger .quality/container-image-enrollment.json --inventory .quality/images.json

write_ledger '{"version":1,"version":1,"dockerfiles":[],"image_sources":[]}'
expect 'duplicate JSON keys fail' 1 --root "$TEMPORARY" --ledger .quality/container-image-enrollment.json --inventory .quality/images.json

printf 'ok container image enrollment graph validation\n'
