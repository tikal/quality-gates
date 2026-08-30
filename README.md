# quality-gates

Eighteen code quality gates, run as pre-commit hooks. Every repository-specific value is a
command-line argument, so one copy serves many repositories.

## Use

Start with the policy you intend to adopt. This minimal configuration works in any Git repository;
set the ceiling to the marker budget your repository has reviewed.

```yaml
repos:
  - repo: https://github.com/tikal/quality-gates
    rev: afc57504c07e827cf108764dad1ab1e06204c155  # reviewed commit
    hooks:
      - id: check-marker-budget
        args: [--ceiling, "20"]
```

Add only the policies and paths that match your repository. This Python-oriented example uses
`src` and `tests` as explicit consumer choices, not package defaults:

```yaml
repos:
  - repo: https://github.com/tikal/quality-gates
    rev: afc57504c07e827cf108764dad1ab1e06204c155  # reviewed commit
    hooks:
      - id: check-inline-comments
        args: [--baseline, .quality/inline-comments.txt, src]
      - id: dict-param-check
        args: [--baseline, .quality/dict-params.txt, src]
      - id: check-marker-preservation
      - id: check-dead-code
        args: [--path, src, --path, tests, --min-confidence, "80"]
      - id: check-forbidden-mocks
        args: [--factory-location, tests/factories.py, tests]
      - id: check-pytest-describe
        args: [tests]
      - id: check-duplication
        args: [--root, ., --format, "python,bash", --ext, '\.(py|sh)$', --min-lines, "5", --min-tokens, "50", --select, tree, --threshold, "0", --strict-scope]
```

Pin this repository to a commit, not a tag. Each hook runs code when a consumer commits.
A tag can be moved, which changes the code consumers run without a reviewed change in their repository.

`pre-commit autoupdate` can replace a commit pin with a tag. Review its changes and restore the
intended reviewed commit before you commit the update.

The metadata supplies policy defaults, not a guarantee that an arbitrary repository is clean.
Baselines, declared source paths, and strict budgets need consumer setup. `check-forbidden-mocks`, `check-pytest-describe`, `check-hook-scope-contract`,
`check-manifest-audit-coverage`, `check-dockerfile-enrollment`, `check-marker-preservation`, `check-generated-artifact-freshness`,
`check-downloaded-asset-enrollment`, `check-vulnerability-exceptions`, `check-container-image-cves`,
`check-container-image-enrollment`, `check-container-image-immutable-assessment`, and `check-base-image-eol` are opt-in. The mock gate
requires a consumer to set `--factory-location PATH`; configure the pytest-describe gate with
its intended test roots and each enrollment gate with its consumer policy inputs. The defaults are deliberately strict: the marker budget defaults to
`--ceiling 0`, so a repository must state the number it wants.

## Scope contract

Every exported gate prints `scope=N` when it succeeds. `N` is the number of audited units; source
gates count files, while meta-gates count their declared configuration units. A zero-source-file
scan fails. A clean result without either condition does not show that the gate examined its
intended scope.

## The gates

### check-inline-comments

Fails on a plain comment, a marker block over ten lines, an overlong module docstring, and
a docstring on a private function. Marker comments may be TODO, FIXME, NOTE, HACK, or XXX.

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

Caps TODO/FIXME/NOTE/HACK/XXX blocks a repository may hold. `--ceiling N` sets the total.
A file may hold ten blocks; `--per-file PATH=N` raises or lowers that for one file.

A raised per-file allowance that a file no longer needs is reported, so extra budget cannot drift
above what the code uses.

### check-marker-preservation

This opt-in, pre-commit-only gate compares staged index blobs with their `HEAD` versions and fails
when an exact marker header disappears. It protects `TODO`, `FIXME`, `NOTE`, `HACK`, and `XXX`
comments in `.py`, `.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`, `.go`, and `.sh` source files. Parsed
comment nodes/tokens count; marker-looking literal data does not.

