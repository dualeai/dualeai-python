"""Observability unit tests through SDK operations.

In-memory instruments and dependency spies expose telemetry produced by
``ask``, ``execute_activity``, and ``get_health_status``.
"""

import asyncio
from collections.abc import Callable, Iterator
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from opentelemetry import trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader, Sum
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import ProxyTracerProvider

from dualeai import DualeAIConfig, DualeAISDK
from dualeai.cache import MockCacheBackend
from dualeai.events.http_transport import HTTPTransportConnectionError
from dualeai.exceptions import DualeAIConnectionError, DualeAIError
from dualeai.observability import SDKObservability
from dualeai.orchestrator import ask
from tests.helpers.lifecycle import inject_llm_completion
from tests.mocks.mock_http import MockHTTPTransport


@pytest.mark.unit
class TestUnitObservabilityFeature:
    """Observability features exercised through real SDK code paths."""

    @pytest.fixture
    def isolated_otel_providers(self) -> Iterator[InMemoryMetricReader]:
        """Own the SDK-created providers without installing them process-wide."""
        tracers: list[TracerProvider] = []
        meters: list[MeterProvider] = []
        reader = InMemoryMetricReader()

        def get_meter(name: str):
            return meters[0].get_meter(name)

        with (
            patch(
                "dualeai.observability.trace.get_tracer_provider",
                side_effect=lambda: tracers[0] if tracers else ProxyTracerProvider(),
            ),
            patch("dualeai.observability.trace.set_tracer_provider", side_effect=tracers.append),
            patch(
                "dualeai.observability.metrics.get_meter_provider",
                side_effect=lambda: meters[0] if meters else None,
            ),
            patch("dualeai.observability.metrics.set_meter_provider", side_effect=meters.append),
            patch("dualeai.observability.metrics.get_meter", side_effect=get_meter),
            patch("dualeai.observability.OTLPSpanExporter", return_value=InMemorySpanExporter()),
            patch("dualeai.observability.OTLPMetricExporter"),
            patch("dualeai.observability.PeriodicExportingMetricReader", return_value=reader),
            patch.object(SDKObservability, "_setup_auto_instrumentation"),
        ):
            try:
                yield reader
            finally:
                for provider in [*tracers, *meters]:
                    provider.shutdown()

    async def test_stream_failure_survives_metric_views_as_a_local_outcome(
        self,
        minimal_mock_sdk: DualeAISDK,
        mock_transport: MockHTTPTransport,
        observability_config_factory,
        isolated_otel_providers: InMemoryMetricReader,
    ) -> None:
        """Read the real instruments after the SDK's production attribute filter."""
        previous = minimal_mock_sdk.observability
        minimal_mock_sdk._observability = SDKObservability(observability_config_factory())
        try:
            mock_transport.fail_next_task(HTTPTransportConnectionError("stream lost"))
            lost = await ask(action="synthetic", request_id="metric-lost", sdk=minimal_mock_sdk)
            with pytest.raises(DualeAIConnectionError):
                await lost.model()
            mock_transport.inject_event("metric-done", mock_transport.create_task_completed_event())
            done = await ask(action="synthetic", request_id="metric-done", sdk=minimal_mock_sdk)
            assert await done.model() == "Task completed"
            exported = isolated_otel_providers.get_metrics_data()
            assert exported is not None
            outcomes = {
                (point.attributes["operation"], point.attributes["status"]): point.value
                for resource in exported.resource_metrics
                for scope in resource.scope_metrics
                for metric in scope.metrics
                if metric.name == "dualeai.sdk.operations" and isinstance(metric.data, Sum)
                for point in metric.data.data_points
                if point.attributes is not None
            }
            assert outcomes[("task_stream", "failed")] == 1
            assert outcomes[("task_completion", "success")] == 1
            assert ("task_completion", "failed") not in outcomes
        finally:
            minimal_mock_sdk._observability = previous

    @pytest.fixture
    def observability_config_factory(self):
        """Config factory that turns observability on (endpoint + token set)."""

        def _create(**overrides: object) -> DualeAIConfig:
            if "token" not in overrides:
                overrides["token"] = "dualeai_test_token_12345_padded_to_32bytes"
            config = DualeAIConfig.model_validate(overrides)
            config.observability.endpoint = "http://localhost:4318"
            config.observability.token = "test-token"
            return config

        return _create

    @pytest.mark.usefixtures("isolated_otel_providers")
    def test_exporters_receive_per_signal_endpoints(
        self, observability_config_factory: Callable[..., DualeAIConfig]
    ) -> None:
        """Span and metric exporters are wired to their per-signal OTLP paths.

        The OTLP/HTTP exporters use an explicitly passed endpoint AS-IS
        (no path appended — verified against the installed 1.41.0 and 1.44.0
        source), so the SDK must hand each exporter its own ``/v1/<signal>``
        URL, never the bare base.
        """
        captured: dict[str, str] = {}

        def _capture(signal: str) -> Callable[..., MagicMock]:
            def _init(*, endpoint: str, headers: dict[str, str]) -> MagicMock:
                captured[signal] = endpoint
                return MagicMock()

            return _init

        with (
            patch("dualeai.observability.OTLPSpanExporter", side_effect=_capture("traces")),
            patch("dualeai.observability.OTLPMetricExporter", side_effect=_capture("metrics")),
        ):
            SDKObservability(config=observability_config_factory())

        assert captured["traces"] == "http://localhost:4318/v1/traces"
        assert captured["metrics"] == "http://localhost:4318/v1/metrics"

    @pytest.mark.usefixtures("isolated_otel_providers")
    async def test_tracing_injects_context(self, observability_config_factory: Callable[..., DualeAIConfig]) -> None:
        """``trace_operation`` opens a recording span with valid context."""
        sdk = DualeAISDK(config=observability_config_factory(), auto_start=False)

        try:
            assert sdk.observability.enabled is True

            with sdk.observability.trace_operation("test_operation", test_attr="test_value") as span:
                assert span is not None
                current_span = trace.get_current_span()
                assert current_span.is_recording()
                assert current_span.get_span_context().is_valid
        finally:
            await sdk.cleanup()

    async def test_observability_optional_when_disabled(self, config_factory: Callable[..., DualeAIConfig]) -> None:
        """SDK is fully functional when observability is disabled."""
        sdk = DualeAISDK(config=config_factory(), auto_start=False)

        try:
            assert sdk.observability.enabled is False
            # Calls are no-ops, must not raise.
            sdk.observability.task_submitted(task_type="test")
            sdk.observability.task_completed(duration=1.0)
            sdk.observability.task_failed()

            health = sdk.get_health_status()
            assert health["status"] in ["healthy", "unhealthy"]

            # Tracing returns None when disabled.
            with sdk.observability.trace_operation("test") as span:
                assert span is None
        finally:
            await sdk.cleanup()

    @pytest.mark.usefixtures("isolated_otel_providers")
    async def test_observability_configuration_from_config(
        self, observability_config_factory: Callable[..., DualeAIConfig]
    ) -> None:
        """SDK observability inherits endpoint + token from config."""
        config = observability_config_factory()
        sdk = DualeAISDK(config=config, auto_start=False)

        try:
            assert sdk.observability is not None
            assert sdk.observability.config is config
            assert sdk.observability.enabled is True
            assert sdk.observability.service_name == "dualeai-sdk"
            resource = sdk.observability._create_otel_resource()
            assert resource.attributes["service.namespace"] == "dualeai"
            assert resource.attributes["telemetry.distro.name"] == "dualeai-python"
        finally:
            await sdk.cleanup()

    @pytest.mark.usefixtures("isolated_otel_providers")
    async def test_observability_context_propagation(
        self, observability_config_factory: Callable[..., DualeAIConfig]
    ) -> None:
        """Child spans inherit the parent trace_id."""
        sdk = DualeAISDK(config=observability_config_factory(), auto_start=False)

        try:
            with sdk.observability.trace_operation("parent_operation") as parent_span:
                assert parent_span is not None
                assert parent_span.is_recording()
                parent_trace_id = parent_span.get_span_context().trace_id

                with sdk.observability.trace_operation("child_operation") as child_span:
                    assert child_span is not None
                    assert child_span.is_recording()
                    assert parent_trace_id == child_span.get_span_context().trace_id
        finally:
            await sdk.cleanup()

    async def test_observability_disabled_by_default(self, config_factory: Callable[..., DualeAIConfig]) -> None:
        """Observability is disabled when endpoint/token are not set."""
        sdk = DualeAISDK(config=config_factory(), auto_start=False)

        try:
            assert sdk.observability is not None
            assert sdk.observability.enabled is False

            sdk.observability.task_submitted(task_type="test")
            sdk.observability.cache_hit(cache_type="redis")

            with sdk.observability.trace_operation("test") as span:
                assert span is None
        finally:
            await sdk.cleanup()

    @pytest.mark.usefixtures("isolated_otel_providers")
    async def test_observability_safe_when_errors_occur(
        self, observability_config_factory: Callable[..., DualeAIConfig]
    ) -> None:
        """The ``safe_observability`` wrapper swallows internal errors."""
        sdk = DualeAISDK(config=observability_config_factory(), auto_start=False)

        try:

            def failing_record(*args: object, **kwargs: object) -> None:
                raise RuntimeError("Test observability error")

            # Patch the PRIVATE recording seam every convenience method funnels
            # through — task_submitted calls _record_operation, so the failure
            # is on the executed path and @safe_observability must swallow it.
            with patch.object(sdk._observability, "_record_operation", new=failing_record):
                sdk.observability.task_submitted(task_type="test")  # must not raise

                # Health reporting also records metrics; it must survive a
                # failing observability backend too.
                health = sdk.get_health_status()

            assert health["status"] in ("healthy", "unhealthy")
        finally:
            await sdk.cleanup()


