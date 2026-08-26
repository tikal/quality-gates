# quality-gates

Four code quality gates, run as pre-commit hooks. Every repository-specific value is a
command-line argument, so one copy serves many repositories.

## Use

```yaml
- repo: https://github.com/tikal/quality-gates
  rev: 4a4e9bbb8007e1d55c9d677ea4dd36faf197e8e9  # v0.1.0
  hooks:
    - id: check-inline-comments
      args: [--baseline, .quality/inline-comments.txt]
    - id: dict-param-check
      args: [--baseline, .quality/dict-params.txt, src]
    - id: check-marker-budget
      args: [--ceiling, "20", --per-file, "big_module.py=15"]
    - id: check-dead-code
      args: [--path, src, --path, tests, --min-confidence, "80"]
    - id: check-duplication
      args: [--root, ., --format, "python,bash", --ext, '\.(py|sh)$', --min-lines, "5", --min-tokens, "50", --select, tree, --threshold, "0", --strict-scope]
```

Pin this repository to a commit, not a tag. Each hook runs code when a consumer commits.
A tag can be moved, which changes the code consumers run without a reviewed change in their repository.

`v0.1.0` is an annotated tag. `git ls-remote` for `refs/tags/v0.1.0` returns the tag object, not
the commit. In a checkout, peel the tag with `git rev-list -n 1 v0.1.0` and pin the resulting
commit. `pre-commit autoupdate` can replace the commit pin with a tag. Review its changes and
restore the intended commit pin before you commit the update.

Each hook ships a working default `args:`, so `pre-commit try-repo` runs without configuration.
The defaults are deliberately strict: the marker budget defaults to `--ceiling 0`, so a
repository must state the number it wants.

## Scope contract

Every exported gate prints `scope=N` when it succeeds. `N` is the number of source files the
gate read. A zero-file scan fails. A clean result without either condition does not show that the
gate examined the intended source.

## The gates

### check-inline-comments

Fails on a plain inline comment, a marker block over ten lines, and a docstring on a
private function. A comment must be a TODO, a FIXME or a NOTE.

`--baseline PATH` is required and has no default. A baseline stored beside an installed
hook would be shared between repositories and could not be written. Pass `--no-baseline`
instead to report every violation; the two are mutually exclusive, and so are
`--no-baseline` and either baseline write command.

Generate one for an existing tree with `--update-baseline`. The file grandfathers what
exists today, so a NEW violation still fails. Use `--shrink-baseline` after you remove a
violation. It lowers matching counts and removes missing fingerprints. It never adds a
fingerprint, so a new violation still fails. It does not write a baseline when any scanned
source file cannot be read.

A baseline line is `path`, a tab, `kind`, a tab, twelve hexadecimal digits, a tab, a
count. The digits are a SHA-1 of the offending TEXT, not of its position: whitespace is
collapsed, the text is lowercased, and a leading `#` is removed. Moving a comment keeps
it grandfathered. Case-only and whitespace-only edits also keep it grandfathered. Editing
words or punctuation does not. A line the gate cannot read in that shape is an error, so a
hand-edited baseline cannot silently lose entries. The path cannot be empty. The kind must
be one this gate reports. The digest must contain twelve lowercase hexadecimal digits. The
count must be a positive decimal integer.

### dict-param-check

Fails on an anonymous mapping shape in a public parameter or return type. This
includes `dict`, `Mapping`, and `MutableMapping`. The policy rejects anonymous
mapping shapes, not typed values. Use a dataclass, a BaseModel or a TypedDict to
declare the mapping shape.

The gate reads public signatures only. It reads positional, keyword-only and
positional-only parameters, `*args` and `**kwargs`, and the return type. It resolves
canonical qualified forms such as `builtins.dict[...]`, `typing.Dict[...]`, and
`collections.abc.Mapping[...]`, a dict-like mapping inside a wrapper such as
`list[...]` or `Optional[...]`, a union arm, and a string annotation such as
`"dict[str, int]"`. Other qualified names such as `foreign.Mapping` are not
treated as mapping annotations.

`--baseline PATH` is optional. Without it, the gate reports every violation. Use
`--update-baseline` to record existing unbadged violations, and use
`--shrink-baseline` after a repair. A baseline still fails a NEW signature. Its
fingerprint contains the path, `dict-param` or `dict-return`, and a SHA-1 of the
annotation text. This keeps a moved signature grandfathered, but a changed
annotation or an additional matching signature fails. `--no-baseline` is explicit
strict mode. It cannot be combined with either baseline write command.

To keep one dict, put `# ALLOW: dict-param` or `# ALLOW: dict-return` on the line where
the ANNOTATION ENDS. On a one-line signature every annotation ends on that one line, so
ONE badge there exempts every dict parameter of that signature. Split the signature over
several lines to badge one parameter alone. The badge name must match exactly:
`# ALLOW: dict-parameter-later` does not suppress `dict-param`. An ALLOW badge is
exempt before baseline matching. It remains a deliberate permanent exemption, not
grandfathered debt.

### check-marker-budget

Caps the TODO/FIXME/NOTE blocks a repository may hold. `--ceiling N` sets the total.
A file may hold ten blocks; `--per-file PATH=N` raises or lowers that for one file.

A per-file entry that a file no longer needs is reported, so the budget cannot drift
above what the code uses.