The gate reads the index, not working-tree files, so unstaged edits cannot hide a staged removal.
It preserves a multiset of exact, stripped header text within one path, so same-file relocation is
allowed but rewording and cross-file moves are not. An initial commit uses an empty baseline.
Renames are conservatively treated as deletion plus addition. Decode and parse failures on either
blob, including Bash heredocs, fail the gate. Generated filenames and shared skipped directories
are out of scope. A clean run reports staged eligible paths as `scope=N`. When no eligible source
path is staged, it instead compares every tracked eligible source path, so `pre-commit run --all-files`
remains a meaningful verification. A repository with no eligible tracked source files fails.

Python files are read through `tokenize`. JavaScript, TypeScript, TSX, Go, and Bash files use
the bundled Tree-sitter grammars from `tree-sitter-language-pack`. Marker text in literal data
does not count. Syntax errors and invalid UTF-8 fail as unreadable source. A `* NOTE:` bullet
inside a Python docstring does not count.

The Tree-sitter-backed marker hooks pin the tested `tree-sitter==0.25.2` runtime. They fail closed
if a selected source cannot be parsed safely.

For a valid Bash heredoc, marker-looking text in the heredoc body is ignored while real comments
outside the body are scanned. Malformed or ambiguous heredoc syntax remains a fail-closed error.

### check-marker-removal-authorization

This opt-in alternative to `check-marker-preservation` runs at the `commit-msg` stage. It permits
an intentional staged marker removal only when the final commit message contains one exact,
rationale-backed trailer for each removed header. Do not configure it alongside strict
`check-marker-preservation`: the strict pre-commit gate intentionally permits no removals.

```yaml
default_install_hook_types: [pre-commit, commit-msg]
repos:
  - repo: https://github.com/tikal/quality-gates
    rev: <reviewed revision>
    hooks:
      - id: check-marker-removal-authorization
```

Set `default_install_hook_types` so a normal `pre-commit install` installs the required
`commit-msg` hook. Each trailer has exactly three pipe-separated values: repository-relative path,
the full marker header as reported by the gate (including its comment prefix), and a nonblank
rationale. Quote values containing a pipe. In a quoted value, double each embedded double quote
(`"` becomes `""`).

```text
Marker-Removal: src/engine.py | "# NOTE: provenance remains visible" | The provenance mechanism was removed.
```

The gate compares `HEAD` and staged index blobs just like `check-marker-preservation`. Missing,
stale, mismatched, or malformed trailers fail. Repeated identical removals require repeated
trailers, so no authorization can cover another file, header, or occurrence. A clean run reports
the staged eligible source-file count as `scope=N`. With no staged eligible source path, it checks
every tracked eligible source file and reports that fallback scope.

### check-generated-artifact-freshness

This opt-in gate verifies that declared generated artifacts exactly match deterministic output.
Pass each tracked artifact with repeatable `--artifact PATH`, then put the generator's complete
argv after `--`:

```text
check-generated-artifact-freshness [--root PATH] --artifact PATH [--artifact PATH ...] -- COMMAND [ARG ...]
```

An artifact path must be an exact root-relative POSIX path. It cannot be absolute, contain `.` or
`..`, or be repeated. The gate requires every declared artifact to be tracked in the index.

```yaml
- id: check-generated-artifact-freshness
  args:
    - --artifact
    - generated/output.txt
    - --artifact
    - generated/schema.json
    - --
    - python
    - tools/generate.py
```

The command runs from a temporary staged-index snapshot, excluding the declared output artifacts,
not from the working tree. Unstaged edits therefore cannot affect the inputs it verifies. The gate
runs the same argv twice against independent snapshots. For each run it sets
`QUALITY_GATES_OUTPUT_DIR` to a different empty directory. The generator must write each declared
artifact below that directory at its declared relative path; for example, it writes `generated/output.txt` to
`$QUALITY_GATES_OUTPUT_DIR/generated/output.txt`. It must not rely on output written into the
snapshot.

