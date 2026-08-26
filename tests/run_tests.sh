#!/bin/bash
# Every gate must be able to FAIL. Each case builds a tree, runs the gate, and
# asserts the exit code and the message.
set -u
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

expect_unreadable_marker() {
    local label="$1" path="$2"
    expect "$label fails the marker budget" 1 check-marker-budget --ceiling 99
    expect_says "$label names the unreadable file" "$path" check-marker-budget --ceiling 99
    git rm -q --cached "$path"
    rm "$path"
}

expect_invalid_shrink_baseline() {
    local label="$1" baseline="$2" source="$3"
    cp "$baseline" "$baseline.before"
    expect "$label is rejected" 1 check-inline-comments --baseline "$baseline" --shrink-baseline "$source"
    expect "$label is not rewritten" 0 cmp "$baseline" "$baseline.before"
    rm "$baseline.before"
}

TMP="$(mktemp -d)"
OTHER="$(mktemp -d)"
TOOLCHAIN="$(mktemp -d)"
MARKER_SKIPS="$(mktemp -d)"
DEAD_CODE_BIN="$(mktemp -d)"
DEAD_CODE_SOURCE="$(mktemp -d)"
trap 'rm -rf "$TMP" "$OTHER" "$TOOLCHAIN" "$MARKER_SKIPS" "$DEAD_CODE_BIN" "$DEAD_CODE_SOURCE"' EXIT
PACKAGE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

expect "the Tree-sitter core support cap is declared" 0 python -c \
    'import tomllib; assert "tree-sitter>=0.25,<0.27" in tomllib.load(open("pyproject.toml", "rb"))["project"]["dependencies"]'

npm install --prefix "$TOOLCHAIN" --cache "$TOOLCHAIN/cache" --no-save "$PACKAGE_ROOT" jscpd@5.0.16 >/dev/null 2>&1 || exit 1
PATH="$TOOLCHAIN/node_modules/.bin:$PATH"

mkdir -p "$OTHER/pkg"
printf '# TODO: a marker in another repository\n' > "$OTHER/pkg/a.py"
git -C "$OTHER" init -q .
git -C "$OTHER" config user.email t@t
git -C "$OTHER" config user.name t
git -C "$OTHER" add -A && git -C "$OTHER" commit -qm other

mkdir -p "$MARKER_SKIPS/pkg"
printf 'CLEAN = 1\n' > "$MARKER_SKIPS/pkg/clean.py"
for directory in venv .venv node_modules __pycache__ _generated; do
    mkdir -p "$MARKER_SKIPS/ignored/$directory"
    printf '# TODO: marker in a skipped directory\n' > "$MARKER_SKIPS/ignored/$directory/dependency.py"
done
git -C "$MARKER_SKIPS" init -q .
git -C "$MARKER_SKIPS" config user.email t@t
git -C "$MARKER_SKIPS" config user.name t
git -C "$MARKER_SKIPS" add -f . && git -C "$MARKER_SKIPS" commit -qm skipped-directories

MARKER_SKIPPED_ROOT="$MARKER_SKIPS/venv/repository"
mkdir -p "$MARKER_SKIPPED_ROOT/pkg"
printf '# TODO: marker in a skipped-root repository\n' > "$MARKER_SKIPPED_ROOT/pkg/marker.py"
git -C "$MARKER_SKIPPED_ROOT" init -q .
git -C "$MARKER_SKIPPED_ROOT" config user.email t@t
git -C "$MARKER_SKIPPED_ROOT" config user.name t
git -C "$MARKER_SKIPPED_ROOT" add -A && git -C "$MARKER_SKIPPED_ROOT" commit -qm skipped-root

cd "$TMP" || exit 1
git init -q . && git config user.email t@t && git config user.name t

printf '#!/bin/sh\nif [ -n "$VULTURE_OPTIONS" ]; then\n    if [ "$1" != "--config" ] || [ ! -f "$2" ]; then\n        exit 1\n    fi\n    shift 2\n    if [ "$*" != "$VULTURE_OPTIONS" ]; then\n        exit 1\n    fi\nfi\nfor argument in "$@"; do\n    case "$argument" in\n        *dead.py) printf "dead.py:1: unused function\\n"; exit 3 ;;\n    esac\ndone\nexit 0\n' > "$DEAD_CODE_BIN/vulture"
chmod +x "$DEAD_CODE_BIN/vulture"

echo "== dead code =="
mkdir -p "$DEAD_CODE_SOURCE/empty" "$DEAD_CODE_SOURCE/generated" "$DEAD_CODE_SOURCE/mixed/generated" \
    "$DEAD_CODE_SOURCE/hidden-only/.private" "$DEAD_CODE_SOURCE/hidden-real/.private"
