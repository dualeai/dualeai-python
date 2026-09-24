# SDK Test Suite

## Test Types

| Type | Command | Purpose |
|------|---------|---------|
| Static | `make test-static` | Linting (ruff), type checking (ty), dead code (vulture) |
| Unit | `make test-unit` | Fast, isolated tests with mocked dependencies |
| Integration | `make test-int` | Signed uploads against local S3 and protected HTTP against local TLS |
| Bench | `make test-bench` | CodSpeed performance benchmarks |

Run static, unit, and integration checks with `make test`.

## Structure

```text
tests/
  conftest.py              # Shared fixtures (minimal_mock_sdk, config_factory)
  mocks/                   # Network-boundary mocks (MockHTTPTransport)
  helpers/                 # Assertion utilities
  fixtures/                # Static test data
  test_*.py                # Unit tests (@pytest.mark.unit)
  benchmarks/              # CodSpeed benchmarks (@pytest.mark.benchmark)
    test_bench_sdk.py      # Config, SSE parsing, cache, utilities
  test_attachments.py      # Upload unit tests and a local HTTP integration test
  test_attachments_s3.py   # Local S3 integration test (@pytest.mark.integration)
  test_http_transport_v3.py # Local TLS integration and transport unit tests
```

## Unit Tests

All unit tests use `@pytest.mark.unit`. SDK-facing tests normally use the
`minimal_mock_sdk` fixture, which keeps the real SDK and mocks only the HTTP
transport. Pure contract tests exercise schema and validation transforms
without SDK state or transport.

```python
import pytest

from dualeai import DualeAISDK, ask
from dualeai.models.bridge import BridgeTaskCreateRequest
from tests.mocks.mock_http import require_mock_http_transport


@pytest.mark.unit
async def test_action_reaches_the_task_request(minimal_mock_sdk: DualeAISDK) -> None:
    await ask(action="Test", sdk=minimal_mock_sdk)

    [call] = require_mock_http_transport(minimal_mock_sdk).get_requests()
    request = call["request"]
    assert isinstance(request, BridgeTaskCreateRequest)
    assert request.action_prompt == "Test"
```

## Benchmarks

CodSpeed benchmarks cover Pydantic validation, SSE checksums, cache-key generation,
JSON serialization, and string truncation.

```bash
make test-bench  # Runs via --codspeed, serial execution, no coverage
```

## Integration Tests

The integration suite checks signed uploads against local S3, bounded part
scheduling against a loopback object store, and protected Bridge and Library
calls against a local TLS endpoint. The enforcing tests are
`test_public_upload_preserves_bytes_and_document_metadata`,
`test_public_upload_preserves_true_multipart_bytes_and_opaque_etags`,
`test_many_parts_keep_pending_upload_tasks_bounded`, and
`test_real_v3_tls_boundary_checks_both_services_and_live_task_block`. These
tests do not contact external services.