@pytest.mark.unit
class TestUnitObservabilityWiring:
    """Spies on the observability instance — driven by REAL SDK code paths.

    Each test calls a public SDK API (``ask``, ``execute_activity``,
    ``get_health_status``) and asserts that the production code
    invoked the relevant observability method. The spy is the
    boundary; production code does the calling.
    """

    async def test_task_completion_invokes_task_completed_metric(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """A successful task end-to-end fires ``task_completed`` with duration."""
        task_id = "obs-task-completed"
        inject_llm_completion(mock_transport, task_id, completion="ok")

        with patch.object(minimal_mock_sdk._observability, "task_completed") as spy:
            response = await ask(action="hello", request_id=task_id, sdk=minimal_mock_sdk)
            await response.model()
            # Yield so the task's done callback runs.
            await asyncio.sleep(0)

        spy.assert_called_once()
        kwargs = spy.call_args.kwargs
        assert kwargs["task_type"] == "completion"
        assert kwargs["duration"] >= 0.0

    async def test_continuation_completion_uses_continuation_metric_label(
        self,
        minimal_mock_sdk: DualeAISDK,
        mock_transport: MockHTTPTransport,
    ) -> None:
        """The child runner labels a successful continuation separately from a root task."""
        parent_task_id = "parent-task-123"
        inject_llm_completion(mock_transport, parent_task_id, completion="parent")
        parent = await ask(action="Start", request_id=parent_task_id, sdk=minimal_mock_sdk)
        await parent.model()

        with patch.object(minimal_mock_sdk._observability, "task_completed") as spy:
            response = await parent.next(message="Continue")
            mock_transport.inject_event(response.task_id, mock_transport.create_task_completed_event())
            await response.model()
            await asyncio.sleep(0)

        spy.assert_called_once()
        kwargs = spy.call_args.kwargs
        assert kwargs["task_type"] == "continuation"
        assert kwargs["duration"] >= 0.0

    async def test_continuation_error_uses_continuation_metric_label(
        self,
        minimal_mock_sdk: DualeAISDK,
        mock_transport: MockHTTPTransport,
    ) -> None:
        """The child runner labels a failed continuation separately from a root task."""
        parent_task_id = "parent-task-123"
        inject_llm_completion(mock_transport, parent_task_id, completion="parent")
        parent = await ask(action="Start", request_id=parent_task_id, sdk=minimal_mock_sdk)
        await parent.model()

        with patch.object(minimal_mock_sdk._observability, "task_failed") as spy:
            response = await parent.next(message="Continue")
            mock_transport.inject_event(
                response.task_id,
                mock_transport.create_task_error_event("Child failed", error_code="CHILD_FAILED"),
            )
            with pytest.raises(DualeAIError):
                await response.model()
            await asyncio.sleep(0)

        spy.assert_called_once()
        kwargs = spy.call_args.kwargs
        assert kwargs["task_type"] == "continuation"
        assert kwargs["duration"] >= 0.0

    async def test_task_submit_invokes_task_submitted_metric(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """``submit_task`` fires ``task_submitted`` with task_type + streaming flag."""
        task_id = "obs-task-submitted"
        inject_llm_completion(mock_transport, task_id, completion="ok")

        with patch.object(minimal_mock_sdk._observability, "task_submitted") as spy:
            response = await ask(action="hello", request_id=task_id, sdk=minimal_mock_sdk)
            await response.model()

        spy.assert_called()
        kwargs = spy.call_args.kwargs
        assert kwargs["task_type"] == "completion"
        # Streaming flag is normalized to a lowercase string.
        assert kwargs["streaming_enabled"] in ("true", "false")

    async def test_execute_activity_cache_miss_records_metric(self, minimal_mock_sdk: DualeAISDK) -> None:
        """First ``execute_activity`` invocation fires ``cache_miss``."""
        sdk = minimal_mock_sdk
        sdk.cache = MockCacheBackend(tenant_id="obs-test")

        def fetch() -> str:
            return "value"

        with patch.object(sdk._observability, "cache_miss") as miss_spy:
            result = await sdk.execute_activity(fetch, cache_ttl=timedelta(minutes=1), max_retries=1)

        assert result == "value"
        miss_spy.assert_called()
        kwargs = miss_spy.call_args.kwargs
        assert kwargs["cache_type"] == "activity"

    async def test_execute_activity_cache_hit_records_metric(self, minimal_mock_sdk: DualeAISDK) -> None:
        """Second ``execute_activity`` with same args fires ``cache_hit``."""
        sdk = minimal_mock_sdk
        sdk.cache = MockCacheBackend(tenant_id="obs-test-hit")

        def fetch() -> str:
            return "value"

        # Prime the cache (real SDK code populates the backend).
        primed = await sdk.execute_activity(fetch, cache_ttl=timedelta(minutes=1), max_retries=1)
        assert primed == "value"

        with patch.object(sdk._observability, "cache_hit") as hit_spy:
            cached = await sdk.execute_activity(fetch, cache_ttl=timedelta(minutes=1), max_retries=1)

        assert cached == "value"
        hit_spy.assert_called()
        kwargs = hit_spy.call_args.kwargs
        assert kwargs["cache_type"] == "activity"

    async def test_get_health_status_records_component_metrics(self, minimal_mock_sdk: DualeAISDK) -> None:
        """``get_health_status`` records one record_operation call per component."""
        with patch.object(minimal_mock_sdk._observability, "record_operation") as spy:
            minimal_mock_sdk.get_health_status()

        # Production records four components: sdk / scheduler / cache / events.
        assert spy.call_count == 4
        components = {c.kwargs.get("component") for c in spy.call_args_list}
        assert components == {"sdk", "scheduler", "cache", "events"}