printf 'def unused():\n    return 1\n' > "$DEAD_CODE_SOURCE/dead.py"
printf 'VALUE = 1\n' > "$DEAD_CODE_SOURCE/clean.py"
printf 'OTHER = 2\n' > "$DEAD_CODE_SOURCE/clean-two.py"
printf 'GENERATED = 3\n' > "$DEAD_CODE_SOURCE/generated/code.py"
printf 'VISIBLE = 4\n' > "$DEAD_CODE_SOURCE/mixed/visible.py"
printf 'GENERATED = 5\n' > "$DEAD_CODE_SOURCE/mixed/generated/code.py"
printf 'HIDDEN = 6\n' > "$DEAD_CODE_SOURCE/hidden-only/.hidden.py"
printf 'PRIVATE = 7\n' > "$DEAD_CODE_SOURCE/hidden-only/.private/code.py"
printf 'def used() -> int:\n    return 1\n\nused()\n' > "$DEAD_CODE_SOURCE/hidden-real/.private/clean.py"
expect "dead code is reported" 1 env PATH="$DEAD_CODE_BIN:$PATH" python -m quality_gates.dead_code \
    --path "$DEAD_CODE_SOURCE/dead.py" --min-confidence 80
expect_says "a dead-code report names its source" "dead.py" env PATH="$DEAD_CODE_BIN:$PATH" \
    python -m quality_gates.dead_code --path "$DEAD_CODE_SOURCE/dead.py"
expect "dead-code options are forwarded" 0 env PATH="$DEAD_CODE_BIN:$PATH" \
    VULTURE_OPTIONS="$DEAD_CODE_SOURCE/clean.py $DEAD_CODE_SOURCE/clean-two.py --min-confidence 80 --sort-by-size --ignore-names unused --exclude */ignored.py" \
    python -m quality_gates.dead_code --path "$DEAD_CODE_SOURCE/clean.py" --path "$DEAD_CODE_SOURCE/clean-two.py" \
    --ignore-names unused --exclude '*/ignored.py'
expect "a bare exclusion is normalized for Vulture" 0 env PATH="$DEAD_CODE_BIN:$PATH" \
    VULTURE_OPTIONS="$DEAD_CODE_SOURCE/mixed --min-confidence 80 --sort-by-size --exclude *generated*" \
    python -m quality_gates.dead_code --path "$DEAD_CODE_SOURCE/mixed" --exclude generated
expect_says "a bare exclusion keeps visible mixed source in scope" "scope=1" env PATH="$DEAD_CODE_BIN:$PATH" \
    python -m quality_gates.dead_code --path "$DEAD_CODE_SOURCE/mixed" --exclude generated
expect "zero minimum confidence is accepted" 0 env PATH="$DEAD_CODE_BIN:$PATH" python -m quality_gates.dead_code \
    --path "$DEAD_CODE_SOURCE/clean.py" --min-confidence 0
expect "maximum confidence is accepted" 0 env PATH="$DEAD_CODE_BIN:$PATH" python -m quality_gates.dead_code \
    --path "$DEAD_CODE_SOURCE/clean.py" --min-confidence 100
expect "a negative minimum confidence is rejected" 2 env PATH="$DEAD_CODE_BIN:$PATH" python -m quality_gates.dead_code \
    --path "$DEAD_CODE_SOURCE/clean.py" --min-confidence -1
expect "an over-maximum confidence is rejected" 2 env PATH="$DEAD_CODE_BIN:$PATH" python -m quality_gates.dead_code \
    --path "$DEAD_CODE_SOURCE/clean.py" --min-confidence 101
expect_says "a clean dead-code scan reports its scope" "scope=2" env PATH="$DEAD_CODE_BIN:$PATH" \
    python -m quality_gates.dead_code --path "$DEAD_CODE_SOURCE/clean.py" --path "$DEAD_CODE_SOURCE/clean-two.py"
expect "a missing dead-code path fails" 2 env PATH="$DEAD_CODE_BIN:$PATH" python -m quality_gates.dead_code \
    --path "$DEAD_CODE_SOURCE/missing.py"
expect_says "a missing dead-code path is named" "$DEAD_CODE_SOURCE/missing.py" env PATH="$DEAD_CODE_BIN:$PATH" \
    python -m quality_gates.dead_code --path "$DEAD_CODE_SOURCE/missing.py"
expect "a zero-file dead-code scope fails" 1 env PATH="$DEAD_CODE_BIN:$PATH" python -m quality_gates.dead_code \
    --path "$DEAD_CODE_SOURCE/empty"
expect "a bare exclusion that leaves no source fails" 1 env PATH="$DEAD_CODE_BIN:$PATH" python -m quality_gates.dead_code \
    --path "$DEAD_CODE_SOURCE/generated" --exclude generated
expect "a hidden-only source path is scanned" 0 env PATH="$DEAD_CODE_BIN:$PATH" python -m quality_gates.dead_code \
    --path "$DEAD_CODE_SOURCE/hidden-only"
expect "Vulture scans a hidden-only directory" 0 env PYTHONPATH="$PACKAGE_ROOT/src" \
    uv run --isolated --no-project --with vulture==2.14.0 python -m quality_gates.dead_code \
    --path "$DEAD_CODE_SOURCE/hidden-real"
expect_says "a hidden Vulture source is counted" "scope=1" env PYTHONPATH="$PACKAGE_ROOT/src" \
    uv run --isolated --no-project --with vulture==2.14.0 python -m quality_gates.dead_code \
    --path "$DEAD_CODE_SOURCE/hidden-real"
