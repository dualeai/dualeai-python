# Contributing

```console
$ make install     # create the environment and install development dependencies
$ make test        # static checks and functional tests
$ make lint        # ruff format, ruff check --fix, ty, and vulture
```

`make test` and `make lint` must be green before a pull request. CI runs
`make test-static` and `make test-func` — the same checks, not the same targets: it
cannot run `make lint`, which rewrites files rather than reporting on them.

**Branch from `develop` and open the pull request against `develop`.** `main` is the
release branch: it is what the publish workflow reads and what `git describe` tags.
Work lands on `develop` first and reaches `main` when a release is cut.

Dependabot version-update pull requests follow the same rule. GitHub always opens
Dependabot security-update pull requests against the default branch, so those target
`main`; the Test workflow also covers that branch.

**Commit subjects are [Conventional Commits](https://www.conventionalcommits.org)** —
`feat:`, `fix:`, `docs:`, `test:`, `ci:`, `refactor:` — because the release notes are
written from the log. No CLA and no DCO sign-off; the Apache-2.0 license on the
repository covers the contribution.

## Rules that are not obvious, and are not negotiable

**A test must fail if you break the code it covers.** Before adding one, remove the
implementation in your head: if the test still passes, the assertion is too weak.
Write the failing test first, reproduce the defect, and then fix it.

**No `pytest.skip`.** A missing dependency means a broken environment. Use
`xfail(strict=True)` only for a known defect that remains intentionally unfixed.

**Use the registered test marker that owns the proof.** `unit` covers mocked boundaries;
`integration` covers local services owned by the suite; `benchmark` belongs under
`tests/benchmarks/` and runs through `make test-bench`. Tests must not require a Duale AI
account, live credentials, or external network access. `pytest-socket` enforces the
network boundary while allowing loopback fixtures.

**Use synthetic data.** Never put a real API token, Tenant or Agent identifier,
customer record, prompt, Tool argument, signed upload URL, or captured response in a
test, issue, log, or fixture.

**Do not hand-edit `src/dualeai/models/`.** Those modules are generated from the public
API contract. Open an issue that describes the contract change; maintainers update the
generated set.

**Every lint suppression carries a reason.** Fix the issue instead of disabling a
rule for the repository. A local suppression is acceptable only when its comment says
why the rule does not apply.

## Benchmarks

`make test-bench` runs the CodSpeed benchmarks. A correctness assertion belongs in the
normal suite; a benchmark measures a supported operation and must not replace that
assertion.

## Cutting a release

A GitHub Release publishes. `git push --tags` does not. The release workflow builds and
validates one wheel and source distribution, uploads them to TestPyPI and then PyPI,
and attaches those same artifacts and SBOMs to the Release.

## Writing

No marketing copy or superlatives. State behavior that exists in the current package.
When prose names an invariant, name the test that enforces it or state that no
automated test enforces it.

Prefer the shorter word, cut what can be cut, and use `Duale AI` for the company and
`dualeai` for the package and repository identifiers.

## Security

Do not open a public issue for a suspected vulnerability. See [SECURITY.md](SECURITY.md).
