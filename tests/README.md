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

CodSpeed benchmarks cover Pydantic validation, SSE parsing, cache-key generation,
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
`test_real_v3_tls_boundary_reuses_discovery_per_service`. These
tests do not contact external services.

## Verify Task stream integrity

`HTTPTransport` passes authenticated `hpke-http` SSE blocks to the event parser.
The [protected response contract](https://github.com/dualeai/hpke-http/blob/v4.0.1/PROTOCOL.md#protected-response)
authenticates each complete block, including its cursor, event type, and data,
before delivery. The SDK neither requires nor validates application CRC32
comments: they add no required protection inside this authenticated transport
and provide no keyed authentication. The parser still validates JSON, event
schemas, matching event types, and size limits.

- `test_sse_parser.py::TestSSEParserEvents::test_parser_accepts_the_frozen_content_delta_wire_event`
  accepts a CRC-free typed event. The parser suite also covers malformed payloads,
  limits, cursor handling, and ordinary SSE comments.
- `test_http_transport_v3.py::test_real_v3_tls_boundary_reuses_discovery_per_service`
  delivers a CRC-free Task result over real TLS and HPKE.
- `test_http_transport_v3.py::test_real_tls_task_recovery[sse_integrity]`
  rejects an altered encrypted SSE record before event delivery and does not retry
  the authentication failure.

`test_task_recovers_from_missing_end_with_the_last_delivered_cursor` checks the
resume cursor after an interrupted stream. Per-event authentication does not
prove full transport completion, which requires END and outer EOF.
`test_task_terminal_does_not_read_or_retry_past_the_result` pins the SDK's return
at an application terminal without draining the transport. Both tests are in
`test_http_transport_v3.py`.