expect "an all-excluded dead-code scope fails" 1 env PATH="$DEAD_CODE_BIN:$PATH" python -m quality_gates.dead_code \
    --path "$DEAD_CODE_SOURCE/clean.py" --exclude '*/clean.py'
expect "dead-code requires a path" 2 env PATH="$DEAD_CODE_BIN:$PATH" python -m quality_gates.dead_code

echo "== marker budget =="
mkdir -p pkg
printf 'x = 1\n# TODO: one\ny = 2\n# NOTE: two\n' > pkg/a.py
git add -A && git commit -qm base
expect "under the ceiling passes" 0 check-marker-budget --ceiling 5
expect "over the ceiling fails" 1 check-marker-budget --ceiling 1
expect_says "the failure names the count" "2" check-marker-budget --ceiling 1
expect "a per-file budget can fail alone" 1 check-marker-budget --ceiling 99 --per-file pkg/a.py=1
expect "a per-file budget can pass" 0 check-marker-budget --ceiling 99 --per-file pkg/a.py=2

expect "default skipped directories do not affect the marker budget" 0 \
    check-marker-budget --root "$MARKER_SKIPS" --ceiling 0
expect_says "default skipped directories do not affect marker scope" "scope=1" \
    check-marker-budget --root "$MARKER_SKIPS" --ceiling 0
expect "a repository under a skipped directory fails the zero-file scan" 1 \
    check-marker-budget --root "$MARKER_SKIPPED_ROOT" --ceiling 0
expect_says "a repository under a skipped directory has zero marker scope" "scanned 0 files" \
    check-marker-budget --root "$MARKER_SKIPPED_ROOT" --ceiling 0

printf '# TODO: untracked and therefore uncounted\nz = 1\n' > pkg/untracked.py
expect "an untracked file is not counted" 0 check-marker-budget --ceiling 2
rm pkg/untracked.py

printf 'BAD = """\n# TODO: marker text inside a string literal\n"""\nGOOD = 1\n' > pkg/lit.py
printf 'def doc():\n    """Doc.\n\n    * NOTE: a bullet, not a marker\n    """\n    return 1\n' > pkg/bullet.py
printf 'const marker = "// TODO: marker text inside a string literal";\n' > pkg/lit.ts
printf 'const marker = "// TODO: marker text inside a string literal";\n' > pkg/lit.tsx
printf 'const marker = "// TODO: marker text inside a string literal";\n' > pkg/lit.js
printf 'const marker = "// TODO: marker text inside a string literal";\n' > pkg/lit.jsx
printf 'const marker = `// TODO: marker text inside a string literal`;\n' > pkg/lit.mjs
printf 'const marker = `// TODO: marker text inside a string literal`\n' > pkg/lit.go
printf "marker='# TODO: marker text inside a string literal'\n" > pkg/lit.sh
git add -A && git commit -qm markers
expect "marker text in a string literal is not counted" 0 check-marker-budget --ceiling 2
expect "a marker bullet in a docstring is not counted" 0 check-marker-budget --ceiling 99 --per-file pkg/bullet.py=0
expect "a TypeScript string literal is not counted" 0 check-marker-budget --ceiling 99 --per-file pkg/lit.ts=0
expect "a TSX string literal is not counted" 0 check-marker-budget --ceiling 99 --per-file pkg/lit.tsx=0
expect "a JavaScript string literal is not counted" 0 check-marker-budget --ceiling 99 --per-file pkg/lit.js=0
expect "a JSX string literal is not counted" 0 check-marker-budget --ceiling 99 --per-file pkg/lit.jsx=0
expect "an MJS template literal is not counted" 0 check-marker-budget --ceiling 99 --per-file pkg/lit.mjs=0
expect "a Go raw string literal is not counted" 0 check-marker-budget --ceiling 99 --per-file pkg/lit.go=0
expect "a shell string literal is not counted" 0 check-marker-budget --ceiling 99 --per-file pkg/lit.sh=0
expect_says "GIT_DIR does not redirect the scan" "scope=10" \
    env GIT_DIR="$OTHER/.git" GIT_INDEX_FILE="$OTHER/.git/index" check-marker-budget --ceiling 5

