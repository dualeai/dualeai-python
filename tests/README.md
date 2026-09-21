# SDK Test Suite

## Test Types

| Type | Command | Purpose |
|------|---------|---------|
| Static | `make test-static` | Linting (ruff), type checking (ty), dead code (vulture) |
| Unit | `make test-unit` | Fast, isolated tests with mocked dependencies |
| Integration | `make test-int` | Multipart upload against the local S3 test server |
| Bench | `make test-bench` | CodSpeed performance benchmarks |

Run static, unit, and integration checks with `make test`.

## Structure

```text
tests/
  conftest.py              # Shared fixtures (minimal_mock_sdk, config_factory)
  mocks/                   # Network-boundary mocks (MockHTTPTransport)
  helpers/                 # Assertion utilities
  fixtures/                # Test data (mock bridge server, controlled timing)
  test_*.py                # Unit tests (@pytest.mark.unit)
  benchmarks/              # CodSpeed benchmarks (@pytest.mark.benchmark)
    test_bench_sdk.py      # Config, SSE parsing, cache, utilities
  test_attachments_s3.py   # Local S3 integration test (@pytest.mark.integration)
```

## Unit Tests

All unit tests use `@pytest.mark.unit`. SDK-facing tests normally use the
`minimal_mock_sdk` fixture, which keeps the real SDK and mocks only the HTTP
transport. Pure contract tests exercise schema and validation transforms
without SDK state or transport.

```python
@pytest.mark.unit
class TestMyFeature:
    async def test_something(self, minimal_mock_sdk: DualeAISDK) -> None:
        response = await ask(action="Test", sdk=minimal_mock_sdk)
```

## Benchmarks

CodSpeed benchmarks cover hot paths: Pydantic model validation, SSE checksum, cache key generation, JSON serialization, string truncation.

```bash
make test-bench  # Runs via --codspeed, serial execution, no coverage
```

## Integration Tests

The integration suite uploads single-part and multipart files through presigned
URLs against a local S3 test server. It does not contact external services.