Python files are read through `tokenize`. JavaScript, TypeScript, TSX, Go, and Bash files use
the bundled Tree-sitter grammars from `tree-sitter-language-pack`. Marker text in literal data
does not count. Syntax errors and invalid UTF-8 fail as unreadable source. A `* NOTE:` bullet
inside a Python docstring does not count.

The bundled Bash grammar has known heredoc parsing defects. A Bash file containing a heredoc
fails the marker budget rather than returning a result that could misread heredoc data as a
comment.

### check-dead-code

Runs Vulture against the source paths you declare. `--path PATH` is required and repeatable.
Each path must exist. The gate fails if the declared paths contain no Python files after Vulture
exclusions, because an empty clean result has no value.

On success, `scope=N` is the number of distinct Python files the wrapper selects from `--path`
after it applies `--exclude`. The wrapper reads and parses each selected file before it invokes
Vulture. This is the wrapper's verified input scope, not a source count that Vulture reports.

`--min-confidence N` sets Vulture's report threshold and defaults to `80`. Use
`--ignore-names PATTERNS` for Vulture's comma-separated name allowlist. Use `--exclude PATTERNS`
for its comma-separated absolute-path exclusions. A bare pattern such as `generated` becomes
`*generated*` and matches any absolute path that contains it. A glob pattern keeps its Vulture
meaning. Directory paths include hidden Python files and directories, as Vulture does. The hook
installs the exact `vulture==2.14.0` dependency in its isolated Python environment.

The hook ignores `[tool.vulture]` settings in a consumer `pyproject.toml`. Configure its effective
scope only with the hook arguments: `--path`, `--exclude`, `--ignore-names`, and
`--min-confidence`. This prevents project configuration from changing an audited scope.

### check-duplication

Runs `jscpd` against a declared source scope. The hook installs the exact `jscpd@5.0.16`
dependency in its isolated Node environment. It does not use `npx`, `jq`, or a globally
installed executable.

`--select diff` is the default. It scans the union of staged and unstaged added, copied, and
modified files. `--select tree` scans every tracked file plus untracked files that Git does not
ignore. `--all PATH` narrows either mode to one root-relative path.

`--strict-scope` counts selected files that have source beyond line comments and a standalone
triple-quoted docstring. The gate then compares that count with `jscpd`'s report. A mismatch
fails, so a tool limit cannot silently produce a partial clean result. Use `--comment-prefix` for
each line-comment syntax in the selected formats. The default prefix is `#`.

The scope audit uses one line and one token. Files below the clone limits still count as read.

`--ext` is a required regular expression. `--format` is the matching comma-separated `jscpd`
format list. `--min-lines`, `--min-tokens`, and `--threshold` keep the source detector's limits
visible in the consumer configuration. `--ignore`, `--exclude-prefix`, and `--diff-exclude`
remove files from the declared scope. The last option applies only in diff mode.

The gate prints up to ten clone locations on failure. `--no-report` suppresses those locations.
`--reporters` adds `jscpd` reporters, but JSON remains enabled because the scope check reads its
report.

## Badges and skipped directories

`# ALLOW:` and `# TYPE:` are both badges. A badge is a machine-readable declaration, not
prose. `# TYPE:` is exempt from the comment rules and from every budget.
`# TYPE:` marks a deliberate typing decision for a repository's type tooling. No gate in this
package consumes it.
The exemption remains for downstream compatibility because a downstream repository relies on it.

`check-inline-comments`, `dict-param-check`, and `check-marker-budget` skip `venv`, `.venv`,
`node_modules`, `__pycache__` and `_generated` anywhere in a path. `_generated` is in that list
because generated output is not written by hand, so a rule about how a human writes a comment
cannot apply to it. `check-dead-code` scans its declared `--path` scope, subject to Vulture
`--exclude` patterns.

`check-inline-comments` and `dict-param-check` support repeatable `--skip-dir NAME` and
`--src-dir NAME`. They ADD to their defaults. They can only widen a scan, never narrow it: a
flag that replaced the defaults could silently drop a whole tree, and the gate would report a
clean result it never earned. To scan less, name the path you want.

## Behaviour every gate shares

- A scan that reads zero files fails. A gate that cannot fail is not a gate.
- A file the gate cannot decode or parse is a VIOLATION, not a skip. It is reported with
  its path and the reason, and it can never be grandfathered by a baseline. Otherwise
  committing an unparsable file would hide its contents from the gate forever.
- For `check-inline-comments` and `dict-param-check`, a directory argument narrows to `src` and
  `tests` only when it is a PROJECT ROOT, which means it holds a `pyproject.toml`, a `setup.py`
  or a `setup.cfg`. Any other directory is scanned whole. A project root also scans `*.py` files
  sitting directly in it.
- A clean run prints `scope=N`, the number of files read. `check-dead-code` reports its
  wrapper-verified Python scope, as defined above.
- Exit code is 0 when clean and 1 on any breach.
- `check-marker-budget` reads the files git tracks, so `--root` must be a git repository.

## Test

```bash
uv venv && uv pip install --python .venv/bin/python -e .
PATH="$PWD/.venv/bin:$PATH" bash tests/run_tests.sh
bash tests/run_integration.sh
bash tests/run_dogfood.sh
```

Each gate is tested for the failure it exists to catch, and for the case it must let
through. The integration suite creates an isolated consumer repository and installs each hook
from a packaged repository through pre-commit. The dogfood suite installs every exported hook
through `.pre-commit-config.yaml` and runs it across this repository.

## Licence

MIT. See `LICENSE`.