printf 'const value = `// TODO: template data ${/* TODO: a real template substitution comment */ value} // TODO: template data`;\n' > pkg/template.js
printf 'printf foo# TODO\n' > pkg/shell-token.sh
printf 'const expression = /[/* TODO]/;\n' > pkg/regex.js
printf '<div>// TODO: JSX text</div>;\n' > pkg/jsx-text.jsx
printf '<div title={/* TODO: an attribute expression comment */ value} />;\n' > pkg/jsx-attribute.jsx
printf 'function expression() { return /[/* TODO]/; }\n' > pkg/regex-keyword.js
printf 'const value = 1; /* context\n * TODO: preserved block marker\n */\n' > pkg/block.js
printf '((value << 1))\n# TODO: real comment after arithmetic shift\n' > pkg/arithmetic.sh
printf 'value="$(# TODO: a command substitution comment\nprintf ok\n)"\n' > pkg/shell-substitution.sh
printf 'value="`# TODO: a backtick substitution comment\nprintf ok\n`"\n' > pkg/shell-backticks.sh
printf '// TODO: a TypeScript comment\n' > pkg/comment.ts
printf '// TODO: a TSX comment\n' > pkg/comment.tsx
printf '// TODO: a JavaScript comment\n' > pkg/comment.js
printf '// TODO: a JSX comment\n' > pkg/comment.jsx
printf '// TODO: an MJS comment\n' > pkg/comment.mjs
printf '// TODO: a Go comment\n' > pkg/comment.go
printf '# TODO: a shell comment\n' > pkg/comment.sh
git add -A && git commit -qm reader-boundaries
expect "a template substitution comment is counted" 1 check-marker-budget --ceiling 99 --per-file pkg/template.js=0
expect "template literal data around a substitution is not counted" 0 check-marker-budget --ceiling 99 --per-file pkg/template.js=1
printf 'BROKEN = """\n# TODO: marker after an unclosed triple quote\n' > pkg/broken.py
git add pkg/broken.py
expect "a marker after an unclosed triple quote is counted" 1 check-marker-budget --ceiling 99 --per-file pkg/broken.py=0
expect_says "an unclosed Python literal is unreadable" "pkg/broken.py" check-marker-budget --ceiling 99
git rm -q --cached pkg/broken.py
rm pkg/broken.py
expect "a shell token containing # is not a comment" 0 check-marker-budget --ceiling 99 --per-file pkg/shell-token.sh=0
printf 'cat <<EOF\n\tEOF\n# TODO: ordinary heredoc data\nEOF\n' > pkg/heredoc.sh
git add pkg/heredoc.sh
expect "an ordinary heredoc parser limitation fails safely" 1 check-marker-budget --ceiling 99 --per-file pkg/heredoc.sh=0
git rm -q --cached pkg/heredoc.sh
rm pkg/heredoc.sh
printf 'cat <<\\EOF\ndata\nEOF\n# TODO: a real comment after an escaped delimiter\n' > pkg/heredoc-escaped.sh
git add pkg/heredoc-escaped.sh
expect "an escaped heredoc parser limitation fails safely" 1 check-marker-budget --ceiling 99 --per-file pkg/heredoc-escaped.sh=0
git rm -q --cached pkg/heredoc-escaped.sh
rm pkg/heredoc-escaped.sh
expect "a JavaScript regex character class is not a comment" 0 check-marker-budget --ceiling 99 --per-file pkg/regex.js=0
expect "JSX text is not a JavaScript comment" 0 check-marker-budget --ceiling 99 --per-file pkg/jsx-text.jsx=0
expect "a JSX attribute expression comment is counted" 1 check-marker-budget --ceiling 99 --per-file pkg/jsx-attribute.jsx=0
expect "a regex after a keyword is not a comment" 0 check-marker-budget --ceiling 99 --per-file pkg/regex-keyword.js=0
expect "a later C-style block marker after code is counted" 1 check-marker-budget --ceiling 99 --per-file pkg/block.js=0
expect "a shell arithmetic shift does not open a heredoc" 1 check-marker-budget --ceiling 99 --per-file pkg/arithmetic.sh=0
expect "a shell command substitution comment is counted" 1 check-marker-budget --ceiling 99 --per-file pkg/shell-substitution.sh=0
expect "a shell backtick substitution comment is counted" 1 check-marker-budget --ceiling 99 --per-file pkg/shell-backticks.sh=0
expect "a TypeScript comment is counted" 1 check-marker-budget --ceiling 99 --per-file pkg/comment.ts=0
expect "a TSX comment is counted" 1 check-marker-budget --ceiling 99 --per-file pkg/comment.tsx=0
expect "a JavaScript comment is counted" 1 check-marker-budget --ceiling 99 --per-file pkg/comment.js=0
expect "a JSX comment is counted" 1 check-marker-budget --ceiling 99 --per-file pkg/comment.jsx=0
expect "an MJS comment is counted" 1 check-marker-budget --ceiling 99 --per-file pkg/comment.mjs=0
expect "a Go comment is counted" 1 check-marker-budget --ceiling 99 --per-file pkg/comment.go=0
expect "a shell comment is counted" 1 check-marker-budget --ceiling 99 --per-file pkg/comment.sh=0
printf 'cat <<E"OF"\ndata\nEOF\n# TODO: a real comment after a mixed delimiter\n' > pkg/heredoc-mixed.sh
git add pkg/heredoc-mixed.sh
expect "a mixed quoted heredoc parser limitation fails safely" 1 check-marker-budget --ceiling 99
expect_says "a mixed quoted heredoc parser limitation names the file" "pkg/heredoc-mixed.sh" check-marker-budget --ceiling 99
git rm -q --cached pkg/heredoc-mixed.sh
rm pkg/heredoc-mixed.sh
printf 'const value = "unterminated\n// TODO: hidden marker\n' > pkg/unreadable.js
git add pkg/unreadable.js
expect_unreadable_marker "an unclosed JavaScript literal" pkg/unreadable.js
printf 'const value = "unterminated\n// TODO: hidden marker\n' > pkg/unreadable.ts
git add pkg/unreadable.ts
expect_unreadable_marker "an unclosed TypeScript literal" pkg/unreadable.ts
printf 'const value = "unterminated\n// TODO: hidden marker\n' > pkg/unreadable.tsx
git add pkg/unreadable.tsx
expect_unreadable_marker "an unclosed TSX literal" pkg/unreadable.tsx
printf 'const value = "unterminated\n// TODO: hidden marker\n' > pkg/unreadable.jsx
git add pkg/unreadable.jsx
expect_unreadable_marker "an unclosed JSX literal" pkg/unreadable.jsx
printf 'const value = `unterminated\n// TODO: hidden marker\n' > pkg/unreadable.mjs
git add pkg/unreadable.mjs
expect_unreadable_marker "an unclosed MJS template literal" pkg/unreadable.mjs
printf 'var value = "unterminated\n// TODO: hidden marker\n' > pkg/unreadable.go
git add pkg/unreadable.go
expect_unreadable_marker "an unclosed Go literal" pkg/unreadable.go
printf 'value="unterminated\n# TODO: hidden marker\n' > pkg/unreadable.sh
git add pkg/unreadable.sh
expect_unreadable_marker "an unclosed shell literal" pkg/unreadable.sh
printf 'const value = "first line\nsecond line\n' > pkg/multiline.js
git add pkg/multiline.js
expect_unreadable_marker "an invalid JavaScript multiline literal" pkg/multiline.js
printf 'const value = 1; /* never closes\n' > pkg/block-unreadable.js
git add pkg/block-unreadable.js
expect_unreadable_marker "an unclosed JavaScript block comment" pkg/block-unreadable.js
printf 'cat <<EOF\n# TODO: heredoc data without a terminator\n' > pkg/heredoc-unreadable.sh
git add pkg/heredoc-unreadable.sh
expect_unreadable_marker "a shell heredoc without its terminator" pkg/heredoc-unreadable.sh
printf '\377\376\372' > pkg/invalid-utf8.js
git add pkg/invalid-utf8.js
expect_unreadable_marker "invalid UTF-8 JavaScript source" pkg/invalid-utf8.js
expect "--per-file without = is rejected" 2 check-marker-budget --ceiling 5 --per-file pkg/a.py
expect "--per-file with a non-number is rejected" 2 check-marker-budget --ceiling 5 --per-file pkg/a.py=x
expect "--per-file with a non-ASCII digit is rejected" 2 check-marker-budget --ceiling 5 --per-file "pkg/a.py=²"
expect_says "the malformed --per-file names the format" "expected PATH=N" \
    check-marker-budget --ceiling 5 --per-file "pkg/a.py=²"

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

