#!/bin/bash
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UV="$(command -v uv)"
NODE_DIRECTORY="$(dirname "$(command -v node)")"
TEMPORARY="$(mktemp -d)"
CONSUMER="$TEMPORARY/consumer"
trap 'rm -rf "$TEMPORARY"' EXIT

mkdir "$CONSUMER"
git -C "$ROOT" archive --format=tar HEAD | tar -xf - -C "$CONSUMER"
git -C "$ROOT" diff --binary HEAD | git -C "$CONSUMER" apply --allow-empty
while IFS= read -r -d '' path; do
    mkdir -p "$CONSUMER/$(dirname "$path")"
    cp -p "$ROOT/$path" "$CONSUMER/$path"
done < <(git -C "$ROOT" ls-files --others --exclude-standard -z)
git -C "$CONSUMER" init -q
git -C "$CONSUMER" config user.email test@example.com
git -C "$CONSUMER" config user.name test
git -C "$CONSUMER" add -A
git -C "$CONSUMER" commit -qm dogfood

run_pre_commit() {
    (
        cd "$CONSUMER"
        PRE_COMMIT_HOME="$TEMPORARY/pre-commit" "$UV" run --isolated --with pre-commit==4.6.0 pre-commit "$@"
    )
}

run_pre_commit validate-config .pre-commit-config.yaml
run_pre_commit install --install-hooks

FAKE_BIN="$TEMPORARY/fake-bin"
mkdir "$FAKE_BIN"
printf '#!/bin/sh\nlines=\noutput=\nwhile [ "$#" -gt 0 ]; do\n    if [ "$1" = "--min-lines" ]; then\n        lines="$2"\n    fi\n    if [ "$1" = "--output" ]; then\n        output="$2"\n    fi\n    shift\ndone\nmkdir -p "$output"\nif [ "$lines" = 1 ]; then\n    printf "{\\\"statistics\\\": {\\\"total\\\": {\\\"sources\\\": 14}}, \\\"duplicates\\\": %%s}" "$SCOPE_CLONES" > "$output/jscpd-report.json"\n    exit 1\nfi\nprintf "{\\\"statistics\\\": {\\\"total\\\": {\\\"sources\\\": 14}}, \\\"duplicates\\\": []}" > "$output/jscpd-report.json"\n' > "$FAKE_BIN/jscpd"
chmod +x "$FAKE_BIN/jscpd"

run_fake_duplication() {
    (
        cd "$CONSUMER"
        SCOPE_CLONES="$1" PATH="$FAKE_BIN:$PATH" node scripts/check-duplication.js --root . --format python,bash,javascript \
            --ext '\.(py|sh|js)$' --min-lines 5 --min-tokens 50 --select tree --threshold 0 --comment-prefix // --strict-scope
    )
}

if ! output="$(run_fake_duplication '[{}]' 2>&1)"; then
    printf 'a duplicate coverage result must not fail the scope audit\n%s\n' "$output" >&2
    exit 1
fi

if output="$(run_fake_duplication '[]' 2>&1)"; then
    printf 'a clone-free coverage result with exit 1 must fail\n' >&2
    exit 1
elif [[ "$output" != *'coverage scan exited 1'* ]]; then
    printf 'a clone-free coverage exit 1 reported an unexpected failure\n%s\n' "$output" >&2
    exit 1
fi

has_one_argument() {
    [ "$(grep -Fo -- "$2" "$1" | wc -l | tr -d ' ')" = 1 ] && grep -Fq -- "$3" "$1"
}

has_full_duplication_scope() {
    has_one_argument "$1" --root '--root, .' \
        && has_one_argument "$1" --format '--format, "python,bash,javascript"' \
        && has_one_argument "$1" --ext "--ext, '\\.(py|sh|js)\$'" \
        && has_one_argument "$1" --min-lines '--min-lines, "5"' \
        && has_one_argument "$1" --min-tokens '--min-tokens, "50"' \
        && has_one_argument "$1" --select '--select, tree' \
        && has_one_argument "$1" --threshold '--threshold, "0"' \
        && has_one_argument "$1" --comment-prefix '--comment-prefix, //' \
        && [ "$(grep -Fo -- --strict-scope "$1" | wc -l | tr -d ' ')" = 1 ] \
        && ! grep -Eq -- '--(all|ignore|exclude-prefix|diff-exclude)' "$1"
}

if ! diff \
    <(grep '^- id:' "$CONSUMER/.pre-commit-hooks.yaml" | cut -d ' ' -f3 | grep -v -e '^check-forbidden-mocks$' -e '^check-pytest-describe$' | sort) \
    <(grep '^      - id:' "$CONSUMER/.pre-commit-config.yaml" | awk '{print $3}' | sort); then
    printf 'dogfood configuration must apply every non-opt-in exported hook\n' >&2
    exit 1
fi

for hook in check-forbidden-mocks check-pytest-describe; do
    if grep -q "^      - id: $hook$" "$CONSUMER/.pre-commit-config.yaml"; then
        printf 'dogfood configuration must not enable opt-in hook %s\n' "$hook" >&2
        exit 1
    fi
done

if ! output="$(run_pre_commit run --all-files --verbose 2>&1)"; then
    printf '%s\n' "$output" >&2
    exit 1
fi
printf '%s\n' "$output"

if [ "$(printf '%s\n' "$output" | grep -c 'scope=')" -ne 5 ]; then
    printf 'every dogfood hook must report its declared scope\n' >&2
    exit 1
fi

if ! has_full_duplication_scope "$CONSUMER/.pre-commit-config.yaml"; then
    printf 'the duplication gate must scan the full Python, Bash, and JavaScript tree\n' >&2
    exit 1
fi

for option in --ignore --exclude-prefix; do
    narrowed="$TEMPORARY/${option#--}.yaml"
    cp "$CONSUMER/.pre-commit-config.yaml" "$narrowed"
    printf '        args: [%s, scripts/check-duplication.js]\n' "$option" >> "$narrowed"
    if has_full_duplication_scope "$narrowed"; then
        printf 'the duplication gate accepted %s as a full-tree configuration\n' "$option" >&2
        exit 1
    fi
done

for arguments in '--root, src' '--root, ., --root, src'; do
    narrowed="$TEMPORARY/root-${arguments##* }.yaml"
    cp "$CONSUMER/.pre-commit-config.yaml" "$narrowed"
    printf '        args: [%s]\n' "$arguments" >> "$narrowed"
    if has_full_duplication_scope "$narrowed"; then
        printf 'the duplication gate accepted narrowed root arguments: %s\n' "$arguments" >&2
        exit 1
    fi
done

printf 'DOGFOOD = 1\n' > "$CONSUMER/dogfood.py"
git -C "$CONSUMER" add dogfood.py
if PATH="$NODE_DIRECTORY:/usr/bin:/bin" command -v pre-commit >/dev/null; then
    printf 'the portability test path unexpectedly provides pre-commit\n' >&2
    exit 1
fi
PATH="$NODE_DIRECTORY:/usr/bin:/bin" run_pre_commit run --hook-stage pre-commit --all-files
