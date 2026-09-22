# Security policy

**Report a vulnerability privately, either way:**

- [`/security/advisories/new`](https://github.com/dualeai/dualeai-python/security/advisories/new)
  — private vulnerability reporting is the preferred route.
- **Email `security@duale.ai`** if you would rather not use GitHub.

**Do not open a public issue for a suspected vulnerability.** The timeline below runs
from whichever channel you use.

## Response timeline

- **48 hours**: initial acknowledgment
- **7 days**: assessment and action plan
- **90 days**: target for fix and coordinated disclosure

We will keep you informed of progress and will credit reporters who wish to be
credited.

### Coordinated disclosure

Duale AI is not a CVE Numbering Authority. Where a CVE identifier is warranted, we
request one through GitHub, which is a CNA, using the repository's Security Advisory
workflow. Advisories published this way propagate to Dependabot alerts for downstream
users.

### Third-party dependencies

Report a vulnerability in a dependency to that project. We still want to hear about it
when the issue affects users of this SDK, so that we can pin, patch, or advise.

## Supported versions

The public API is explicitly unstable while the major version is 0. Security fixes are
released against the latest `0.x` only. The supported runtimes are CPython 3.10 through
3.14.

## Scope

### In scope

- Exposure of API tokens, signed upload URLs, customer data, prompts, Tool arguments,
  or decrypted Platform responses through SDK-owned logs, telemetry, exceptions,
  caches, or request routing.
- Cross-credential or cross-Tenant cache access or deletion caused by SDK-owned key
  construction or cache clearing.
- A request-signing, authentication, or encryption defect in SDK-owned transport code
  that weakens its documented wire contract.
- Unsafe parsing of Platform responses or Tool inputs that causes code execution,
  uncontrolled filesystem or network access, or material CPU or memory exhaustion.
- A release artifact that does not correspond to this repository or includes files
  outside the documented Python package.

### Out of scope

- A service-side authorization or availability defect that reproduces without this
  SDK. Report it through the Duale AI security channel and include the affected API.
- Code executed by a Customer Tool. The SDK calls registered Tool code, but the Tool
  owner controls that code and its external effects.
- Credentials, network clients, exporters, or callbacks supplied by the application.
  Report an SDK defect when the SDK exposes or misroutes those values.
- A vulnerability that exists only in a third-party dependency and does not affect an
  SDK execution path. Report that case upstream.

## Security properties of this SDK

- **Redis cache clearing is scoped to one API-token fingerprint.** `DualeAISDK` hashes its
  API token for the `dualeai:sdk:{token_fingerprint}:` prefix; it does not use the
  configured Library Tenant ID. `clear()` scans and deletes only that prefix, and
  rotating the API token selects a new namespace. The SDK does not recognize or
  migrate older key formats.
  <!-- Evidence: tests/test_cache.py::TestRedisCacheCommands holds the caller-supplied namespace and Redis command boundary. src/dualeai/cache.py states that no focused automated test covers the SDK's token-to-namespace selection or token-rotation behavior. -->
- **Redis credentials stay out of initialization logs.** Both `redis://` and
  `rediss://` reach redis-py, while the connection URL and its userinfo do not reach
  the initialization event.
  <!-- Evidence: tests/test_cache.py::TestRedisCacheCommands::test_tls_url_reaches_redis_without_reaching_logs; tests/test_config.py::TestConfigValidation::test_redis_url_accepts_plain_and_tls_schemes. -->
- **Tests use no external network.** The default pytest configuration enables
  `pytest-socket`; only loopback hosts used by test-owned services are allowed. CI
  runs the same unit and local integration suites without a Duale AI credential.
  <!-- Evidence: pyproject.toml configures --disable-socket with an explicit loopback allowlist; .github/workflows/test.yml runs make test-static and make test-func. -->
- **Generated models reject unknown wire fields.** The generated Pydantic contracts
  are committed and reviewed as package source. Do not report an expected validation
  error as code execution; report any input that bypasses the declared model and
  reaches a privileged SDK action.
  <!-- Evidence: tests/test_sse_parser.py::TestSSEParserEdges::test_parse_sse_rejects_added_fields_on_a_known_event; tests/test_sse_parser.py::TestSSEParserEdges::test_parse_sse_rejects_added_fields_in_a_known_nested_result. -->
- **Customer Tool exception text is sensitive.** Uncaught exception messages can be
  presented to a model provider. Tool authors must keep credentials and personal data
  out of exception text.
  <!-- Evidence: tests/test_agent_lifecycle.py::test_registered_tool_error_is_module_class_prefixed_and_bounded; tests/test_agent_lifecycle.py::test_tool_error_transform_redacts_model_facing_message. -->

## Supply chain

The release workflow uses PyPI Trusted Publishing (OIDC); no long-lived PyPI API token
is configured. It builds a wheel and source distribution once, checks both with
Twine, generates CycloneDX and SPDX SBOMs, and creates GitHub build provenance before
publishing the same artifacts through TestPyPI and PyPI.

Verify a downloaded release artifact:

```bash
# artifact -> source commit (GitHub build provenance)
gh attestation verify ./dualeai-<version>-py3-none-any.whl \
  -R dualeai/dualeai-python

# artifact -> PyPI publisher (PEP 740)
pypi-attestations verify pypi \
  --repository https://github.com/dualeai/dualeai-python \
  pypi:dualeai-<version>-py3-none-any.whl
```

An attestation identifies where a package came from. It does not assess the package's
behavior. Neither `pip` nor `uv` requires this verification automatically.