The two generated byte sequences must match each other and then match the staged bytes. The gate
fails if an artifact is untracked, a Git operation or snapshot read fails, the generator exits
nonzero or exceeds its 30-second limit, an expected output cannot be read, either run produces
different bytes, or generated bytes differ from the staged artifact. A clean run reports
`scope=N`, where `N` is the number of declared artifacts.

The hook executes consumer-configured argv, so it is deliberately opt-in. Treat the generator and
its staged inputs as code execution: review changes to both, and enable this gate in trusted CI
before or alongside developer pre-commit use. Do not add it to an environment that automatically
runs untrusted repository configuration.

Use `--reject-extra-outputs` when the declared artifacts must be the generator's complete output
set. Use `--clear-env` to start the generator with no inherited environment (apart from
`QUALITY_GATES_OUTPUT_DIR` and `PWD`), then add only required settings with `--env NAME=VALUE` or
`--inherit-env NAME`. `--timeout-seconds` is bounded to 1--300 seconds and defaults to 30. These
options make the generator's input and output boundary explicit; retain consumer-specific command,
environment, timeout, and artifact policy values rather than adopting example values blindly.

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

### check-forbidden-mocks

This gate is opt-in. Add the hook to a consumer configuration and set
`--factory-location PATH`. The location is required. It appears in each failure as the place
where that repository keeps its approved test doubles. The package does not assume a `conftest`
path or create a default location.

The gate rejects calls to `Mock`, `MagicMock`, `AsyncMock`, and `patch`. It also rejects a
`patch` decorator and a `monkeypatch` function parameter. It reports every independent finding
in a file, so one repair does not expose a hidden mock violation.

### check-pytest-describe

This gate is opt-in. It validates `test_*.py` and `conftest.py` below the paths it receives;
pass the test roots explicitly, such as `args: [tests]`. A directory with no matching test files
fails, as does a test file that cannot be decoded or parsed. A clean run prints `scope=N` for
the test files it read. Every explicitly supplied root must contain at least one matching test file.

The gate enforces this opinionated `pytest-describe` grammar:

- Only `describe_*` hierarchy blocks may appear at module level. Ordinary module helpers and
  fixtures are outside this grammar.
- A `describe_*` block may contain nested descriptions, preconditions, scenarios, or direct
  tests. It cannot mix direct tests and scenarios at the same level.
- Every hierarchy block must contain at least one recognized child.
- `given_*` and `for_*` are preconditions. They may contain scenarios or direct tests, but not
  descriptions or another precondition.
- `when_*`, `with_*`, `without_*`, and `and_*` are scenarios. They may contain preconditions,
  nested scenarios, or direct tests, but not descriptions.
- `test_*` and `it_*` are leaves. They cannot contain a nested hierarchy block.
- A hierarchy block cannot embed `_when_`, `_with_`, `_without_`, `_and_`, `_given_`, or `_for_`
  in its name. Express that condition with a nested block instead.

Use repeatable `--condition-infix VALUE` to replace that default infix list with the consumer's
approved vocabulary. For example, `--condition-infix _when_ --condition-infix _with_` preserves
a policy that only prohibits embedded `when` and `with` conditions.

The gate only recognizes direct function children of a module or hierarchy block. It does not
classify methods on ordinary classes or nested helper functions outside the declared hierarchy.
Configure `pytest-describe` collection prefixes to match this grammar in each consumer project.

### check-hook-scope-contract

This opt-in meta-gate audits a consumer `.pre-commit-config.yaml`. Every declared hook must be
listed exactly once as a `--scope-emitter ID=PATH` or an `--exempt ID=REASON`. An emitter is a
repository-owned source file that contains a dynamic `scope=` output. The auditor fails on
unclassified hooks, stale declarations, unreadable emitters, or a guard that is not itself a
reporting emitter. A clean run reports the number of classified hooks as `scope=N`.