echo "== inline comments: baseline arguments =="
expect "--no-baseline works without --baseline" 0 check-inline-comments --no-baseline pkg/e.py
expect "neither --baseline nor --no-baseline is rejected" 2 check-inline-comments pkg/e.py
expect "--baseline with --no-baseline is rejected" 2 check-inline-comments --baseline b.txt --no-baseline pkg/e.py
expect "--update-baseline with --no-baseline is rejected" 2 check-inline-comments --no-baseline --update-baseline pkg/e.py
expect "--shrink-baseline with --no-baseline is rejected" 2 check-inline-comments --no-baseline --shrink-baseline pkg/e.py
expect "--shrink-baseline with --update-baseline is rejected" 2 \
    check-inline-comments --baseline b.txt --shrink-baseline --update-baseline pkg/e.py

echo "== inline comments: baseline contents =="
mkdir -p grand
printf 'def f():\n    x = 1  # keep me\n    return x\n' > grand/m.py
check-inline-comments --baseline grand.txt --update-baseline grand >/dev/null 2>&1
printf 'def f():\n\n\n    x = 1  # keep me\n    return x\n' > grand/m.py
expect "a moved comment stays grandfathered" 0 check-inline-comments --baseline grand.txt grand
printf 'def f():\n    x = 1  # keep me, now edited\n    return x\n' > grand/m.py
expect "an edited comment is no longer grandfathered" 1 check-inline-comments --baseline grand.txt grand
expect_says "the edited comment is reported" "now edited" check-inline-comments --baseline grand.txt grand

mkdir -p stale
printf 'def f():\n    x = 1  # goes away\n    return x\n' > stale/s.py
check-inline-comments --baseline stale.txt --update-baseline stale >/dev/null 2>&1
printf 'def f():\n    x = 1\n    return x\n' > stale/s.py
expect "a stale baseline entry fails" 1 check-inline-comments --baseline stale.txt stale
expect_says "the stale entry is named" "stale x1" check-inline-comments --baseline stale.txt stale

mkdir -p multi
printf 'def f():\n    x = 1  # twice\n    y = 2  # twice\n    return x + y\n' > multi/m.py
check-inline-comments --baseline multi.txt --update-baseline multi >/dev/null 2>&1
expect "two identical comments are grandfathered twice" 0 check-inline-comments --baseline multi.txt multi
printf 'def f():\n    x = 1  # twice\n    y = 2  # twice\n    z = 3  # twice\n    return x\n' > multi/m.py
expect "a third copy of a grandfathered comment fails" 1 check-inline-comments --baseline multi.txt multi

