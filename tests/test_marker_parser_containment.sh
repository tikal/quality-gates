#!/bin/bash
set -eu

. "$(dirname "$0")/test_helpers.sh"
ROOT="$TEST_PACKAGE_ROOT"
test_setup_repository
TEMPORARY="$TEST_TEMPORARY"
printf '// NOTE: preserve this fact\nconst value = 1;\n' > "$TEMPORARY/source.js"
git -C "$TEMPORARY" add source.js
git -C "$TEMPORARY" commit -qm fixture

PYTHONPATH="$ROOT/src" TEMPORARY="$TEMPORARY" python "$ROOT/tests/test_marker_parser_containment.py"