```yaml
- id: check-hook-scope-contract
  args:
    - --hook-id=check-hook-scope-contract
    - --scope-emitter=check-hook-scope-contract=scripts/hook_scope.py
    - --scope-emitter=check-marker-budget=scripts/marker_budget.py
    - --exempt=ruff=Upstream hook owns changed-file selection.
```

The configured hook `entry:` must name each mapped emitter. To keep this static check auditable,
the accepted forms are `PATH`, `python PATH`, `bash PATH`, `uv run python PATH`, or `bash -c`
containing one of those forms. The `bash -c` form may begin with exactly
`cd RELATIVE_DIRECTORY &&` when the remaining emitter path resolves to the mapped path. Shell
pipelines, redirections, substitutions, and other compound commands are rejected. The gate
statically verifies wiring; each emitter still needs its own runtime test proving its clean path
prints an accurate scope.

### check-manifest-audit-coverage

This opt-in meta-gate verifies dependency-audit enrollment, not vulnerability findings. Declare
each dependency manifest basename with `--manifest` and select audit hooks by
`--audit-hook-prefix`. Every tracked matching manifest must match a selected hook's `files:`
regular expression or have an exact, rationale-backed exemption in an optional TSV file.
Exemptions become stale when their manifest disappears or becomes covered. A configured gate
with no declared manifests fails rather than reporting a meaningless clean result.

```yaml
- id: check-manifest-audit-coverage
  args: [--manifest, pyproject.toml, --manifest, package.json, --audit-hook-prefix, dependency-audit-, --exemptions, .quality/manifest-exemptions.tsv]
```

Each exemption is `root/relative/manifest<TAB>reviewed rationale`. The exemption file must be
tracked, and a stale exemption fails.

### check-dockerfile-enrollment

This opt-in gate requires a tracked JSON ledger (`--ledger PATH`) to classify every tracked
`Dockerfile*` as `pull`, `build`, or `ignore`. Ignore entries need a rationale, and stale ledger
entries fail. It reads every selected Dockerfile and reports `scope=N`; zero Dockerfiles fail.
The gate verifies scan enrollment only. It does not run Docker, build images, or scan CVEs.

```yaml
- id: check-dockerfile-enrollment
  args: [--ledger, .quality/dockerfile-enrollment.json]
```

```json
{"version": 1, "dockerfiles": [{"path": "services/api/Dockerfile", "classification": "build"}, {"path": "examples/legacy/Dockerfile", "classification": "ignore", "rationale": "Documentation-only example."}]}
```

### check-downloaded-asset-enrollment

This opt-in enrollment gate requires every direct external asset acquisition selected by consumer-supplied
file and site regular expressions to be recorded in a tracked JSON ledger. Each record identifies an exact
file/selector pair and declares `sha256`, `signature`, `repository-signature`, `digest`, `unverified`, or
`ignore`. `unverified` and `ignore` require rationale and fail unless explicitly allowed with `--allow-kind`.
The gate detects missing, stale, duplicate, unreadable, and ambiguous policy inputs and reports the number of
matched acquisition sites as `scope=N`. It audits reviewed enrollment; it does not infer shell data flow or
prove that a command cryptographically verifies downloaded bytes.

```yaml
- id: check-downloaded-asset-enrollment
  args: [--ledger, .quality/assets.json, --candidate-file-regex, '(^|/)(Dockerfile|.*\.sh)$', --download-site-regex, 'https://\S+']
```

### check-vulnerability-exceptions

This opt-in gate applies a tracked exception ledger to a normalized scanner JSON report. The report records
its tool, target, positive `scanned_units`, and findings with primary ID, aliases, subject, blocking status,
and available fixes. It fails new blocking findings, accepted blocking findings that now have a fix, malformed
input, and stale exceptions by default. Use `--stale-exceptions warn` only while deliberately migrating an
existing exception list. Consumers own scanner execution and translate scanner output to the documented
normalized report; the gate reports `scope=N` from `scanned_units`.