mkdir -p shrink
printf 'def f():\n    x = 1  # keep twice\n    y = 2  # keep twice\n    return x + y\n' > shrink/s.py
check-inline-comments --baseline shrink.txt --update-baseline shrink >/dev/null 2>&1
printf 'def f():\n    x = 1  # keep twice\n    return x\n' > shrink/s.py
expect "--shrink-baseline lowers a fingerprint count" 0 check-inline-comments --baseline shrink.txt --shrink-baseline shrink
expect "a shrunk fingerprint keeps its reduced count" 0 bash -c 'test "$(cut -f4 "$1")" = 1' bash shrink.txt

mkdir -p removed
printf 'def f():\n    x = 1  # remove me\n    return x\n' > removed/r.py
check-inline-comments --baseline removed.txt --update-baseline removed >/dev/null 2>&1
printf 'def f():\n    return 1\n' > removed/r.py
expect "--shrink-baseline removes a missing fingerprint" 0 check-inline-comments --baseline removed.txt --shrink-baseline removed
expect "a missing fingerprint is removed from the baseline" 0 test ! -s removed.txt
printf 'def f():\n    x = 1  # a new violation\n    return x\n' > removed/r.py
expect "--shrink-baseline cannot grandfather a new violation" 1 check-inline-comments --baseline removed.txt --shrink-baseline removed
expect "a new violation still fails after shrinking" 1 check-inline-comments --baseline removed.txt removed

printf 'not a baseline line\n' > bad-baseline.txt
expect "a malformed baseline line fails" 1 check-inline-comments --baseline bad-baseline.txt multi
expect_says "the malformed baseline line is located" "bad-baseline.txt:1" check-inline-comments --baseline bad-baseline.txt multi
printf 'a.py\tinline\tabc123abc123\tmany\n' > bad-count.txt
expect "a baseline count that is not a number fails" 1 check-inline-comments --baseline bad-count.txt multi

mkdir -p invalid-baseline
printf 'VALUE = 1\n' > invalid-baseline/clean.py
printf '\tinline\tabc123abc123\t1\n' > bad-path.txt
expect_invalid_shrink_baseline "a baseline entry without a path" bad-path.txt invalid-baseline/clean.py
printf 'invalid-baseline/clean.py\tunsupported\tabc123abc123\t1\n' > bad-kind.txt
expect_invalid_shrink_baseline "a baseline entry with an unsupported kind" bad-kind.txt invalid-baseline/clean.py
printf 'invalid-baseline/clean.py\tinline\tabc123abc12g\t1\n' > bad-digest.txt
expect_invalid_shrink_baseline "a baseline entry with a non-hex digest" bad-digest.txt invalid-baseline/clean.py
printf 'invalid-baseline/clean.py\tinline\tabc123abc123\t0\n' > zero-count.txt
expect_invalid_shrink_baseline "a baseline entry with a zero count" zero-count.txt invalid-baseline/clean.py
printf 'invalid-baseline/clean.py\tinline\tabc123abc123\t-1\n' > negative-count.txt
expect_invalid_shrink_baseline "a baseline entry with a negative count" negative-count.txt invalid-baseline/clean.py

echo "== scope =="
mkdir -p empty
expect "an empty scan fails the comment gate" 1 check-inline-comments --no-baseline empty
expect "an empty scan fails the dict gate" 1 dict-param-check empty

mkdir -p lib/tests
printf 'x = 1  # a bad comment beside a tests directory\n' > lib/bad.py
printf 'def pub(d: dict) -> int:\n    return 1\n' > lib/dp.py
printf 'CLEAN = 1\n' > lib/tests/ok.py
expect "a directory holding tests/ is still read whole by the comment gate" 1 check-inline-comments --no-baseline lib
expect "a directory holding tests/ is still read whole by the dict gate" 1 dict-param-check lib

mkdir -p proj/src proj/notes
printf '[project]\nname = "p"\n' > proj/pyproject.toml
printf 'OK = 1\n' > proj/src/ok.py
printf 'x = 1  # outside the source directories\n' > proj/notes/bad.py
expect "a project root narrows to its source directories" 0 check-inline-comments --no-baseline proj
printf 'y = 2  # sitting at the project root\n' > proj/top.py
expect "a project root still reads its own top-level files" 1 check-inline-comments --no-baseline proj

mkdir -p proj2/src/__pycache__ proj2/extra
printf '[project]\nname = "p2"\n' > proj2/pyproject.toml
printf 'OK = 1\n' > proj2/src/ok.py
printf 'ALSO = 2\n' > proj2/extra/ok.py
printf 'CACHED = 3\n' > proj2/src/__pycache__/c.py
expect_says "an extra directory is not a source directory by default" "scope=1" check-inline-comments --no-baseline proj2
expect_says "--src-dir ADDS to the default source directories" "scope=2" \
    check-inline-comments --no-baseline --src-dir extra proj2
expect_says "--skip-dir KEEPS the default skipped directories" "scope=1" \
    check-inline-comments --no-baseline --skip-dir extra proj2
