# Working in this repository

`dualeai-python` is the public Python SDK for submitting Tasks to Duale AI and
hosting Customer Tools. The distribution and import name are both `dualeai`.

## Commands

```console
$ make install     # create the environment and install development dependencies
$ make test        # static checks and functional tests
$ make lint        # format, fix lint findings, type-check, and run Vulture
```

Run `make lint` and `make test-static` before a commit. CI runs
`make test-static` and `make test-func`; `make lint` rewrites files and therefore
does not run in CI.

## Searching the code

Use `seek`, not `grep` or `rg`. It ranks by relevance, groups by file, labels symbol
definitions, and prints context. It searches this repository by default; paths after
the query narrow the search.

```console
$ seek [flags] '<query>' [path...]
```

Filters stay inside one quoted query: `sym:Name` finds a definition, `file:x` and
`-file:x` filter paths, `lang:python` filters language, and `content:<regex>` searches
file contents. Flags precede the query. Use `-n` and `-m` to bound large results.

When delegating, repeat this rule: use `seek 'pattern' [path...]` and never use
`grep`, `rg`, `find`, or `git grep`.

If `seek` is missing:

```console
$ curl -sSfL https://raw.githubusercontent.com/dualeai/seek/main/install.sh | sh
```

## Keep the public repository self-contained

Do not add names, paths, commit identifiers, branches, tools, or architecture from a
non-public repository. This rule applies to source, tests, comments, documentation,
workflow text, commit messages, release notes, and generated artifacts.

Files under `src/dualeai/models/` are generated. Do not edit them by hand. A public
change that needs a model update must identify the contract change; maintainers update
the generated set.

Never commit credentials, real Tenant or Agent identifiers, customer data, prompts,
Tool arguments, signed upload URLs, or captured production responses. Tests and issue
reproductions use synthetic values.

## Tests

A test must fail when the implementation it covers is broken. Reproduce a defect with
a failing test before fixing it. Use the smallest assertion that proves the public
contract.

The suite has three markers:

- `unit` for tests with mocked dependencies;
- `integration` for local multi-component tests; and
- `benchmark` for CodSpeed benchmarks run through `make test-bench`.

Tests must not require a Duale AI account, live credentials, or external network
access. `pytest-socket` blocks external egress. Integration fixtures may use loopback
services owned by the test process.

Do not use `pytest.skip` to hide a missing dependency or broken environment. Use
`xfail(strict=True)` only for a known defect that remains intentionally unfixed.

Every lint suppression needs a nearby reason. Fix static-analysis findings instead of
disabling a rule for the repository.

## Writing

Use plain, specific language. Do not add marketing claims or superlatives. A statement
about package behavior must be true in the current release and name the test that
enforces it, or state that no automated test enforces it.

Use `Duale AI` in text, `dualeai` in URLs and package identifiers, and `DUALEAI_*`
for this SDK's environment variables.

## GitHub Actions must be SHA-pinned

Never reference an action by tag. Resolve the release tag to a commit SHA and keep the
tag in a trailing comment:

```yaml
- uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
```

Use the GitHub `commits/<tag>` API when resolving a pin. It handles lightweight and
annotated tags; an annotated tag object's SHA is not the commit to execute.

## Releases

A published GitHub Release triggers publication. Pushing a tag alone does not publish.
The release workflow builds one wheel and source distribution, validates them, creates
SBOMs and provenance, uploads first to TestPyPI and then PyPI through Trusted
Publishing, and finally attaches the same artifacts to the GitHub Release.

Do not add a long-lived PyPI token or another publication path.

## Security

Do not open a public issue for a suspected vulnerability. Follow
[SECURITY.md](SECURITY.md).