```json
{"version":1,"scanned_units":1,"tool":"pip-audit","target":"api","findings":[{"id":"CVE-2026-1","aliases":["GHSA-example"],"subject":"package@1.0","blocking":true,"fixes":["1.1"]}]}
```

Each finding has exactly `id`, `aliases`, `subject`, `blocking`, and `fixes`; identifier and fix lists hold
nonblank strings. An exception is `{ "id", "rationale" }` or additionally exact `tool` and `target` scope.

```yaml
- id: check-vulnerability-exceptions
  args: [--report, .quality/audit.json, --ledger, .quality/vulnerabilities.json, --stale-exceptions, fail]
```

The report has exactly `version`, `scanned_units`, `tool`, `target`, and `findings`; `version` is the integer
`1` and `scanned_units` is positive. The ledger has exactly `version` and `exceptions`. An exception is either
`{"id": "CVE-2026-0001", "rationale": "Reviewed reason."}` or the scoped form
`{"id": "CVE-2026-0001", "rationale": "Reviewed reason.", "tool": "trivy", "target": "api"}`.
`tool` and `target` are optional only as a pair, and every value is a nonblank string. Scoped exceptions apply
only to a report with that exact tool and target; an exact scoped match takes precedence over an unscoped match.
Exception identity is `(id, tool, target)`, so the same vulnerability may have distinct reviewed exceptions for
different scanner targets.

### check-container-image-cves

This opt-in companion policy consumes a tracked image inventory, a consumer-produced normalized image scanner
report, and a tracked image-scoped CVE exception ledger. It fails new fixable HIGH/CRITICAL findings, stale
exceptions, unknown inventory references, incomplete report scope, and malformed input. It deliberately does
not run Docker, pull images, or choose a scanner; a client can use Trivy or another scanner in its own CI.

```json
{"version":1,"scanned_units":1,"scanned_images":["registry.example/api:1"],"findings":[{"id":"CVE-2026-1","image":"registry.example/api:1","severity":"HIGH","package":"openssl","installed":"3.0","fixes":["3.1"]}]}
```

The inventory is `{ "version": 1, "images": [{"id":"api","reference":"registry.example/api:1"}] }`.
Reports must list each enrolled image exactly once. Image exceptions are `{ "id", "image", "rationale" }` and
apply to that CVE/image pair; severity is one of `LOW`, `MEDIUM`, `HIGH`, or `CRITICAL`.

```yaml
- id: check-container-image-cves
  args: [--inventory, .quality/images.json, --report, .quality/image-report.json, --exceptions, .quality/image-exceptions.json]
```

The inventory has exactly `version` and `images`, where every image is an `id`/`reference` object and references
are unique. The report has exactly `version`, `scanned_units`, `scanned_images`, and `findings`.
`scanned_images` is a unique list of image-reference strings whose set must exactly equal the inventory's
references; `scanned_units` must equal that list's length. The count is therefore not trusted as proof that the
whole inventory was scanned.

```json
{"version":1,"scanned_units":2,"scanned_images":["registry.example/api:1","registry.example/worker:1"],"findings":[]}
```

### check-container-image-enrollment

This opt-in gate validates the reviewed graph between every tracked `Dockerfile*` and a tracked
container image inventory. Both `--ledger` and `--inventory` must be tracked; the gate reads every
tracked Dockerfile and fails on unreadable files, zero Dockerfiles, unclassified or stale Dockerfile
entries, missing or orphaned inventory mappings, and mappings to ignored or unknown Dockerfiles.
It does not build or pull images, run Docker, execute a scanner, or create CI evidence.

The version-1 ledger has exactly `version`, `dockerfiles`, and `image_sources`. Every Dockerfile
has one `path` and one classification: `build`, `pull`, or `ignore`; `ignore` also requires a
nonblank `rationale`. Every inventory ID has exactly one source: a `dockerfile` path, or an
`external` source with a nonblank rationale. Each non-ignored Dockerfile must source at least one
inventory ID. The inventory is `{ "version": 1, "images": [{"id":"api","reference":"registry.example/api:1"}] }`;
IDs are unique and nonempty.