expect_says "--skip-dir can exclude a directory --src-dir added" "scope=1" \
    check-inline-comments --no-baseline --src-dir extra --skip-dir extra proj2
printf 'def pub(d: dict) -> int:\n    return 1\n' > proj2/extra/dp.py
expect "the dict gate ignores an extra directory by default" 0 dict-param-check proj2
expect "--src-dir widens the dict gate too" 1 dict-param-check --src-dir extra proj2

echo "== unreadable files =="
mkdir -p bad
mkdir -p unreadable-shrink
printf 'def f():\n    x = 1  # keep me\n    return x\n' > unreadable-shrink/u.py
check-inline-comments --baseline unreadable-shrink.txt --update-baseline unreadable-shrink >/dev/null 2>&1
cp unreadable-shrink.txt unreadable-shrink.before
printf 'def f(:\n' > unreadable-shrink/u.py
expect "--shrink-baseline rejects an unreadable source" 1 \
    check-inline-comments --baseline unreadable-shrink.txt --shrink-baseline unreadable-shrink
expect "an unreadable source leaves the baseline unchanged" 0 cmp unreadable-shrink.txt unreadable-shrink.before
printf 'def broken(:\n# a comment hidden behind a syntax error\n' > bad/broken.py
expect "an unparsable file fails the comment gate" 1 check-inline-comments --no-baseline bad/broken.py
expect_says "the unparsable file is named" "bad/broken.py" check-inline-comments --no-baseline bad/broken.py
expect "an unparsable file fails the dict gate" 1 dict-param-check bad/broken.py
check-inline-comments --baseline broken.txt --update-baseline bad/broken.py >/dev/null 2>&1
expect "an unparsable file cannot be grandfathered" 1 check-inline-comments --baseline broken.txt bad/broken.py
printf '# -*- coding: nosuchcodec -*-\nx = 1\n' > bad/cookie.py
expect "a bad encoding cookie is reported, not raised" 1 check-inline-comments --no-baseline bad/cookie.py
expect_says "the encoding failure names the file" "bad/cookie.py" check-inline-comments --no-baseline bad/cookie.py
expect "a bad encoding cookie fails the dict gate too" 1 dict-param-check bad/cookie.py
printf 'x = "\377\376\372"\n' > bad/rawbytes.py
expect "undecodable bytes fail the comment gate" 1 check-inline-comments --no-baseline bad/rawbytes.py
expect "undecodable bytes fail the dict gate" 1 dict-param-check bad/rawbytes.py

echo "== dict params =="
printf 'def pub(x: dict) -> int:\n    return 1\n' > pkg/f.py
expect "a public dict parameter fails" 1 dict-param-check pkg/f.py
printf 'def pub(x: dict) -> int:  # ALLOW: dict-param\n    return 1\n' > pkg/g.py
expect "an ALLOW badge suppresses it" 0 dict-param-check pkg/g.py
printf 'def _priv(x: dict) -> int:\n    return 1\n' > pkg/h.py
expect "a private function is not checked" 0 dict-param-check pkg/h.py
printf 'def pub2(x: int) -> dict:\n    return {}\n' > pkg/i.py
expect "a dict return type fails" 1 dict-param-check pkg/i.py

printf 'import typing\ndef pub(x: typing.Dict[str, int]) -> int:\n    return 1\n' > pkg/j.py
expect "typing.Dict[...] is caught" 1 dict-param-check pkg/j.py
printf 'import typing\ndef pub(x: typing.Dict) -> int:\n    return 1\n' > pkg/q.py
expect "a bare typing.Dict is caught" 1 dict-param-check pkg/q.py
printf 'def pub(x: dict.Thing) -> int:\n    return 1\n' > pkg/r.py
expect "an attribute whose value is named dict is not caught" 0 dict-param-check pkg/r.py
printf 'def pub(x: "dict[str, int]") -> int:\n    return 1\n' > pkg/k.py
expect "a string annotation is caught" 1 dict-param-check pkg/k.py
printf 'def pub(*args: dict) -> int:\n    return 1\n' > pkg/l.py
expect "a dict on *args is caught" 1 dict-param-check pkg/l.py
printf 'def pub(**kwargs: dict) -> int:\n    return 1\n' > pkg/m.py
expect "a dict on **kwargs is caught" 1 dict-param-check pkg/m.py
printf 'def pub(x: dict) -> int:  # ALLOW: dict-parameter-later\n    return 1\n' > pkg/n.py
expect "a badge that merely starts with the name does not suppress" 1 dict-param-check pkg/n.py

