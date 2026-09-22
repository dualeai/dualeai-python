"""Optional OpenTelemetry traces, metrics, and log correlation for the SDK.

Telemetry is enabled only when both an OTLP endpoint and telemetry token are
configured. The SDK exports traces and metrics; it does not install an
OpenTelemetry log exporter. Optional aiohttp, Redis, SQLite, and system
instrumentors are activated only when their instrumentation packages are
installed. OTLP exporters are base dependencies; the ``telemetry`` extra adds
only those optional instrumentors.

OpenTelemetry providers and instrumentors are process-global. Existing host
providers take precedence where the OpenTelemetry API exposes them, while a
provider installed here can be reused by later SDK instances. SDK cleanup does
not flush or shut down global providers.

Provider, instrumentor, and redaction behavior is covered by
``tests/test_feature_observability.py`` and
``tests/test_observability_tool_error_redaction.py``.
"""

import functools
import time
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from typing import ParamSpec, Protocol, TypeVar

import structlog
from opentelemetry import metrics, propagate, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import NoOpMeterProvider
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import View
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import NoOpTracerProvider, ProxyTracerProvider, Status, StatusCode
from opentelemetry.trace.span import Span
from opentelemetry.util.types import AttributeValue
from structlog.typing import EventDict, WrappedLogger

from dualeai.config import DualeAIConfig
from dualeai.utils import tenant_id_from_token
from dualeai.version import get_version


class Instrumentor(Protocol):
    """Optional OpenTelemetry instrumentor methods used by the SDK."""

    def instrument(self) -> None:
        """Activate this process-global instrumentor."""
        ...

    def uninstrument(self) -> None:
        """Deactivate this process-global instrumentor when supported."""
        ...


InstrumentorFactory = Callable[[], Instrumentor]

_AIOHTTP_INSTRUMENTOR: InstrumentorFactory | None
try:
    from opentelemetry.instrumentation.aiohttp_client import AioHttpClientInstrumentor as _AioHttpClientInstrumentor
except ImportError:
    _AIOHTTP_INSTRUMENTOR = None
else:
    _AIOHTTP_INSTRUMENTOR = _AioHttpClientInstrumentor

_REDIS_INSTRUMENTOR: InstrumentorFactory | None
try:
    from opentelemetry.instrumentation.redis import RedisInstrumentor as _RedisInstrumentor
except ImportError:
    _REDIS_INSTRUMENTOR = None
else:
    _REDIS_INSTRUMENTOR = _RedisInstrumentor

_SQLITE_INSTRUMENTOR: InstrumentorFactory | None
try:
    from opentelemetry.instrumentation.sqlite3 import SQLite3Instrumentor as _SQLite3Instrumentor
except ImportError:
    _SQLITE_INSTRUMENTOR = None
else:
    _SQLITE_INSTRUMENTOR = _SQLite3Instrumentor

_SYSTEM_METRICS_INSTRUMENTOR: InstrumentorFactory | None
try:
    from opentelemetry.instrumentation.system_metrics import SystemMetricsInstrumentor as _SystemMetricsInstrumentor
except ImportError:
    _SYSTEM_METRICS_INSTRUMENTOR = None
else:
    _SYSTEM_METRICS_INSTRUMENTOR = _SystemMetricsInstrumentor

logger = structlog.get_logger(__name__)

P = ParamSpec("P")
ReturnT = TypeVar("ReturnT")
ExporterT = TypeVar("ExporterT", OTLPSpanExporter, OTLPMetricExporter)


def safe_observability(func: Callable[P, ReturnT]) -> Callable[P, ReturnT | None]:
    """Suppress ordinary observability failures so they cannot fail SDK work."""

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> ReturnT | None:
        try:
            return func(*args, **kwargs)
        except Exception:  # noqa: BLE001
            pass  # Silently ignore observability errors
        return None

    return wrapper


# OpenTelemetry-compatible scalar attribute types, hoisted so the per-attribute
# isinstance check does not rebuild a UnionType on every metric record.
_OTEL_SCALAR_TYPES = (str, bool, int, float)


def _otel_attributes(attributes: Mapping[str, object]) -> dict[str, AttributeValue]:
    """Keep only OpenTelemetry-compatible scalar attribute values."""
    return {name: value for name, value in attributes.items() if isinstance(value, _OTEL_SCALAR_TYPES)}


