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
  helpers/                 # Bridge round-trip helpers
  fixtures/                # Static test data
  test_*.py                # Unit (@pytest.mark.unit) and local integration (@pytest.mark.integration) tests
  benchmarks/              # CodSpeed benchmarks (@pytest.mark.benchmark)
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
calls against a local TLS endpoint. These tests do not contact external
services.

## Verify Task stream integrity

`HTTPTransport` passes authenticated `hpke-http` SSE blocks to the event parser.
The [protected response contract](https://github.com/dualeai/hpke-http/blob/v4.0.1/PROTOCOL.md#protected-response)
authenticates each complete block, including its cursor, event type, and data,
before delivery. The SDK neither requires nor validates application CRC32
comments: they add no required protection inside this authenticated transport
and provide no keyed authentication. The parser still validates JSON, event
schemas, matching event types, and size limits.

- The parser accepts a CRC-free typed event, and rejects malformed payloads and
  oversized events while handling cursors and ordinary SSE comments.
- A CRC-free Task result reaches the caller over real TLS and HPKE.
- An altered encrypted SSE record is rejected before event delivery, and the
  authentication failure is not retried.
- After a stream ends without END, the Task resumes from the last delivered
  cursor. Per-event authentication does not prove full transport completion,
  which requires END and outer EOF.
- The SDK returns at an application terminal without reading or retrying past
  the result, so it does not drain the transport.