printf 'def pub(\n    x: dict,  # ALLOW: dict-param\n) -> int:\n    return 1\n' > pkg/o.py
expect "a badge on the annotation line suppresses" 0 dict-param-check pkg/o.py
printf 'def pub(  # ALLOW: dict-param\n    x: dict,\n) -> int:\n    return 1\n' > pkg/p.py
expect "a badge on the def line does not suppress" 1 dict-param-check pkg/p.py
expect_says "the report names the parameter's own line" "pkg/p.py:2:5" dict-param-check pkg/p.py
printf 'from collections.abc import Mapping\ndef pub(x: Mapping[str, int]) -> int:\n    return 1\n' > pkg/s.py
expect "an imported Mapping is caught" 1 dict-param-check pkg/s.py
printf 'from collections.abc import MutableMapping\ndef pub(x: MutableMapping[str, int]) -> int:\n    return 1\n' > pkg/t.py
expect "an imported MutableMapping is caught" 1 dict-param-check pkg/t.py
printf 'import collections.abc\ndef pub(x: collections.abc.Mapping[str, int]) -> int:\n    return 1\n' > pkg/u.py
expect "a qualified Mapping is caught" 1 dict-param-check pkg/u.py
printf 'import collections.abc\ndef pub(x: collections.abc.MutableMapping[str, int]) -> int:\n    return 1\n' > pkg/v.py
expect "a qualified MutableMapping is caught" 1 dict-param-check pkg/v.py
printf 'import typing\ndef pub(x: typing.Mapping[str, int]) -> int:\n    return 1\n' > pkg/aa.py
expect "a typing.Mapping is caught" 1 dict-param-check pkg/aa.py
printf 'import typing\ndef pub(x: typing.MutableMapping[str, int]) -> int:\n    return 1\n' > pkg/ab.py
expect "a typing.MutableMapping is caught" 1 dict-param-check pkg/ab.py
printf 'import builtins\ndef pub(x: builtins.dict[str, int]) -> int:\n    return 1\n' > pkg/ac.py
expect "a builtins.dict is caught" 1 dict-param-check pkg/ac.py
printf 'def pub(x: foreign.Mapping[str, int]) -> int:\n    return 1\n' > pkg/w.py
expect "a foreign Mapping is not caught" 0 dict-param-check pkg/w.py
printf 'def pub(x: foreign.MutableMapping[str, int]) -> int:\n    return 1\n' > pkg/x.py
expect "a foreign MutableMapping is not caught" 0 dict-param-check pkg/x.py
printf 'def pub(x: "foreign.Mapping[str, int]") -> int:\n    return 1\n' > pkg/y.py
expect "a quoted foreign Mapping is not caught" 0 dict-param-check pkg/y.py
printf 'def pub(x: list[foreign.MutableMapping[str, int]]) -> int:\n    return 1\n' > pkg/z.py
expect "a wrapped foreign MutableMapping is not caught" 0 dict-param-check pkg/z.py

echo "== duplication =="
mkdir -p clones
printf 'def alpha():\n    total = 1\n    total += 2\n    total += 3\n    return total\n' > clones/a.py
printf 'def beta():\n    total = 1\n    total += 2\n    total += 3\n    return total\n' > clones/b.py
git add -A && git commit -qm clones
expect "a clone fails in tree mode" 1 check-duplication --root . --format python --ext '\.py$' \
    --min-lines 2 --min-tokens 5 --select tree --threshold 0
expect_says "--all . keeps the full tree scope" "Top duplication opportunities" check-duplication --root . --format python \
    --ext '\.py$' --min-lines 2 --min-tokens 5 --select tree --threshold 0 --all .
printf '"""Module documentation."""\n' > clones/docstring.py
expect "a docstring-only file does not break strict scope" 0 check-duplication --root . --format python --ext '\.py$' \
    --min-lines 2 --min-tokens 5 --select tree --threshold 0 --all clones --ignore 'clones/b.py' --strict-scope
printf 'def short():\n    value = 1\n    value += 2\n    return value\n' > clones/short.py
expect "strict scope reads files below the clone token limit" 0 check-duplication --root . --format python --ext '\.py$' \
    --select tree --threshold 0 --all clones/short.py --strict-scope
expect_says "strict scope reports the audited source count" "scope=1" check-duplication --root . --format python --ext '\.py$' \
    --select tree --threshold 0 --all clones/short.py --strict-scope
expect "--ignore narrows strict scope" 0 check-duplication --root . --format python --ext '\.py$' --min-lines 2 \
    --min-tokens 5 --select tree --threshold 0 --all clones --ignore 'clones/b.py' --strict-scope

printf 'def alpha():\n    total = 4\n    total += 5\n    total += 6\n    return total\n' > clones/a.py
printf 'def beta():\n    total = 4\n    total += 5\n    total += 6\n    return total\n' > clones/b.py
expect "a clone fails in diff mode" 1 check-duplication --root . --format python --ext '\.py$' \
    --min-lines 2 --min-tokens 5 --select diff --threshold 0

mkdir -p fake-bin
printf '#!/bin/sh\nwhile [ "$#" -gt 0 ]; do\n    if [ "$1" = "--output" ]; then\n        mkdir -p "$2"\n        printf "{\\\"statistics\\\": {\\\"total\\\": {\\\"sources\\\": 0}}, \\\"duplicates\\\": []}" > "$2/jscpd-report.json"\n        exit 0\n    fi\n    shift\ndone\nexit 1\n' > fake-bin/jscpd
chmod +x fake-bin/jscpd
expect "a strict scope mismatch fails" 1 env PATH="$PWD/fake-bin:$PATH" check-duplication --root . --format python \
    --ext '\.py$' --min-lines 2 --min-tokens 5 --select tree --threshold 0 --strict-scope

echo
echo "==== PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = 0 ]