```yaml
- id: check-container-image-enrollment
  args: [--ledger, .quality/container-image-enrollment.json, --inventory, .quality/images.json]
```

```json
{"version":1,"dockerfiles":[{"path":"services/api/Dockerfile","classification":"build"},{"path":"examples/Dockerfile","classification":"ignore","rationale":"Documentation-only example."}],"image_sources":[{"id":"api","dockerfile":"services/api/Dockerfile"},{"id":"docs","external":"registry.example/docs:1","rationale":"Published by another service."}]}
```

### check-container-image-immutable-assessment

This opt-in CI policy gate validates consumer-produced immutable assessment evidence. It is published for the
`manual` pre-commit stage and must be invoked by trusted CI, not normal developer commits. Its strict
input boundary is intentional: `--enrollment`, `--inventory`, and `--exceptions` are reviewed,
tracked policy files; `--report` is generated, untracked evidence. Each scan's `raw_evidence.path`
must also name an untracked file under the repository root, and its lowercase SHA-256 must match
the file bytes. Do not commit the generated report or raw scanner output merely to satisfy the
gate. The gate reads and validates evidence only: it does not run Docker, pull or build images, or
execute a scanner.

The gate validates the integrity, freshness, scope, and immutable-identity claims in consumer-produced evidence.
It cannot generically prove arbitrary raw scanner bytes describe the claimed artifact; retain scanner-native
provenance or attestations when that stronger assurance is required.

`--as-of YYYY-MM-DDTHH:MM:SSZ` and positive `--max-age-hours` are required. A report must contain
one fresh scan for every enrolled inventory ID, with no duplicates; a scan timestamp cannot be in
the future or older than the requested age window. The report must be version 2 and contain exactly
`version`, `enrollment_sha256`, `scans`, and `findings`. `enrollment_sha256` must match the exact
tracked enrollment-file bytes, so evidence cannot be replayed after enrollment changes.

Every v2 scan contains exactly `image_id`, immutable `artifact_digest` (`sha256:` plus 64 lowercase
hexadecimal characters), UTC `scanned_at`, and `raw_evidence` (`path` and matching `sha256`). Every
finding contains exactly `id`, `image_id`, `artifact_digest`, `severity`, `package`, `installed`, and
`fixes`; its digest must equal the corresponding scan digest. HIGH and CRITICAL findings with fixes
fail unless handled by a matching exception, and a matching exception for a fixable finding also
fails. The tracked exception ledger is version 2 with exactly `version` and `exceptions`; each
exception has exactly `id`, `image_id`, `artifact_digest`, and nonblank `rationale`. Exception and
finding identity is therefore bound to the immutable artifact, and stale exceptions fail.

```yaml
- id: check-container-image-immutable-assessment
  args:
    - --enrollment
    - .quality/container-image-enrollment.json
    - --inventory
    - .quality/images.json
    - --report
    - .quality-ci/immutable-assessment.json
    - --exceptions
    - .quality/container-image-exceptions.json
    - --as-of
    - "2026-08-28T12:00:00Z"
    - --max-age-hours
    - "24"
```

### check-base-image-eol

This opt-in gate reads every tracked `Dockerfile*` and checks mapped runtime `FROM` stages against a tracked,
consumer-owned lifecycle snapshot. The policy maps image names to lifecycle products and cycle forms, supplies
EOL dates, and requires `--as-of YYYY-MM-DD` for deterministic evaluation. It fails unknown mapped cycles, past-EOL
bases, unreadable Dockerfiles, malformed policy data, and zero recognized runtime bases; bases inside
`--warning-days` are reported as warnings. No network lifecycle API is consulted, so CI cannot pass merely
because a lifecycle lookup failed.

