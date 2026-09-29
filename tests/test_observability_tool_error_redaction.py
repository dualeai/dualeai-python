"""The SDK's own telemetry must not export a customer tool's raw exception.

A customer tool (or activity) may raise an exception whose message embeds a
secret. ``error_transform`` keeps that off the wire to the model, but the SDK's
OTel span, left on ``start_as_current_span``'s defaults, would record the raw
``exception.message``/``exception.stacktrace`` and an error-status description
carrying ``str(exc)`` — then export it off-box. These tests drive the two spans
that wrap customer code and assert the exported span hides the exception body
while keeping a type-only error signal.

Isolation: ``span_exporter`` supplies an in-memory tracer provider before SDK
construction, restores provider lookups and closes its provider at teardown.
"""

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from opentelemetry.metrics import NoOpMeterProvider
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from dualeai import DualeAIConfig, DualeAISDK, tool
from dualeai.cache import MockCacheBackend
from dualeai.models.bridge import BridgeToolUseResponse
from dualeai.observability import SDKObservability
from tests.mocks.mock_http import MockHTTPTransport


@pytest.fixture
def span_exporter() -> Iterator[InMemorySpanExporter]:
    """Capture real spans without global providers or unrelated metric exports."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    meter_provider = NoOpMeterProvider()
    with (
        patch("dualeai.observability.trace.get_tracer_provider", return_value=provider),
        patch("dualeai.observability.metrics.get_meter_provider", return_value=meter_provider),
        patch("dualeai.observability.metrics.get_meter", side_effect=meter_provider.get_meter),
        patch.object(SDKObservability, "_setup_auto_instrumentation"),
    ):
        try:
            yield exporter
        finally:
            provider.shutdown()


def _enabled_sdk(transport: MockHTTPTransport | None = None) -> DualeAISDK:
    """Create the SDK after the test's local providers are available."""
    config = DualeAIConfig.model_validate(
        {"token": "dualeai_test_token_12345_padded_to_32bytes", "agent_id": "agent-test-rc3"}
    )
    config.observability.endpoint = "http://localhost:4318"
    config.observability.token = "test-token"
    return DualeAISDK(config=config, transport=transport, auto_start=False)


def _one_span(exporter: InMemorySpanExporter, name: str) -> ReadableSpan:
    spans = [span for span in exporter.get_finished_spans() if span.name == name]
    assert len(spans) == 1, f"expected one {name!r} span, got {[s.name for s in exporter.get_finished_spans()]}"
    return spans[0]


def _assert_exception_body_not_exported(span: ReadableSpan, secret: str) -> None:
    # Sweep every event attribute value, not just exception.message/stacktrace, so
    # a regression that records the secret under any key is caught.
    for event in span.events:
        for value in (event.attributes or {}).values():
            assert secret not in str(value), f"secret leaked via span event {event.name!r}"
    assert secret not in (span.status.description or ""), "secret leaked via status.description"


@pytest.mark.unit
class TestCustomerToolExceptionNotExported:
    async def test_execute_tool_span_omits_customer_exception(
        self, mock_http_transport: MockHTTPTransport, span_exporter: InMemorySpanExporter
    ) -> None:
        sdk = _enabled_sdk(transport=mock_http_transport)
        await sdk._ensure_events_client()
        secret = "postgresql://pricing:s3cr3t-rc3-tool@db.internal/prices"

        @tool(sdk=sdk, description="raises with a secret", timeout=timedelta(seconds=5))
        async def leaky_tool() -> dict[str, str]:
            raise RuntimeError(secret)

        tool_use = BridgeToolUseResponse(
            type="tool.use",
            timestamp=datetime.now(timezone.utc),
            tool_call_id="call-rc3",
            name="leaky_tool",
            input={},
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=5),
        )
        mock_http_transport.inject_event("task-rc3", mock_http_transport.create_task_completed_event())

        try:
            # _execute_tool_use swallows the exception into a BridgeToolResultError.
            await sdk._execute_tool_use("task-rc3", tool_use)

            span = _one_span(span_exporter, "dualeai.sdk.execute_tool leaky_tool")
            _assert_exception_body_not_exported(span, secret)
            # Failure is still visible as a signal — type only, no message.
            assert span.status.status_code is StatusCode.ERROR
            assert span.status.description == "RuntimeError"
        finally:
            await sdk.cleanup()

    async def test_activity_execution_span_omits_customer_exception(self, span_exporter: InMemorySpanExporter) -> None:
        sdk = _enabled_sdk()
        sdk.cache = MockCacheBackend(tenant_id="rc3")
        secret = "postgresql://pricing:s3cr3t-rc3-activity@db.internal/prices"

        def leaky_activity() -> int:
            raise RuntimeError(secret)

        try:
            with pytest.raises(RuntimeError):
                await sdk.execute_activity(leaky_activity, cache_ttl=timedelta(minutes=1), max_retries=0)

            span = _one_span(span_exporter, "dualeai.sdk.activity_execution")
            _assert_exception_body_not_exported(span, secret)
            assert span.status.status_code is StatusCode.ERROR
            assert span.status.description == "RuntimeError"
        finally:
            await sdk.cleanup()
