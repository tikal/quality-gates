#!/bin/bash
# Every gate must be able to FAIL. Each case builds a tree, runs the gate, and
# asserts the exit code and the message.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PASS=0
FAIL=0

expect() {
    local label="$1" want="$2"; shift 2
    local out rc
    out="$("$@" 2>&1)"; rc=$?
    if [ "$rc" = "$want" ]; then
        PASS=$((PASS + 1)); echo "  ok $label"
    else
        FAIL=$((FAIL + 1)); echo "  FAIL $label: exit $rc, wanted $want"; echo "$out" | sed 's/^/      /'
    fi
}

expect_says() {
    local label="$1" needle="$2"; shift 2
    local out
    out="$("$@" 2>&1)"
    if echo "$out" | grep -q -- "$needle"; then
        PASS=$((PASS + 1)); echo "  ok $label"
    else
        FAIL=$((FAIL + 1)); echo "  FAIL $label: output did not mention '$needle'"; echo "$out" | sed 's/^/      /'
    fi
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cd "$TMP" || exit 1
git init -q . && git config user.email t@t && git config user.name t

echo "== marker budget =="
mkdir -p pkg
printf 'x = 1\n# TODO: one\ny = 2\n# NOTE: two\n' > pkg/a.py
git add -A && git commit -qm base
expect "under the ceiling passes" 0 check-marker-budget --ceiling 5
expect "over the ceiling fails" 1 check-marker-budget --ceiling 1
expect_says "the failure names the count" "2" check-marker-budget --ceiling 1
expect "a per-file budget can fail alone" 1 check-marker-budget --ceiling 99 --per-file pkg/a.py=1
expect "a per-file budget can pass" 0 check-marker-budget --ceiling 99 --per-file pkg/a.py=2
expect "an untracked file is not counted" 0 check-marker-budget --ceiling 2

echo "== inline comments =="
printf 'def f():\n    x = 1  # explain\n    return x\n' > pkg/b.py
git add -A && git commit -qm b
expect "a plain inline comment fails" 1 check-inline-comments --baseline missing.txt pkg
expect_says "the failure names the file" "pkg/b.py" check-inline-comments --baseline missing.txt pkg
check-inline-comments --baseline base.txt --update-baseline pkg >/dev/null 2>&1
expect "a written baseline grandfathers it" 0 check-inline-comments --baseline base.txt pkg
printf 'def g():\n    y = 2  # a new one\n    return y\n' > pkg/c.py
expect "a NEW comment still fails against a baseline" 1 check-inline-comments --baseline base.txt pkg
printf 'def _h():\n    """Private docstring."""\n    return 1\n' > pkg/d.py
expect "a private docstring fails" 1 check-inline-comments --baseline base.txt pkg/d.py
printf '# NOTE: a marker is allowed\nz = 3\n' > pkg/e.py
expect "a marker comment is allowed" 0 check-inline-comments --baseline base.txt pkg/e.py

echo "== dict params =="
printf 'def pub(x: dict) -> int:\n    return 1\n' > pkg/f.py
expect "a public dict parameter fails" 1 dict-param-check pkg/f.py
printf 'def pub(x: dict) -> int:  # ALLOW: dict-param\n    return 1\n' > pkg/g.py
expect "an ALLOW badge suppresses it" 0 dict-param-check pkg/g.py
printf 'def _priv(x: dict) -> int:\n    return 1\n' > pkg/h.py
expect "a private function is not checked" 0 dict-param-check pkg/h.py
printf 'def pub2(x: int) -> dict:\n    return {}\n' > pkg/i.py
expect "a dict return type fails" 1 dict-param-check pkg/i.py

echo
echo "==== PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = 0 ]