```yaml
- id: check-base-image-eol
  args: [--policy, .quality/base-image-lifecycles.json, --as-of, "2026-08-28", --warning-days, "120"]
```

The policy has exactly `version`, `runtimes`, and `lifecycles`; it has no exception list. Every runtime is an
`image`/`product`/`cycle` object, with nonblank strings and a `cycle` of exactly `major` or `major.minor`.
Runtime image names are unique. Every lifecycle is a unique `product`/`cycle` pair with a nonblank ISO-8601
`eol` date. The gate validates every list item and rejects duplicates rather than ignoring malformed entries.
A mapped runtime image must carry a tag, such as `python:3.11-slim`; `FROM python` is an unknown lifecycle, not
an unscanned stage.

```json
{"version":1,"runtimes":[{"image":"python","product":"python","cycle":"major.minor"}],"lifecycles":[{"product":"python","cycle":"3.11","eol":"2027-10-24"}]}
```

## CI image security

`check-container-image-enrollment`, `check-container-image-immutable-assessment`, and
`check-base-image-eol` are policy evaluators, not an image-scanning platform. Keep the boundary
strict: enrollment policy, inventory, exception ledgers, and lifecycle snapshots are reviewed and
tracked; scanner reports and raw scanner output are generated, untracked CI evidence. Enrollment
proves a two-way mapping: every tracked Dockerfile and inventory ID have a reviewed source. Immutable assessment
proves that fresh evidence is bound to the exact enrollment bytes and resolved artifact digests.
EOL evaluates mapped tracked Dockerfile `FROM` references against a tracked offline lifecycle
snapshot. None of these gates pulls or builds images, runs Docker, invokes a scanner, manages
credentials, or schedules CI; credentials remain consumer-owned. Consumers own that orchestration and must map every runtime base-image
family they intend to govern.

Run Dockerfile enrollment on developer push and in CI. Run image retrieval/build, scanning, report normalization,
CVE policy evaluation, and lifecycle evaluation in a scheduled security workflow; allow authenticated manual or
API-triggered runs for remediation. Monitor outside that workflow and alert when a successful assessment result is
not received within the expected interval. This catches a disabled schedule, a job that never starts, and a red job.

The CI job order is:

1. Run `check-container-image-enrollment` against the tracked ledger and inventory.
2. Use the tracked inventory as the complete target list. Consumer CI resolves each target to an immutable digest or
build result, retaining its Dockerfile/build or registry provenance.
3. Scan each exact resolved artifact with a scanner version pinned by digest or another immutable identity. Retain
raw output as an untracked CI artifact and record its SHA-256.
4. Generate the v2 immutable assessment report, including the enrollment SHA-256, one fresh digest-bound scan per
inventory ID, raw-evidence checksums, and digest-bound findings.
5. Run `check-container-image-immutable-assessment` with an explicit UTC `--as-of` timestamp and a consumer-chosen
`--max-age-hours`.
6. Run `check-base-image-eol` with the tracked lifecycle snapshot and its required explicit `--as-of` date.

The legacy v1 evaluator remains available while consumers migrate: its `scanned_images` exactly equal
the enrolled inventory references; the list must contain no duplicates.
New assessments should use the immutable gate because tag equality alone cannot
prove artifact identity.

The following is a CI-system-neutral job shape for a Linux/POSIX-shell worker. Adapt the YAML keys and the clock
command for the chosen CI provider and runner; a PowerShell or Windows worker needs its shell's equivalent command.

```yaml
image-security:
  runner-image: scanner.example/tool@sha256:REVIEWED_DIGEST
  cache: scanner-database-only
  script:
    - check-container-image-enrollment --ledger .quality/container-image-enrollment.json --inventory .quality/images.json
    - build_or_pull_every_inventory_image
    - scan_and_write_immutable_assessment .quality-ci/immutable-assessment.json raw-scanner-output/
    - AS_OF=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
    - check-container-image-immutable-assessment --enrollment .quality/container-image-enrollment.json --inventory .quality/images.json --report .quality-ci/immutable-assessment.json --exceptions .quality/container-image-exceptions.json --as-of "$AS_OF" --max-age-hours 24
    - check-base-image-eol --policy .quality/base-image-lifecycles.json --as-of "${AS_OF%T*}"
  artifacts:
    when: always
    retention: 7 days
    paths: [raw-scanner-output/, .quality-ci/immutable-assessment.json]
```