def _instrumentor_classes() -> tuple[InstrumentorFactory, ...]:
    """Return installed OpenTelemetry instrumentors used by the SDK."""
    return tuple(
        instrumentor
        for instrumentor in (
            _AIOHTTP_INSTRUMENTOR,
            _REDIS_INSTRUMENTOR,
            _SQLITE_INSTRUMENTOR,
            _SYSTEM_METRICS_INSTRUMENTOR,
        )
        if instrumentor is not None
    )


def _apply_instrumentor_class(instrumentor_class: InstrumentorFactory, instrument: bool) -> None:  # noqa: FBT001
    """Apply one optional OpenTelemetry instrumentor."""
    try:
        instrumentor = instrumentor_class()
        if instrument:
            instrumentor.instrument()
        else:
            instrumentor.uninstrument()
    except Exception:  # noqa: BLE001
        pass  # Silent failures for optional instrumentation


# Context propagation functions
def inject_trace_context(headers: dict[str, str], enabled: bool = True) -> dict[str, str]:  # noqa: FBT001, FBT002
    """Inject trace context into headers."""
    if enabled:
        propagate.inject(headers)
    return headers


class SDKObservability:
    """Configure SDK traces and metrics and expose pre-created instruments.

    The object is disabled unless ``config.observability.token`` and endpoint
    are both non-empty. Its ``tenant_id`` attribute is a SHA-256 API-token
    fingerprint used for telemetry correlation and cache namespacing; it is not
    ``DualeAIConfig.tenant_id`` or a canonical tenant identifier.

    When this object installs a tracer provider it uses ``ALWAYS_ON`` sampling.
    If the host has already installed a provider, that provider and its sampling
    policy remain in control. Optional-instrumentor and metric-recording
    failures are suppressed; initial provider/exporter construction is not a
    universal failure boundary and can still make SDK construction fail.
    """

    def __init__(self, config: "DualeAIConfig"):
        """Initialize no-op or OTLP-backed instruments from SDK configuration."""
        self.config = config
        self.service_name = "dualeai-sdk"
        # This historical attribute name holds a token fingerprint, not the
        # configured Library tenant id.
        # config.token is always str here — the field_validator raises if None/missing
        token = config.token
        # DualeAIConfig.validate_token_present_and_format raises if token is None/missing
        assert token is not None, "config.token must be set"
        self.tenant_id = tenant_id_from_token(token)
        # Simple enabled check: if endpoint and token are provided, enable observability
        self.enabled = bool(config.observability.endpoint and config.observability.token)

        if not self.enabled:
            self._init_noop()
            return

        # Simplified OTEL setup
        self._setup_otel_providers()

        # Get global instances
        self.meter = metrics.get_meter("dualeai.sdk")
        self.tracer = trace.get_tracer("dualeai.sdk")

        # Pre-create instruments for performance (avoid runtime creation)
        self._operations_counter = self.meter.create_counter(
            name="dualeai.sdk.operations",
            description="SDK operations",
            unit="1",
        )
        self._durations_histogram = self.meter.create_histogram(
            name="dualeai.sdk.durations",
            description="SDK operation durations",
            unit="s",
        )

        logger.debug("SDK observability initialized", service=self.service_name, tenant=self.tenant_id)

    def _init_noop(self) -> None:
        """Initialize no-op implementations when disabled."""
        self.meter = metrics.get_meter_provider().get_meter("noop")
        self.tracer = trace.get_tracer_provider().get_tracer("noop")
        # Create no-op instruments
        self._operations_counter = self.meter.create_counter("noop.operations")
        self._durations_histogram = self.meter.create_histogram("noop.durations")

    def _setup_otel_providers(self) -> None:
        """Configure OpenTelemetry providers with simplified setup."""
        resource = self._create_otel_resource()
        self._setup_tracing(resource)
        self._setup_metrics(resource)
        self._setup_auto_instrumentation()

    def _create_otel_resource(self) -> Resource:
        """Create the standardized OpenTelemetry resource from SDK configuration."""
        version = get_version("dualeai", fallback="0.0.0-unknown")

        return Resource.create(
            {
                "service.name": self.service_name,
                "service.version": version,
                "service.namespace": "dualeai",
                "tenant.id": self.tenant_id,
                # Semconv vendor-distro identity. telemetry.sdk.* stays stock —
                # the semconv reserves telemetry.sdk.name for the SDK itself.
                "telemetry.distro.name": "dualeai-python",
                "telemetry.distro.version": version,
            }
        )

    def _setup_tracing(self, resource: Resource) -> None:
        """Setup tracing with simplified configuration and sampling."""
        # Only set tracer provider if not already initialized (prevents override warnings)
        # ProxyTracerProvider = not initialized, NoOpTracerProvider = explicitly disabled
        current_provider = trace.get_tracer_provider()
        if not isinstance(current_provider, (ProxyTracerProvider, NoOpTracerProvider)):
            # Provider already set by another component, skip to avoid override warning
            return

        # When the SDK owns the provider it samples every span. A host-installed
        # provider retains its own sampler because setup returns before this point.
        tracer_provider = TracerProvider(resource=resource, sampler=ALWAYS_ON)
        trace.set_tracer_provider(tracer_provider)

        span_exporter = self._create_otlp_exporter(OTLPSpanExporter, self.config.otel_traces_endpoint)
        tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))

    def _setup_metrics(self, resource: Resource) -> None:
        """Setup metrics with simplified configuration."""
        # Only set meter provider if not already initialized (prevents override warnings)
        # Check for NoOpMeterProvider (explicitly disabled) or SDK MeterProvider (already set)
        current_provider = metrics.get_meter_provider()
        if isinstance(current_provider, (MeterProvider, NoOpMeterProvider)):
            # Provider already set by another component, skip to avoid override warning
            return

        metric_exporter = self._create_otlp_exporter(OTLPMetricExporter, self.config.otel_metrics_endpoint)
        metric_reader = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=30000)

        # Simplified views for core instruments
        views = [View(instrument_name="dualeai.sdk.*", attribute_keys={"operation", "status"})]

        meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader], views=views)
        metrics.set_meter_provider(meter_provider)

    def _setup_auto_instrumentation(self) -> None:
        """Setup automatic instrumentation for common libraries."""
        self._apply_instrumentors(instrument=True)

    def _apply_instrumentors(self, instrument: bool = True) -> None:  # noqa: FBT001, FBT002
        """Apply or remove instrumentors based on the instrument flag."""
        for instrumentor_class in _instrumentor_classes():
            _apply_instrumentor_class(instrumentor_class, instrument)

    def _create_otlp_exporter(self, exporter_class: type[ExporterT], endpoint: str) -> ExporterT:
        """Create OTLP exporter for one signal's endpoint."""
        return exporter_class(endpoint=endpoint, headers=self.config.otel_headers)

    # --- Tracing Methods ---

    @contextmanager
    def trace_operation(self, operation: str, **attributes: object) -> Generator[Span | None]:
        """Trace any operation with automatic span management.

        Pass ``wraps_customer_code=True`` for a span wrapping a customer callable:
        the span then records neither the raised exception's message nor its
        stacktrace and keeps ``str(exc)`` out of its status. A customer exception
        may embed a secret, and this span exports off-box, so recording it would
        bypass the customer's ``error_transform`` boundary. The failure still
        surfaces as a type-only ERROR status. Omit it for SDK-internal spans,
        whose tracebacks are legitimate first-party signal.
        """
        # Read from **attributes rather than a typed keyword param so SDK-internal
        # callers forwarding an object-typed attribute dict don't trip the checker.
        wraps_customer_code = bool(attributes.pop("wraps_customer_code", False))
        if not self.enabled:
            yield None
            return

        with self.tracer.start_as_current_span(
            f"dualeai.sdk.{operation}",
            kind=trace.SpanKind.CLIENT,
            record_exception=not wraps_customer_code,
            set_status_on_exception=not wraps_customer_code,
        ) as span:
            if span.is_recording():
                span.set_attributes(
                    _otel_attributes(
                        {
                            "dualeai.tenant_id": self.tenant_id,
                            "dualeai.operation": operation,
                            **attributes,
                        }
                    )
                )
            try:
                yield span
            except Exception as exc:
                # Customer-code spans opt out of exception recording; still emit a
                # type-only error signal — the class name, never str(exc).
                if wraps_customer_code and span.is_recording():
                    span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                raise

    @contextmanager
    def trace_and_time(self, operation: str, **attributes: object) -> Generator[Span | None]:
        """Trace an operation and record its timing metric.

        Forwards ``wraps_customer_code`` to the span (see ``trace_operation``);
        the flag is popped before the metric, so it never becomes a metric
        attribute.
        """
        # Forward the customer-code flag to the span; keep it out of the metric.
        wraps_customer_code = bool(attributes.pop("wraps_customer_code", False))
        with self.trace_operation(operation, wraps_customer_code=wraps_customer_code, **attributes) as span:
            start_time = time.time()
            try:
                yield span
            finally:
                duration = time.time() - start_time
                # Automatically record timing with unified metrics
                self.record_operation(operation, "completed", duration=duration, **attributes)

    # --- Unified Metrics API ---

    @safe_observability
    def record_operation(
        self, operation: str, status: str = "success", duration: float | None = None, **attributes: object
    ) -> None:
        """Record any SDK operation with pre-created instruments for performance.

        Args:
            operation: Operation type (e.g., 'task_submission', 'cache_get', 'http_request')
            status: Operation status (e.g., 'success', 'error', 'timeout')
            duration: Optional duration in seconds
            **attributes: Additional attributes to record
        """
        self._record_operation(operation, status, duration, attributes)

    def _record_operation(
        self,
        operation: str,
        status: str,
        duration: float | None,
        attributes: Mapping[str, object],
    ) -> None:
        if not self.enabled:
            return

        # Use pre-created instruments for optimal performance
        attrs = _otel_attributes({"operation": operation, "status": status, **attributes})
        self._operations_counter.add(1, attrs)

        if duration is not None:
            self._durations_histogram.record(duration, attrs)

    # --- Convenience Methods for Common Operations ---

    @safe_observability
    def task_submitted(self, task_type: str = "completion", **attrs: object) -> None:
        """Record task submission with simplified API."""
        self._record_operation("task_submission", "submitted", None, {"task_type": task_type, **attrs})

    @safe_observability
    def task_completed(self, duration: float, task_type: str = "completion", **attrs: object) -> None:
        """Record task completion with duration."""
        self._record_operation("task_completion", "success", duration, {"task_type": task_type, **attrs})

    @safe_observability
    def task_failed(self, duration: float | None = None, task_type: str = "completion", **attrs: object) -> None:
        """Record task failure."""
        self._record_operation("task_completion", "failed", duration, {"task_type": task_type, **attrs})

    @safe_observability
    def cache_hit(self, cache_type: str = "redis", operation: str = "get", **attrs: object) -> None:
        """Record cache hit."""
        self._record_operation("cache", "hit", None, {"cache_operation": operation, "cache_type": cache_type, **attrs})

    @safe_observability
    def cache_miss(self, cache_type: str = "redis", operation: str = "get", **attrs: object) -> None:
        """Record cache miss."""
        self._record_operation("cache", "miss", None, {"cache_operation": operation, "cache_type": cache_type, **attrs})

    @safe_observability
    def streaming_dropped(self, count: int, task_id: str, **attrs: object) -> None:
        """Record dropped streaming messages due to queue overflow.

        Args:
            count: Total number of dropped messages
            task_id: Task identifier for the stream
            **attrs: Additional attributes
        """
        self._record_operation("streaming", "dropped", None, {"dropped_count": count, "task_id": task_id, **attrs})

    @safe_observability
    def streaming_queue_pressure(self, task_id: str, usage_pct: float, **attrs: object) -> None:
        """Record streaming queue pressure metrics.

        Args:
            task_id: Task identifier for the stream
            usage_pct: Queue usage percentage (0-100)
            **attrs: Additional attributes
        """
        self._record_operation(
            "streaming_queue",
            "pressure",
            None,
            {"task_id": task_id, "usage_percent": usage_pct, **attrs},
        )

    async def cleanup(self) -> None:
        """Remove optional global instrumentors installed by this module.

        This does not flush or shut down tracer/meter providers. It is not
        called by ``DualeAISDK.cleanup`` and affects process-global
        instrumentation, so applications with multiple SDK instances should
        coordinate ownership before calling it directly.
        """
        if self.enabled:
            self._apply_instrumentors(instrument=False)  # Cleanup automatic instrumentation


# --- Helper Functions ---


class OTELStructlogProcessor:
    """Streamlined structlog processor for OpenTelemetry trace context.

    Uses standard OTEL span context extraction for better compatibility.
    """

    def __call__(self, _logger: WrappedLogger, _method_name: str, event_dict: EventDict) -> EventDict:
        """Add trace context to log event using standard OTEL patterns."""
        span = trace.get_current_span()
        if span.is_recording():
            span_context = span.get_span_context()
            if span_context.is_valid:
                event_dict.update(
                    {
                        "trace_id": format(span_context.trace_id, "032x"),
                        "span_id": format(span_context.span_id, "016x"),
                    }
                )
        return event_dict
