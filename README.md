# quality-gates

Three code quality gates, run as pre-commit hooks. Every repository-specific value is a
command-line argument, so one copy serves many repositories.

## Use

```yaml
- repo: https://github.com/tikal/quality-gates
  rev: v0.1.0
  hooks:
    - id: check-inline-comments
      args: [--baseline, .quality/inline-comments.txt]
    - id: dict-param-check
      args: [src]
    - id: check-marker-budget
      args: [--ceiling, "20", --per-file, "big_module.py=15"]
```

Each hook ships a working default `args:`, so `pre-commit try-repo` runs without configuration.
The defaults are deliberately strict: the marker budget defaults to `--ceiling 0`, so a
repository must state the number it wants.

## The gates

### check-inline-comments

Fails on a plain inline comment, a marker block over ten lines, and a docstring on a
private function. A comment must be a TODO, a FIXME or a NOTE.

`--baseline PATH` is required and has no default. A baseline stored beside an installed
hook would be shared between repositories and could not be written. Pass `--no-baseline`
instead to report every violation; the two are mutually exclusive, and so are
`--no-baseline` and `--update-baseline`.

Generate one for an existing tree with `--update-baseline`. The file grandfathers what
exists today, so a NEW violation still fails.

A baseline line is `path`, a tab, `kind`, a tab, twelve hexadecimal digits, a tab, a
count. The digits are a SHA-1 of the offending TEXT, not of its position: whitespace is
collapsed, the text is lowercased, and a leading `#` is removed. Moving a comment keeps
it grandfathered. Editing it does not. A line the gate cannot read in that shape is an
error, so a hand-edited baseline cannot silently lose entries.

### dict-param-check

Fails on `dict` or `dict[str, Any]` in a public parameter or return type. Use a
dataclass, a BaseModel or a TypedDict instead.

The gate reads public signatures only. It reads positional, keyword-only and
positional-only parameters, `*args` and `**kwargs`, and the return type. It resolves
`typing.Dict[...]`, a dict inside a wrapper such as `list[...]` or `Optional[...]`, a
union arm, and a string annotation such as `"dict[str, int]"`.

To keep one dict, put `# ALLOW: dict-param` or `# ALLOW: dict-return` on the line where
the ANNOTATION ENDS. On a one-line signature every annotation ends on that one line, so
ONE badge there exempts every dict parameter of that signature. Split the signature over
several lines to badge one parameter alone. The badge name must match exactly:
`# ALLOW: dict-parameter-later` does not suppress `dict-param`. There is no baseline.

### check-marker-budget

Caps the TODO/FIXME/NOTE blocks a repository may hold. `--ceiling N` sets the total.
A file may hold ten blocks; `--per-file PATH=N` raises or lowers that for one file.

A per-file entry that a file no longer needs is reported, so the budget cannot drift
above what the code uses.

Python files are read through `tokenize`, so marker text held in a string literal and a
`* NOTE:` bullet inside a docstring do not count. Every other language is read as text.

## Badges and skipped directories

`# ALLOW:` and `# TYPE:` are both badges. A badge is a machine-readable declaration, not
prose, so it is exempt from the comment rules and from every budget. No gate in this
package consumes `# TYPE:`; it is recognised so a repository that uses it for its own
tooling is not forced to choose between that tool and this one.

Every gate skips `venv`, `.venv`, `node_modules`, `__pycache__` and `_generated` anywhere
in a path. `_generated` is in that list because generated output is not written by hand,
so a rule about how a human writes a comment cannot apply to it.

`--skip-dir NAME` and `--src-dir NAME` ADD to those defaults and are repeatable. They can
only widen a scan, never narrow it: a flag that replaced the defaults could silently drop
a whole tree, and the gate would report a clean result it never earned. To scan less,
name the path you want.

## Behaviour every gate shares

- A scan that reads zero files FAILS. A gate that cannot fail is not a gate.
- A file the gate cannot decode or parse is a VIOLATION, not a skip. It is reported with
  its path and the reason, and it can never be grandfathered by a baseline. Otherwise
  committing an unparsable file would hide its contents from the gate forever.
- A directory argument narrows to `src` and `tests` only when it is a PROJECT ROOT, which
  means it holds a `pyproject.toml`, a `setup.py` or a `setup.cfg`. Any other directory is
  scanned whole. A project root is also scanned for `*.py` files sitting directly in it.
- A clean run prints `scope=N`, the number of files read.
- Exit code is 0 when clean and 1 on any breach.
- `check-marker-budget` reads the files git tracks, so `--root` must be a git repository.

## Test

```bash
uv venv && uv pip install --python .venv/bin/python -e .
PATH="$PWD/.venv/bin:$PATH" bash tests/run_tests.sh
```

Each gate is tested for the failure it exists to catch, and for the case it must let
through.

## Licence

MIT. See `LICENSE`.