Use masked, least-privilege, registry-host-scoped read credentials and do not log their configuration. Cache only
reproducible scanner databases, with a consumer-configured maximum age and verified provenance; refresh or fail when
the database is too old or its source cannot be verified. Do not cache registry credentials, tokens, mutable image
results, or a prior normalized report as current evidence. Bound scanner and build timeouts; a retry can address a
transient failure, but the final failure must remain visible and nonzero.

An unavailable registry, failed authentication, unavailable Docker builder, deferred/unscanned target, scanner
timeout, advisory-database download failure, nonzero scanner exit, empty or malformed output, or incomplete image
coverage is an assessment failure, not a clean result. Retain the raw scanner output and normalized report as
short-lived CI artifacts, especially on failure. Do not commit generated reports unless the repository deliberately
treats them as reviewed snapshots.

Keep exceptions narrow: one CVE identity and one image reference with rationale. Do not use an exception for a
whole image, scanner outage, inaccessible registry, or unscanned target. Re-triage/remove an exception when the
scanner no longer reports it or the image reference changes.

## Migration guidance

Replace repository-owned quality scripts with the corresponding gates, but copy each
consumer's existing paths, baselines, budgets, confidence thresholds, formats, and clone thresholds
into hook arguments. The gate is the common enforcement mechanism; the policy values remain owned by
the consumer repository.

| Existing direct script purpose | Current gate | Preserve in consumer configuration |
| --- | --- | --- |
| Inline comment or docstring policy | `check-inline-comments` | Source paths and baseline policy (`--baseline` or `--no-baseline`) |
| Public dict-parameter policy | `dict-param-check` | Source paths and baseline policy |
| TODO/FIXME/NOTE/HACK/XXX budget | `check-marker-budget` | `--ceiling` and every `--per-file` allowance |
| Python dead-code check | `check-dead-code` | Each `--path`, exclusions, ignored names, and `--min-confidence` |
| Copy-paste duplication check | `check-duplication` | Scope selection, paths, formats, extensions, exclusions, minimum lines/tokens, and threshold |
| Pytest hierarchy and mock restrictions | `check-pytest-describe` and `check-forbidden-mocks` | Test roots, `--factory-location`, and consumer-approved `--condition-infix` values |

Do not migrate scanner invocation, Docker/build commands, credential handling, report normalization,
scheduling, or CI artifact retention into these hooks. Keep that operational orchestration in the
consumer's trusted CI workflow, then pass its generated evidence to the applicable policy gate.
Retain any deployment-manifest image-coverage check until an equivalent consumer-side check proves every deployment
image is represented in the reviewed inventory. The enrollment gate validates declared mappings; it does not parse
consumer-specific deployment configuration.

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

`check-inline-comments`, `dict-param-check`, `check-marker-budget`, and `check-marker-preservation` skip `venv`, `.venv`,
`node_modules`, `__pycache__` and `_generated` anywhere in a path. `_generated` is in that list
because generated output is not written by hand, so a rule about how a human writes a comment
cannot apply to it. `check-dead-code` scans its declared `--path` scope, subject to Vulture
`--exclude` patterns.

`check-inline-comments` and `dict-param-check` support repeatable `--skip-dir NAME` and
`--src-dir NAME`. Both add to the defaults: `--src-dir` widens source-directory discovery, while
`--skip-dir` adds an explicit excluded directory name. Neither option replaces the built-in
defaults, so a consumer cannot silently clear the standard source or skip sets. To scan less,
name the path you want.

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
