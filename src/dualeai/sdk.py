"""Async facade for Duale AI Tasks, hosted Tools, Libraries, and activities.

Task submissions return :class:`dualeai.response.AgentResponse` objects backed
by protected Bridge HTTP/SSE calls. Registered Tools execute in the caller's
process when they are delivered on a Task stream. Library metadata uses
HPKE requests; presigned S3 parts use their signed HTTPS URLs.

Cached activities use a process-local scheduler and a Redis backend when it is
available, otherwise SQLite. Invalidation is TTL-, backend-, or caller-driven;
the SDK does not detect function changes or upstream data changes.

Constructing the SDK reconfigures process-global stdlib and structlog logging.
When telemetry is enabled it may also install process-global OpenTelemetry
providers and optional instrumentors. See :mod:`dualeai.logging_config` and
:mod:`dualeai.observability` before embedding the SDK in an application that
already owns either subsystem.

The local behaviors summarized here are covered across
``tests/test_feature_ask.py``, ``tests/test_feature_caching.py``,
``tests/test_agent_lifecycle.py``, ``tests/test_attachments.py``, and
``tests/test_feature_health.py``. Service-side authorization and execution
outcomes are not enforced by this repository's tests.

The transport boundaries are exercised by
``tests/test_http_transport_v3.py::test_real_v3_tls_boundary_reuses_discovery_per_service``
and ``tests/test_attachments_s3.py::test_public_upload_preserves_bytes_and_document_metadata``.
"""

import asyncio
import concurrent.futures
import contextlib
import contextvars
import hashlib
import inspect
import time
import types
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache, partial, wraps
from pathlib import Path
from typing import ParamSpec, TypeAlias, TypeVar
from uuid import uuid4

import aiojobs
import structlog
from pydantic import BaseModel, TypeAdapter
from structlog.typing import Processor
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    stop_after_delay,
    wait_exponential,
    wait_random_exponential,
)
from typing_extensions import Self

from dualeai._circuit_breaker import _CircuitOpenError
from dualeai.attachments import (
    PreparedAttachment,
    _call_scope_library_path,
    _validate_prepared_attachments,
    prepare_attachments,
)
from dualeai.backpressure import BackpressureConfig, BackpressureController
from dualeai.cache import CacheBackend, CacheConfig, create_cache_backend
from dualeai.config import DualeAIConfig
from dualeai.constants import TimingDefaults
from dualeai.decorators import tool as _tool_decorator
from dualeai.events.client import CloudEventsClient
from dualeai.events.transport import BridgeTaskRequest, HTTPTransportProtocol
from dualeai.exceptions import DualeAIConnectionError
from dualeai.libraries import LibrariesClient
from dualeai.lifecycle import LifecycleManager
from dualeai.logging_config import (
    FlowLogger,
    StructuredLogger,
    configure_logging,
    get_task_logger,
    operation_context,
)
from dualeai.models.agent_id import AgentId
from dualeai.models.bridge import (
    Attachment,
    BridgeContentDeltaResponse,
    BridgeContentResetResponse,
    BridgeTaskContinueRequest,
    BridgeTaskCreateRequest,
    BridgeTaskErrorResponse,
    BridgeTaskStoppedResponse,
    BridgeToolResultError,
    BridgeToolResultsRequest,
    BridgeToolResultSuccess,
    BridgeToolUseResponse,
    RegisteredTool,
)
from dualeai.models.capability import Capability
from dualeai.models.json_value import JsonValue
from dualeai.models.library import LibraryCreateRequest, LibraryDocumentCreateResponse
from dualeai.models.reserved_tool_names import ReservedToolName
from dualeai.models.response_format import JsonSchemaResponseFormat, ResponseFormat
from dualeai.models.routing_policy import RoutingPolicy
from dualeai.models.task_stop import TaskStopAccepted, TaskStopRequest
from dualeai.models.tool import Tool
from dualeai.observability import OTELStructlogProcessor, SDKObservability
from dualeai.response import AgentResponse, StreamingContentEvent, TerminalEvent
from dualeai.scheduler_config import create_production_scheduler
from dualeai.tool_context import ToolContext, _bind_tool_context
from dualeai.tools._contract import _compile_tool_callable, _CompiledToolCallable
from dualeai.tools._json import json_schema_adapter
from dualeai.tools._util import callable_name
from dualeai.tools.errors import apply_tool_error, format_registered_tool_error
from dualeai.tools.serialization import tool_success_output
from dualeai.utils import token_fingerprint

# Type variables and definitions
T = TypeVar("T")
R = TypeVar("R")
P = ParamSpec("P")
CacheableValue = JsonValue


# Type definitions for better type safety


ComponentHealth: TypeAlias = dict[str, bool]


# Module-level logger
logger = structlog.get_logger(__name__)
_cacheable_value_adapter: TypeAdapter[CacheableValue] = TypeAdapter(CacheableValue)
_agent_id_adapter: TypeAdapter[str] = TypeAdapter(AgentId)
_reserved_tool_names = frozenset(name.value for name in ReservedToolName)


@dataclass(frozen=True)
class RegisteredToolCallable:
    """SDK-local callable bound to a generated registered-tool wire model."""

    function: Callable[..., Awaitable[object]]
    tool: RegisteredTool
    _compiled: _CompiledToolCallable = field(kw_only=True, repr=False)
    retries: int = 0
    """Additional retry attempts beyond the first run; 0 disables retry."""
    error_transform: Callable[[Exception], str] | None = None
    """Optional hook to sanitize a raised exception into the model-facing message
    (redaction seam). When None, the default ``<module>.<qualname>: <message>`` is used."""

    def tool_kwargs(self, tool_use: BridgeToolUseResponse) -> dict[str, object]:
        """Validate wire input through the contract compiled at registration."""
        return self._compiled.tool_kwargs(tool_use)


@dataclass
class ScheduledToolUse:
    """Process-local replay state for one tool-use delivery."""

    expires_at: datetime
    result: BridgeToolResultSuccess | BridgeToolResultError | None = None


@lru_cache(maxsize=128)
def _generate_json_schema_cached(response_type: type) -> dict[str, JsonValue | None]:
    """Generate JSON Schema from a Python type for request validation.

    This is a module-level cached function to avoid memory leaks from instance-level caching.
    All SDK instances share this cache, preventing unbounded memory growth.

    Args:
        response_type: Python type or Pydantic model

    Returns:
        JSON Schema dict for the type
    """
    # Handle Pydantic models
    if isinstance(response_type, type) and issubclass(response_type, BaseModel):
        return json_schema_adapter.validate_python(response_type.model_json_schema())

    # Handle basic Python types
    type_mapping: dict[type, dict[str, JsonValue | None]] = {
        bool: {"type": "boolean"},
        int: {"type": "integer"},
        float: {"type": "number"},
        str: {"type": "string"},
        list: {"type": "array"},
        dict: {"type": "object"},
    }

    default_schema: dict[str, JsonValue | None] = {"type": "object"}
    return type_mapping.get(response_type, default_schema)


class DualeAISDK:
    """Manage Tasks, hosted Tools, Libraries, attachments, and activities.

    Instances lazily create their network, cache, and activity-scheduler
    resources. Use the async context manager or call :meth:`cleanup` to release
    those resources. Task and Library authorization remains a server concern;
    local cache namespacing uses a SHA-256 fingerprint of the configured token.
    """

    def __init__(  # Wide constructor: many independent field initializations
        self,
        config: DualeAIConfig | None = None,
        agent_id: str | None = None,
        max_jobs: int = TimingDefaults.DEFAULT_MAX_JOBS,
        job_timeout: int = TimingDefaults.DEFAULT_TASK_TIMEOUT_SECONDS,
        auto_start: bool = True,  # noqa: FBT001, FBT002
        backpressure_config: BackpressureConfig | None = None,
        max_concurrent_tools: int | None = None,
        graceful_shutdown_timeout: float = 0.0,
        transport: HTTPTransportProtocol | None = None,
    ):
        """Initialize an SDK instance.

        Construction calls :func:`dualeai.logging_config.configure_logging`,
        which replaces root handlers and changes process-wide logging settings.
        Enabled observability can also affect global OpenTelemetry state.

        Args:
            config: Configuration object. Omission loads ``DUALEAI_*`` settings.
            agent_id: Provisioned identity for hosted-tool lifecycle calls.
                Overrides ``config.agent_id`` and is exposed as ``sdk.agent_id``;
                attachment uploads use it when no per-call ID is supplied.
            max_jobs: Default concurrent cached-activity and registered-tool limit.
            job_timeout: Cached-activity timeout in seconds.
            auto_start: Schedule hosted-tool registration and heartbeats when an
                event loop is running, and start them on async context entry.
                Direct construction defaults to ``True``; :func:`dualeai.create_sdk`
                defaults to ``False``.
            backpressure_config: Activity scheduler and task circuit-breaker policy.
            max_concurrent_tools: Max concurrent registered-tool executions. Defaults to
                ``max_jobs`` so tool concurrency is decoupled from the job-scheduler pool
                only when explicitly set.
            graceful_shutdown_timeout: Seconds to let in-flight tool results finish on
                shutdown before cancelling them. Default 0.0 cancels immediately.
            transport: Preconfigured HTTP transport for dependency injection.
                Omission creates the production transport on first use.

        Raises:
            RuntimeError: If default configuration cannot be loaded.
            pydantic.ValidationError: If ``agent_id`` or a supplied configuration
                value fails validation.
        """
        # If no config provided, try to load from environment variables
        if config is None:
            try:
                self.config = DualeAIConfig()
            except Exception as e:
                raise RuntimeError(
                    "DualeAIConfig initialization failed."
                    " Please provide a valid config or set required environment variables."
                ) from e
        else:
            self.config = config

        # Use DEBUG logging when config.debug=True, otherwise ERROR
        log_level = "DEBUG" if self.config.debug else "ERROR"

        # Initialize observability first
        self._observability = SDKObservability(self.config)

        # Configure console logging with OTEL integration
        configure_logging(level=log_level)
        self._setup_enhanced_logging()

        resolved_agent_id = agent_id if agent_id is not None else self.config.agent_id
        self.agent_id = _agent_id_adapter.validate_python(resolved_agent_id) if resolved_agent_id is not None else None
        self._tools: dict[str, RegisteredToolCallable] = {}
        # In-flight task runners keyed by task_id. Used by cleanup() for
        # cancel-on-shutdown and by tests for assertion. Each runner removes
        # itself when its terminal task ends.
        self._inflight_tasks: dict[str, asyncio.Task[TerminalEvent]] = {}
        self.max_jobs = max_jobs
        self.job_timeout = job_timeout
        self.auto_start = auto_start
        self._cache: CacheBackend[CacheableValue] | None = None
        self._events_client: CloudEventsClient | None = None
        self._events_client_lock = asyncio.Lock()
        self._transport: HTTPTransportProtocol | None = transport
        self._libraries = LibrariesClient(self._ensure_events_client)
        self._scheduler: aiojobs.Scheduler | None = None
        self._lifecycle = LifecycleManager(
            ensure_events_client=self._ensure_events_client,
            manifest=self._registered_tool_manifest,
            agent_id=self.agent_id,
            auto_start=auto_start,
        )
        self._tool_result_tasks: set[asyncio.Task[None]] = set()
        # Bound concurrent registered-tool executions so a burst of tool.use
        # events cannot spawn unbounded in-flight customer work.
        # Bounded pool: a registered tool must not synchronously block on a
        # sibling registered tool, or the pool cannot drain.
        self._max_concurrent_tools = max_concurrent_tools if max_concurrent_tools is not None else max_jobs
        self._tool_execution_semaphore = asyncio.Semaphore(self._max_concurrent_tools)
        # Sync tools run in a DEDICATED bounded pool (not asyncio's shared default
        # executor). A deadline cannot cancel a running thread, so a timed-out sync
        # tool keeps a worker busy until it finishes; a dedicated pool caps the total
        # OS threads at the tool-concurrency bound and isolates this from the rest of
        # the SDK's thread usage. Lazily created on first sync-tool call.
        self._sync_tool_executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._graceful_shutdown_timeout = max(0.0, graceful_shutdown_timeout)
        self._scheduled_tool_uses: dict[tuple[str, str], ScheduledToolUse] = {}
        self._startup_time = time.time()
        self._backpressure_config = backpressure_config or BackpressureConfig(
            max_concurrent_activities=max_jobs,
            max_pending_activities=max_jobs * 5,
        )
        self._backpressure_controller = BackpressureController(self._backpressure_config)
        # Set initial task dependency health status.
        self._update_health_metrics()

    def _setup_enhanced_logging(self) -> None:
        """Setup enhanced logging with simplified OTEL integration."""
        if not self._observability.enabled:
            return

        try:
            # Configure OTEL processor once
            existing_processors = structlog.get_config().get("processors", ())
            processors: list[Processor] = []
            if isinstance(existing_processors, list | tuple):
                processors.extend(processor for processor in existing_processors if callable(processor))
            processors.append(OTELStructlogProcessor())
            structlog.configure_once(processors=processors)

            logger.info("Enhanced logging with OTEL integration enabled")
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to setup enhanced logging", error=str(e))

    # --- Lifecycle delegators (state + machine live in dualeai.lifecycle.LifecycleManager) ---

    async def start(self) -> None:
        """Publish the hosted Tool manifest and start lifecycle heartbeats.

        The call is idempotent. A configured ``agent_id`` is required when any
        hosted Tools are registered; with neither an agent id nor Tools it is a
        no-op.
        """
        await self._lifecycle.start()

    async def serve(self, *, stop_event: asyncio.Event | None = None) -> None:
        """Run the hosted-Tool lifecycle until stopped or a heartbeat fails.

        ``serve`` publishes the manifest and maintains heartbeats; Tool calls
        themselves arrive on Task streams opened by this same SDK instance. On
        exit, ``serve`` calls :meth:`cleanup` for the whole SDK.
        """
        await self._lifecycle.serve(stop_event=stop_event, on_stop=self.cleanup)

    def _estimated_server_time(self, local_now: datetime | None = None) -> datetime:
        """Server-time estimate adjusted by the heartbeat clock offset (lifecycle clock)."""
        return self._lifecycle.estimated_server_time(local_now)

    def _config_hash(self) -> str:
        """Cross-tier config hash over the tool manifest (lifecycle manager owns the recipe)."""
        return self._lifecycle.config_hash()

    async def _ensure_cache(self) -> CacheBackend[CacheableValue]:
        """Ensure cache backend is initialized."""
        cache = self._cache
        if cache is None:
            # Try Redis first, then fall back to SQLite. The namespace is a
            # token fingerprint, not the configured Library tenant id.
            cache_config = CacheConfig()
            sqlite_path_value = str(self.config.sqlite_path) if self.config.sqlite_path else None
            # Token rotation intentionally selects a different cache namespace.
            # config.token is always str — field_validator raises if None/missing
            token = self.config.token
            assert token is not None, "config.token must be set (validated by DualeAIConfig)"
            tenant_id = token_fingerprint(token)
            cache = await create_cache_backend(
                tenant_id=tenant_id,
                redis_url=self.config.redis_url,
                sqlite_path=sqlite_path_value,
                config=cache_config,
            )
            self._cache = cache
        return cache

    async def _ensure_events_client(self) -> CloudEventsClient:
        """Ensure CloudEventsClient is initialized and connected."""
        client = self._events_client
        if client is not None and client.is_connected:
            return client

        async with self._events_client_lock:
            if self._events_client is None:
                # CloudEventsClient gets observability from config. The lock
                # makes the first connection single-flight for task and
                # Library callers sharing this SDK instance.
                self._events_client = CloudEventsClient(self.config, transport=self._transport)

            if not self._events_client.is_connected:
                await self._events_client.connect()
            return self._events_client

    async def _ensure_scheduler(self) -> aiojobs.Scheduler:
        """Ensure the cached-activity scheduler is initialized."""
        if self._scheduler is None or self._scheduler.closed:
            self._scheduler = create_production_scheduler(
                max_jobs=self._backpressure_config.max_concurrent_activities,
                close_timeout=5.0,  # Increased for CI environments - was 1.0s
                pending_limit=self._backpressure_config.max_pending_activities,
            )
        return self._scheduler

    def _get_sync_tool_executor(self) -> concurrent.futures.ThreadPoolExecutor:
        """Lazily create the dedicated bounded thread pool for sync-tool offload."""
        if self._sync_tool_executor is None:
            self._sync_tool_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=self._max_concurrent_tools,
                thread_name_prefix="dualeai-sync-tool",
            )
        return self._sync_tool_executor

    def register_tool(
        self,
        func: Callable[..., object],
        *,
        description: str,
        timeout: timedelta,
        retries: int = 0,
        error_transform: Callable[[Exception], str] | None = None,
    ) -> RegisteredTool:
        """Compile and register one customer-hosted Tool on this SDK.

        The callable can be synchronous or asynchronous. Registration compiles
        its annotated signature into the same Pydantic contract used for the
        published schema and invocation validation. See
        :func:`dualeai.decorators.tool` for the execution, retry, cancellation,
        return-value, and idempotency contract.

        Args:
            func: Annotated callable to expose under its Python name.
            description: Non-empty model-facing Tool description. The callable's
                docstring is not published.
            timeout: Positive per-attempt timeout shared with the absolute
                invocation deadline.
            retries: Non-negative additional attempts after the first.
            error_transform: Optional redactor for model-facing exception text.

        Returns:
            A detached copy of the generated wire manifest entry.

        Raises:
            RuntimeError: If this SDK has no provisioned ``agent_id``.
            ValueError: If description, timeout, retries, name, or registration
                uniqueness is invalid.
            TypeError: If the callable signature cannot form a Tool contract.
        """
        if self.agent_id is None:
            raise RuntimeError("SDK tools require agent_id. Configure DualeAISDK(agent_id=...) or DUALEAI_AGENT_ID.")
        if not description.strip():
            raise ValueError("Tool description is required")
        if timeout <= timedelta(0):
            raise ValueError("Tool timeout must be positive")
        if retries < 0:
            raise ValueError("Tool retries must be non-negative")

        tool_name = callable_name(func)
        if tool_name in _reserved_tool_names:
            raise ValueError(f"Tool name {tool_name!r} is reserved by the platform")
        if tool_name in self._tools:
            raise ValueError(f"Tool {tool_name!r} is already registered")

        compiled = _compile_tool_callable(func)
        model_facing_tool = Tool(
            name=tool_name,
            description=description,
            parameters=compiled.parameters,
        )
        # Surface parameters with no model-facing description — the model sees only
        # Annotated[T, Field(description=...)] text, never the docstring. Debug-level
        # (not a warning) to avoid noise on simple tools.
        undescribed = [
            name
            for name, prop in model_facing_tool.parameters.properties.items()
            if isinstance(prop, Mapping) and "description" not in prop
        ]
        if undescribed:
            logger.debug(
                "Tool parameters have no model-facing description; add Annotated[T, Field(description=...)]",
                tool=model_facing_tool.name,
                parameters=undescribed,
            )
        registered_tool = RegisteredTool(
            tool=model_facing_tool,
            timeout_seconds=timeout.total_seconds(),
        )

        # A sync callable runs in a worker thread so a blocking tool never stalls
        # the SDK event loop; an async callable runs as-is.
        if inspect.iscoroutinefunction(func):
            execution_func: Callable[..., Awaitable[object]] = func
        else:
            sync_func = func

            # Preserve the original signature and metadata for user introspection.
            @wraps(sync_func)
            async def execution_func(**kwargs: object) -> object:  # type: ignore[no-redef]
                loop = asyncio.get_running_loop()
                # copy_context() + ctx.run so the worker thread sees the bound
                # ToolContext: loop.run_in_executor does NOT propagate contextvars
                # into the thread (unlike asyncio.to_thread), so without this
                # current_tool_context() returns None inside a sync tool.
                context = contextvars.copy_context()
                return await loop.run_in_executor(
                    self._get_sync_tool_executor(),
                    lambda: context.run(partial(sync_func, **kwargs)),
                )

        self._tools[tool_name] = RegisteredToolCallable(
            function=execution_func,
            tool=registered_tool,
            _compiled=compiled,
            retries=retries,
            error_transform=error_transform,
        )
        return registered_tool.model_copy(deep=True)

    def tool(
        self,
        *,
        description: str,
        timeout: timedelta,
        retries: int = 0,
        error_transform: Callable[[Exception], str] | None = None,
    ) -> Callable[[Callable[P, R]], Callable[P, R]]:
        """Instance-bound tool decorator: ``@sdk.tool(...)`` avoids passing ``sdk=`` and

        makes a forgotten SDK unrepresentable. Equivalent to
        ``@tool(sdk=self, ...)``.
        """
        return _tool_decorator(
            sdk=self,
            description=description,
            timeout=timeout,
            retries=retries,
            error_transform=error_transform,
        )

    async def submit_tool_results(
        self,
        task_id: str,
        results: list[BridgeToolResultSuccess | BridgeToolResultError],
        *,
        last_event_id: str | None = None,
    ) -> TerminalEvent:
        """Submit tool outputs and consume the emitting task's stream to terminal.

        Args:
            task_id: Child task that emitted the matching ``tool.use`` events.
                Never substitute its continuation parent.
            results: Successful or failed outputs, each carrying its Tool-call id.
            last_event_id: Opaque SSE cursor of the triggering ``tool.use``
                event. Passing it resumes after that event on the same child
                stream.

        Returns:
            The terminal event from the same public ``task_id`` stream.
        """
        request = BridgeToolResultsRequest(type="tool_results", tool_results=results)
        client = await self._ensure_events_client()
        return await client.submit_tool_results(task_id=task_id, request=request, last_event_id=last_event_id)

    def _schedule_tool_use(
        self,
        task_id: str,
        tool_use: BridgeToolUseResponse,
        last_event_id: str | None = None,
    ) -> None:
        """Schedule local execution for one bridge tool-use event."""
        if tool_use.name not in self._tools:
            logger.debug(
                "Ignoring tool-use for unregistered SDK tool",
                task_id=task_id,
                tool_call_id=tool_use.tool_call_id,
                tool_name=tool_use.name,
            )
            return
        if tool_use.deadline_at is None:
            logger.debug(
                "Ignoring registered SDK tool-use without deadline",
                task_id=task_id,
                tool_call_id=tool_use.tool_call_id,
                tool_name=tool_use.name,
            )
            return
        now = self._estimated_server_time()
        self._prune_scheduled_tool_uses(now)
        dedup_key = (task_id, tool_use.tool_call_id)
        scheduled = self._scheduled_tool_uses.get(dedup_key)
        if scheduled is not None:
            if scheduled.result is not None:
                logger.info(
                    "Resubmitting cached tool-use result",
                    task_id=task_id,
                    tool_call_id=tool_use.tool_call_id,
                    tool_name=tool_use.name,
                )
                self._schedule_tool_result_submission(task_id, scheduled.result, last_event_id=last_event_id)
                return
            logger.info(
                "Ignoring duplicate tool-use delivery",
                task_id=task_id,
                tool_call_id=tool_use.tool_call_id,
                tool_name=tool_use.name,
            )
            return
        scheduled = ScheduledToolUse(expires_at=self._tool_use_dedup_expires_at(tool_use, now))
        self._scheduled_tool_uses[dedup_key] = scheduled

        task = asyncio.create_task(
            self._execute_tool_use(
                task_id,
                tool_use,
                last_event_id=last_event_id,
                scheduled_tool_use=scheduled,
            )
        )
        self._tool_result_tasks.add(task)

        def _on_done(done_task: asyncio.Task[None]) -> None:
            self._tool_result_tasks.discard(done_task)
            with contextlib.suppress(asyncio.CancelledError):
                exc = done_task.exception()
                if exc is not None:
                    logger.warning("Tool-use execution task failed", task_id=task_id, error=str(exc))

        task.add_done_callback(_on_done)

    def _schedule_tool_result_submission(
        self,
        task_id: str,
        result: BridgeToolResultSuccess | BridgeToolResultError,
        *,
        last_event_id: str | None = None,
    ) -> None:
        """Schedule a cached tool result submission without re-running customer code."""
        task = asyncio.create_task(
            self._submit_single_tool_result(
                task_id,
                result,
                last_event_id=last_event_id,
            )
        )
        self._tool_result_tasks.add(task)

        def _on_done(done_task: asyncio.Task[None]) -> None:
            self._tool_result_tasks.discard(done_task)
            with contextlib.suppress(asyncio.CancelledError):
                exc = done_task.exception()
                if exc is not None:
                    logger.warning("Tool-result submission task failed", task_id=task_id, error=str(exc))

        task.add_done_callback(_on_done)

    def _prune_scheduled_tool_uses(self, now: datetime) -> None:
        """Drop completed tool-use ids after their replay margin expires."""
        expired = [key for key, scheduled in self._scheduled_tool_uses.items() if scheduled.expires_at <= now]
        for key in expired:
            self._scheduled_tool_uses.pop(key, None)

    def _tool_use_dedup_expires_at(self, tool_use: BridgeToolUseResponse, now: datetime) -> datetime:
        """Return the process-local replay expiry for one tool-use id."""
        base_time = tool_use.deadline_at or now
        base_time = max(base_time, now)
        return base_time + timedelta(seconds=TimingDefaults.TOOL_USE_DEDUP_REPLAY_MARGIN_SECONDS)

    def _render_tool_error(self, tool_name: str, exc: Exception) -> str:
        """Render the model-facing tool error, honoring the tool's error_transform."""
        registered = self._tools.get(tool_name)
        transform = registered.error_transform if registered is not None else None
        if transform is None:
            return format_registered_tool_error(exc)
        try:
            return apply_tool_error(exc, transform)
        except Exception:
            # A redactor was opted in precisely to keep raw exception text (possibly
            # secrets) off the wire. If it fails or returns a non-str, fail CLOSED with
            # a generic message — never fall back to the raw exception (CWE-209).
            logger.exception("Tool error_transform failed; using generic error message", tool=tool_name)
            return f"{tool_name}: tool execution failed"

    async def _run_registered_tool(
        self, task_id: str, tool_use: BridgeToolUseResponse
    ) -> dict[str, JsonValue | None] | str:
        """Run one registered tool with input validation and deadline enforcement."""
        registered_tool = self._tools.get(tool_use.name)
        if registered_tool is None:
            raise ValueError(f"Unknown registered tool: {tool_use.name}")

        # Pre-checks are single-shot: retrying a deterministic input or deadline
        # failure only burns the deadline. Only the callable body is retried.
        kwargs = registered_tool.tool_kwargs(tool_use)
        if tool_use.deadline_at is None:
            raise ValueError(f"Tool call {tool_use.tool_call_id} has no deadline_at")

        result = await self._invoke_registered_tool_with_retry(
            registered_tool,
            kwargs,
            task_id=task_id,
            deadline_at=tool_use.deadline_at,
            tool_call_id=tool_use.tool_call_id,
        )
        return tool_success_output(result)

    async def _invoke_registered_tool_with_retry(
        self,
        registered_tool: RegisteredToolCallable,
        kwargs: dict[str, object],
        *,
        task_id: str,
        deadline_at: datetime,
        tool_call_id: str,
    ) -> object:
        """Invoke the callable, retrying transient failures within the absolute deadline.

        The deadline spans every attempt (``stop_after_delay``); a per-attempt
        timeout is the smaller of the tool timeout and the remaining budget. Total
        elapsed is bounded to the deadline plus at most one backoff interval
        (``TOOL_RETRY_BACKOFF_MAX_SECONDS``), so a fast failure never becomes a
        slow tool_timeout. ``retries=0`` (default) runs the callable exactly once.
        Any raised
        exception is retried — the client owns idempotency (see the @tool
        docstring); a per-attempt timeout cancels the coroutine mid-run, so a
        retried side effect may have partially applied.
        """
        # A future retry-ratio budget could further limit fan-out amplification.
        # Today retries are bounded by the per-call attempt count and absolute
        # deadline only.
        deadline_budget = max(0.0, (deadline_at - self._estimated_server_time()).total_seconds())
        # Surface when the deadline is too short for the configured retries to fire —
        # otherwise `retries=N` silently degrades to fewer (or zero) real attempts.
        full_retry_budget = registered_tool.tool.timeout_seconds * (registered_tool.retries + 1)
        if registered_tool.retries > 0 and deadline_budget < full_retry_budget:
            logger.warning(
                "Tool deadline shorter than tool timeout; configured retries may not fire",
                tool_call_id=tool_call_id,
                retries=registered_tool.retries,
                deadline_budget_seconds=round(deadline_budget, 3),
                tool_timeout_seconds=registered_tool.tool.timeout_seconds,
            )
        async for attempt in AsyncRetrying(
            # Absolute deadline spans all attempts; whichever fires first wins.
            stop=stop_after_attempt(registered_tool.retries + 1) | stop_after_delay(deadline_budget),
            wait=wait_random_exponential(
                multiplier=TimingDefaults.TOOL_RETRY_BACKOFF_MULTIPLIER_SECONDS,
                max=TimingDefaults.TOOL_RETRY_BACKOFF_MAX_SECONDS,
            ),
            retry=retry_if_exception_type(Exception),
            reraise=True,
        ):
            with attempt:
                remaining = (deadline_at - self._estimated_server_time()).total_seconds()
                if remaining <= 0:
                    raise TimeoutError(f"Tool call {tool_call_id} deadline already expired")
                # Expose the call's provider-issued id, attempt, and deadline to the
                # callable via current_tool_context(). The id is stable across retries
                # but not unique; ToolContext.tool_call_id documents the key to build.
                context = ToolContext(
                    tool_call_id=tool_call_id,
                    task_id=task_id,
                    attempt=attempt.retry_state.attempt_number,
                    deadline_at=deadline_at,
                )
                # One OTel GenAI execute_tool span per attempt (semantic-conventions
                # v1.37 — the client-side "function" tool case).
                tool_name = registered_tool.tool.tool.name
                with (
                    self._observability.trace_operation(
                        f"execute_tool {tool_name}",
                        # Customer tool: keep its raw exception (possibly secret-bearing)
                        # out of the exported span; error_transform can't reach telemetry.
                        wraps_customer_code=True,
                        **{
                            "gen_ai.operation.name": "execute_tool",
                            "gen_ai.tool.name": tool_name,
                            "gen_ai.tool.call.id": tool_call_id,
                            "gen_ai.tool.call.attempt": context.attempt,
                        },
                    ),
                    _bind_tool_context(context),
                ):
                    return await asyncio.wait_for(
                        registered_tool.function(**kwargs),
                        timeout=min(registered_tool.tool.timeout_seconds, remaining),
                    )
        raise RuntimeError("Tool retry loop exhausted without producing a result")

    async def _execute_tool_use(
        self,
        task_id: str,
        tool_use: BridgeToolUseResponse,
        *,
        last_event_id: str | None = None,
        scheduled_tool_use: ScheduledToolUse | None = None,
    ) -> None:
        """Execute a tool-use event and submit its generated Bridge result."""
        try:
            # Bound concurrent customer-tool execution; excess tool.use events
            # queue on the semaphore rather than running all at once.
            async with self._tool_execution_semaphore:
                output = await self._run_registered_tool(task_id, tool_use)
            result: BridgeToolResultSuccess | BridgeToolResultError = BridgeToolResultSuccess(
                type="success",
                tool_call_id=tool_use.tool_call_id,
                output=output,
            )
        except Exception as exc:
            # Surface the failure locally: the error otherwise travels only as a
            # string in the wire result, leaving the developer's process silent
            # about which tool failed and why.
            logger.exception(
                "Registered tool execution failed",
                tool_name=tool_use.name,
                tool_call_id=tool_use.tool_call_id,
                task_id=task_id,
            )
            result = BridgeToolResultError(
                type="error",
                tool_call_id=tool_use.tool_call_id,
                message=self._render_tool_error(tool_use.name, exc),
            )

        if scheduled_tool_use is not None:
            scheduled_tool_use.result = result
        await self._submit_single_tool_result(task_id, result, last_event_id=last_event_id)

    async def _submit_single_tool_result(
        self,
        task_id: str,
        result: BridgeToolResultSuccess | BridgeToolResultError,
        *,
        last_event_id: str | None = None,
    ) -> None:
        """Submit one generated Bridge tool result and schedule nested tool-use events."""
        request = BridgeToolResultsRequest(type="tool_results", tool_results=[result])
        client = await self._ensure_events_client()
        await client.submit_tool_results(
            task_id=task_id,
            request=request,
            last_event_id=last_event_id,
            tool_use_callback=lambda next_tool_use, next_event_id: self._schedule_tool_use(
                task_id,
                next_tool_use,
                last_event_id=next_event_id,
            ),
        )

    async def execute_activity(
        self,
        func: Callable[P, CacheableValue | Awaitable[CacheableValue]],
        cache_ttl: timedelta | None = None,
        max_retries: int = 3,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> CacheableValue:
        """Execute and cache a callable through the activity scheduler.

        The cache key is derived from the callable name and ``repr`` of its
        arguments. Changing a callable's implementation does not invalidate old
        entries, and different inputs with identical representations can collide.
        Version inputs explicitly when that distinction matters. A cached JSON
        ``null`` is indistinguishable from a cache miss.

        A synchronous callable runs directly on the event-loop thread. Use an
        async callable, or perform blocking work in your own executor. Every
        ordinary ``Exception`` is retried, so side-effecting activities must be
        idempotent before ``max_retries`` is greater than zero.

        Args:
            func: Synchronous or asynchronous callable returning a JSON value.
            cache_ttl: Entry lifetime. ``None`` stores without backend expiry;
                it does not disable caching.
            max_retries: Additional attempts after the first call. Use a
                non-negative integer; this method does not proactively validate
                negative values.
            *args: Positional arguments forwarded to ``func`` and included in
                the cache-key input.
            **kwargs: Keyword arguments forwarded to ``func`` and included in
                the cache-key input.

        Returns:
            The cached or newly computed JSON-compatible result.

        Raises:
            TimeoutError: If scheduler execution exceeds ``job_timeout``.
            Exception: The final callable failure after retries, or an error
                raised while initializing a non-best-effort backend.
        """
        # Extract function name: FunctionType has __name__; fallback to class name for callables
        func_name = func.__name__ if isinstance(func, types.FunctionType) else type(func).__name__
        # Start observability tracking
        with self._observability.trace_and_time(
            "activity_execution",
            # Customer activity: keep its raw exception out of the exported span.
            wraps_customer_code=True,
            activity_name=func_name,
            max_retries=max_retries,
            has_cache_ttl=cache_ttl is not None,
        ):
            # Generate simple cache key
            inputs_str = f"{func_name}:{args!r}:{kwargs!r}"
            cache_key: str = hashlib.sha256(inputs_str.encode()).hexdigest()[:16]

            # Check cache first
            cache = await self._ensure_cache()
            cached_result = await cache.get(cache_key)
            if cached_result is not None:
                self._observability.cache_hit(cache_type="activity", operation="get")
                return cached_result
            self._observability.cache_miss(cache_type="activity", operation="get")

            # Execute through aiojobs scheduler
            scheduler: aiojobs.Scheduler = await self._ensure_scheduler()

            async def _execute_activity() -> CacheableValue:
                # Execute with tenacity retry
                async def _execute_with_retry() -> CacheableValue:
                    async for attempt in AsyncRetrying(
                        stop=stop_after_attempt(max_retries + 1),
                        wait=wait_exponential(multiplier=1, min=1, max=10),
                        retry=retry_if_exception_type(Exception),
                        reraise=True,
                    ):
                        with attempt:
                            result = func(*args, **kwargs)
                            # Handle both sync and async functions
                            if isinstance(result, Awaitable):
                                return _cacheable_value_adapter.validate_python(await result)
                            return _cacheable_value_adapter.validate_python(result)
                    raise RuntimeError("Retry loop exhausted without producing a result")

                return await _execute_with_retry()

            # Schedule job
            job = await scheduler.spawn(_execute_activity())

            # Wait for result with timeout
            try:
                result = await asyncio.wait_for(job.wait(), timeout=self.job_timeout)
            except TimeoutError:
                await job.close()
                raise TimeoutError(f"Activity execution timed out after {self.job_timeout}s") from None

            # Cache the result
            await cache.set(cache_key, result, cache_ttl)
            self._observability.record_operation("cache", "success", cache_operation="set", cache_type="activity")

            return result

    async def _execute_task_operation(
        self,
        operation_name: str,
        operation_func: Callable[[], Awaitable[R]],
        task_type: str,
        streaming: bool,  # noqa: FBT001
        trace_attributes: dict[str, object],
        rejection_context: str | None = None,
    ) -> R:
        """Execute a task submission with tracing and circuit classification.

        This method shares circuit classification across root and continuation submissions.

        Args:
            operation_name: Name of the operation (e.g., "task_submission", "task_continuation")
            operation_func: Async function to execute
            task_type: Type of task for metrics
            streaming: Whether streaming is enabled
            trace_attributes: Attributes for tracing
            rejection_context: Context string for rejection message

        Returns:
            Value returned by ``operation_func``
        """
        with self._observability.trace_operation(operation_name, **trace_attributes):
            try:
                result = await operation_func()
            except _CircuitOpenError as exc:
                self._backpressure_controller.metrics.record_rejection()
                logger.warning(
                    f"{operation_name} rejected because the task dependency is unavailable",
                    retry_after_seconds=round(exc.retry_after, 3),
                    context=rejection_context,
                )
                raise RuntimeError(
                    f"{operation_name} rejected: task dependency is unavailable; retry in {exc.retry_after:.1f}s"
                ) from None
        self._observability.task_submitted(
            task_type=task_type,
            streaming_enabled=str(streaming).lower(),
        )
        self._backpressure_controller.metrics.record_submission()
        return result

    @staticmethod
    def prepare_attachments(files: list[tuple[Path, str]]) -> list[PreparedAttachment]:
        """Prepare attachment metadata for upload and task submission.

        Reads file sizes with ``stat()`` and does not load file contents.

        Args:
            files: List of (path, description) tuples.

        Returns:
            List of PreparedAttachment with key, filename, description, size.

        Raises:
            FileNotFoundError: If a path does not exist.
            ValueError: If a path is not a regular, non-empty file.
            pydantic.ValidationError: If generated metadata or a description
                violates ``PreparedAttachment`` bounds.
        """
        return prepare_attachments(files)

    async def upload_attachments(
        self,
        task_id: str,
        attachments: list[PreparedAttachment],
        *,
        agent_id: str | None = None,
    ) -> dict[str, LibraryDocumentCreateResponse]:
        """Upload prepared attachments to a Task-scoped Library.

        Resolve the call-scoped Library once, then create an upload session,
        upload presigned S3 parts, and create a queued Library document for
        each attachment. At most eight file pipelines and eight presigned-part
        requests are active at once. The batch is not transactional.

        Args:
            task_id: Client-selected task identifier that will also be supplied
                as ``request_id`` when the Task is submitted.
            attachments: Values returned by :meth:`prepare_attachments`.
            agent_id: Agent identifier (keyword-only). Overrides the SDK's
                configured Agent identity for this upload. Required when the SDK
                has no configured Agent identity.

        Returns:
            Queued Library document receipts keyed by prepared attachment key.

        Raises:
            ValueError: If neither an explicit nor a configured Agent ID exists,
                an attachment key is duplicated, or a prepared file is no longer
                a regular non-empty file of the recorded size.
            FileNotFoundError: If a prepared file no longer exists.
            LibraryUploadError: If a local read or object-store upload fails.
                ``completed_receipts`` contains documents already queued by the
                non-transactional batch.
            DualeAIAuthError: If the Library request is unauthorized.
            BusinessError: If the Library request is rejected.
            DualeAIConnectionError: If a Library transport or server failure occurs.
        """
        if not attachments:
            return {}

        _validate_prepared_attachments(attachments)
        resolved_agent_id = agent_id if agent_id is not None else self.agent_id
        if resolved_agent_id is None:
            raise ValueError("upload_attachments: agent_id is required when the SDK has no configured Agent ID")

        library_path = _call_scope_library_path(
            agent_id=resolved_agent_id,
            task_id=task_id,
        )
        library = await self._libraries.create(LibraryCreateRequest(path=library_path))
        return await self._libraries.upload(library.id, attachments)

    async def stop_task(self, task_id: str, reason: str) -> TaskStopAccepted:
        """Request that the platform stop a Task.

        The returned receipt only acknowledges the request. It does not prove
        that the target exists, is eligible to stop, or will later emit a
        ``task.stopped`` event. If such an event is observed, awaiting
        :meth:`AgentResponse.model <dualeai.response.AgentResponse.model>` raises
        :class:`dualeai.exceptions.TaskStoppedError` with the event's reason.

        Args:
            task_id: The task to stop.
            reason: Non-empty explanation sent with the request.

        Returns:
            The request-acceptance receipt.

        Raises:
            ValueError: If ``reason`` is empty or whitespace.
            DualeAIError: If the request fails before acceptance.
        """
        if not reason or not reason.strip():
            raise ValueError("Stop reason cannot be empty")

        events_client = await self._ensure_events_client()
        return await events_client.stop_task(task_id, TaskStopRequest(reason=reason))

    async def submit_task(
        self,
        action: str,
        capabilities: list[Capability],
        routing_policy: RoutingPolicy | None = None,
        response_type: type[T] | None = None,
        response_schema: dict[str, JsonValue | None] | None = None,
        response_format: ResponseFormat | None = None,
        streaming: bool = False,  # noqa: FBT001, FBT002
        deadline: datetime | None = None,
        request_id: str | None = None,
        task_type: str = "completion",
        attachments: list[PreparedAttachment] | None = None,
    ) -> "AgentResponse[T]":
        """Submit a root Task under the process-local dependency circuit breaker.

        Returns an :class:`AgentResponse` whose ``task`` attribute is
        the ``asyncio.Task`` running the bridge SSE iteration. Cancel
        via ``response.task.cancel()`` propagates to the bridge
        connection (TCP closed). Use :meth:`stop_task` to send a separate
        platform stop request.

        Args:
            action: Non-empty Task instruction.
            capabilities: Capabilities used to derive ``required_capabilities`` when
                ``routing_policy`` is omitted.
            routing_policy: Explicit routing policy. Takes precedence over the
                policy derived from ``capabilities``.
            response_type: Optional type for local ``AgentResponse.model()``
                validation and, unless overridden, request-schema derivation.
            response_schema: Optional JSON Schema used when deriving a response
                format from ``response_type``.
            response_format: Explicit wire response format. Takes precedence
                over ``response_schema`` derivation.
            streaming: Request content delta/reset events.
            deadline: Optional absolute deadline. Omission uses the SDK default.
                A timezone-naive value currently fails with ``TypeError`` during
                deadline arithmetic.
            request_id: Optional client-selected root Task id; omission generates
                UUID4. Use the same id previously used for attachment upload.
            task_type: Observability label only; it does not change routing.
            attachments: Prepared metadata for files already uploaded under
                ``request_id``. This method does not upload or verify them.

        Returns:
            A response handle whose runner may still be in flight.

        Raises:
            ValueError: If ``action`` is empty or whitespace.
            TypeError: If a supplied ``deadline`` is timezone-naive.
            RuntimeError: If the process-local Task dependency circuit is open.

        Transport failures after the runner is created surface through
        ``response.model()`` or by awaiting ``response.task``.
        """
        if not action or not action.strip():
            raise ValueError("Action cannot be empty")

        async def operation() -> AgentResponse[T]:
            return await self._submit_task_internal(
                action,
                capabilities,
                routing_policy,
                response_type,
                response_schema,
                response_format,
                streaming,
                deadline,
                request_id,
                task_type,
                attachments,
            )

        return await self._execute_task_operation(
            operation_name="task_submission",
            operation_func=operation,
            task_type=task_type,
            streaming=streaming,
            trace_attributes={
                # Never log prompt/action CONTENT (PII). Metadata only.
                "action_length": len(action),
                "capabilities_count": len(capabilities),
                "streaming_enabled": streaming,
                "has_routing_policy": routing_policy is not None,
                "response_type": response_type.__name__ if response_type else None,
                "task_type": task_type,
                "request_id": request_id,
            },
            rejection_context=f"action_length={len(action)}",
        )

    async def _continue_task(
        self,
        parent_task_id: str,
        message: str,
        deadline: datetime | None = None,
        response_type: type[T] | None = None,
        response_format: ResponseFormat | None = None,
    ) -> "AgentResponse[T]":
        """Submit a child for a successful ``AgentResponse``.

        The SDK creates the child ID before submission. It sends that ID in the
        HTTP task path and sends ``parent_task_id`` in the continuation
        body. The returned ``AgentResponse.task_id`` is the child ID.
        """
        if deadline is not None and (deadline.tzinfo is None or deadline.utcoffset() is None):
            raise ValueError("Continuation deadline must be timezone-aware")

        return await self._execute_task_operation(
            operation_name="task_continuation",
            operation_func=partial(
                self._continue_task_internal,
                parent_task_id,
                message,
                deadline,
                response_type,
                response_format,
            ),
            task_type="continuation",
            streaming=False,
            trace_attributes={
                "parent_task_id": parent_task_id,
            },
            rejection_context=f"parent_task_id={parent_task_id}",
        )

    def _prepare_task_submission(
        self,
        operation_type: str,
        task_id: str,
        deadline: datetime | None = None,
        streaming: bool = False,  # noqa: FBT001, FBT002
    ) -> tuple[datetime, StructuredLogger]:
        """Common preparation logic for both submit and continue operations.

        Returns:
            Resolved deadline and task-scoped logger.
        """
        # Calculate deadline if not provided
        if deadline is None:
            # Default timeout from platform constants
            default_timeout_seconds = TimingDefaults.DEFAULT_TASK_TIMEOUT_SECONDS
            deadline = datetime.now(timezone.utc) + timedelta(seconds=default_timeout_seconds)
            logger.debug(
                f"Deadline calculated from default timeout for {operation_type}",
                default_timeout_seconds=default_timeout_seconds,
                deadline=deadline.isoformat(),
                task_id=task_id,
            )

        now = datetime.now(timezone.utc)
        deadline_remaining = (deadline - now).total_seconds()

        # Create task-specific logger with bound context
        task_logger = get_task_logger(
            f"dualeai.sdk.{operation_type}",
            task_id=task_id,
            streaming=streaming,
        )

        # Log deadline telemetry with task logger
        task_logger.info(
            f"Task {operation_type} deadline telemetry",
            deadline=deadline.isoformat(),
            deadline_remaining_seconds=round(deadline_remaining, 1),
            task_id=task_id,
        )

        return deadline, task_logger

    def _response_format_default(
        self,
        response_format: ResponseFormat | None,
        response_type: type | None,
        response_schema: dict[str, JsonValue | None] | None,
    ) -> ResponseFormat | None:
        """Resolve response_format, deriving from type-based schema if needed."""
        if response_format is not None:
            return response_format
        if response_type is not None:
            schema = response_schema or _generate_json_schema_cached(response_type)
            return JsonSchemaResponseFormat(json_schema=schema)
        return None

    def _build_streaming_buffers(
        self,
        streaming: bool,  # noqa: FBT001
        task_id: str,
    ) -> tuple[
        asyncio.Queue[StreamingContentEvent] | None,
        list[BridgeContentDeltaResponse] | None,
        Callable[[BridgeContentDeltaResponse], None] | None,
        Callable[[BridgeContentResetResponse], None] | None,
        Callable[[BridgeToolUseResponse, str | None], None] | None,
    ]:
        """Build streaming buffers + spawn-time callbacks.

        Returns ``(delta_queue, deltas_list, delta_callback,
        reset_callback, tool_use_callback)``. The content callbacks close
        over the buffers, which are then passed into ``AgentResponse`` so
        ``response.stream()`` reads what the callbacks write.

        With ``streaming=False`` no streaming queues are allocated. The
        tool-use callback still schedules registered SDK tool execution, because
        bridge ``tool.use`` events are delivery events rather than user-visible
        content streaming.
        """
        delta_queue: asyncio.Queue[StreamingContentEvent] | None = asyncio.Queue() if streaming else None
        deltas: list[BridgeContentDeltaResponse] | None = [] if streaming else None

        def on_delta(delta: BridgeContentDeltaResponse) -> None:
            if deltas is None or delta_queue is None:
                return
            deltas.append(delta)
            delta_queue.put_nowait(delta)

        def on_reset(reset: BridgeContentResetResponse) -> None:
            if deltas is None or delta_queue is None:
                return
            deltas.clear()
            delta_queue.put_nowait(reset)

        return delta_queue, deltas, on_delta, on_reset, partial(self._schedule_tool_use, task_id)

    async def _run_task_with_circuit_breaker(  # noqa: PLR0912 - explicit terminal outcome classification
        self,
        *,
        admission: asyncio.Future[None],
        task_id: str,
        request: BridgeTaskRequest,
        delta_callback: Callable[[BridgeContentDeltaResponse], None] | None,
        reset_callback: Callable[[BridgeContentResetResponse], None] | None,
        tool_use_callback: Callable[[BridgeToolUseResponse, str | None], None] | None,
    ) -> TerminalEvent:
        """Run one terminal bridge task under the SDK-local breaker."""
        controller = self._backpressure_controller

        admitted = False
        terminal: TerminalEvent | None = None
        spawn_time = time.time()
        task_type = "continuation" if request.type == "continue" else "completion"
        try:
            async with controller._task_breaker.protect() as attempt:  # noqa: SLF001 - one SDK-private implementation
                if admission.cancelled():
                    raise asyncio.CancelledError
                admission.set_result(None)
                admitted = True
                try:
                    events_client = await self._ensure_events_client()
                    terminal = await events_client.run_task(
                        task_id=task_id,
                        request=request,
                        delta_callback=delta_callback,
                        reset_callback=reset_callback,
                        tool_use_callback=tool_use_callback,
                    )
                except asyncio.CancelledError:
                    raise
                except DualeAIConnectionError as error:
                    if error.problem_details is not None and error.problem_details.retryable is False:
                        attempt.ignore()
                    raise
                except BaseException:
                    attempt.ignore()
                    raise

                if isinstance(terminal, BridgeTaskErrorResponse):
                    if terminal.data.retryable is True:
                        attempt.fail()
                    else:
                        attempt.ignore()
                elif isinstance(terminal, BridgeTaskStoppedResponse):
                    # Someone asked for this. The platform stayed healthy, so it
                    # counts neither as a success nor as a failure.
                    attempt.ignore()
                return terminal
        except BaseException as exc:
            if not admission.done():
                admission.set_exception(exc)
            raise
        finally:
            self._inflight_tasks.pop(task_id, None)
            if admitted:
                duration = time.time() - spawn_time
                if terminal is None or isinstance(terminal, BridgeTaskErrorResponse):
                    controller.record_failure()
                    self._observability.task_failed(duration=duration, task_type=task_type)
                elif not isinstance(terminal, BridgeTaskStoppedResponse):
                    controller.record_success()
                    self._observability.task_completed(duration=duration, task_type=task_type)

    async def _spawn_run_task(
        self,
        *,
        task_id: str,
        request: BridgeTaskRequest,
        delta_callback: Callable[[BridgeContentDeltaResponse], None] | None,
        reset_callback: Callable[[BridgeContentResetResponse], None] | None,
        tool_use_callback: Callable[[BridgeToolUseResponse, str | None], None] | None,
    ) -> asyncio.Task[TerminalEvent]:
        """Spawn one task and wait until the breaker admits or rejects it."""
        admission = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(
            self._run_task_with_circuit_breaker(
                admission=admission,
                task_id=task_id,
                request=request,
                delta_callback=delta_callback,
                reset_callback=reset_callback,
                tool_use_callback=tool_use_callback,
            )
        )
        self._inflight_tasks[task_id] = task
        try:
            await admission
        except asyncio.CancelledError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            raise
        except BaseException:
            with contextlib.suppress(BaseException):
                await task
            raise
        return task

    async def _spawn_and_wrap_response(
        self,
        *,
        operation_name: str,
        task_id: str,
        request: BridgeTaskRequest,
        streaming: bool,
        response_type: type[T] | None,
        task_logger: StructuredLogger,
    ) -> "AgentResponse[T]":
        """Spawn the bridge-iteration task and wrap it in an AgentResponse.

        Shared tail of ``_submit_task_internal`` and
        ``_continue_task_internal``. The caller must pre-resolve:
        - a fully validated create or continuation ``request``;
        - ``task_logger`` (via ``_prepare_task_submission``, with any
          op-specific bindings already attached);
        - ``task_id`` (the *new* spawn id, never the parent — see
          continue path which mints a fresh UUID).

        Pipeline: open ``operation_context`` → ``FlowLogger.start`` → build
        streaming buffers → spawn the protected bridge-iteration task → build
        the ``AgentResponse`` →
        ``FlowLogger.step`` / ``FlowLogger.end`` → ``await
        asyncio.sleep(0)`` so the spawned task reaches its first
        suspension before the caller sees the response.

        ``FlowLogger.start`` / ``end`` share ``task_id`` so the
        timing cache resolves and ``duration_ms`` is populated.
        """
        with operation_context(operation_name):
            FlowLogger.start(task_logger, operation_name, task_id)

            delta_queue, deltas, on_delta, on_reset, on_tool_use = self._build_streaming_buffers(
                streaming,
                task_id,
            )
            try:
                task = await self._spawn_run_task(
                    task_id=task_id,
                    request=request,
                    delta_callback=on_delta,
                    reset_callback=on_reset,
                    tool_use_callback=on_tool_use,
                )
            except BaseException:
                FlowLogger.end(task_logger, operation_name, task_id, success=False)
                raise
            response: AgentResponse[T] = AgentResponse(
                task_id=task_id,
                task=task,
                expected_type=response_type,
                sdk=self,
                streaming=streaming,
                delta_queue=delta_queue,
                deltas=deltas,
            )

            FlowLogger.step(task_logger, operation_name, "submitted")
            FlowLogger.end(task_logger, operation_name, task_id, success=True)
            await asyncio.sleep(0)
            return response

    async def _continue_task_internal(
        self,
        parent_task_id: str,
        message: str,
        deadline: datetime | None = None,
        response_type: type[T] | None = None,
        response_format: ResponseFormat | None = None,
    ) -> "AgentResponse[T]":
        """Internal task continuation: spawn task, return AgentResponse.

        ``parent_task_id`` is the public parent. The SDK creates the child ID before
        any HTTP work and keeps it on the returned ``AgentResponse``.
        """
        continuation_task_id = str(uuid4())
        deadline, task_logger = self._prepare_task_submission(
            operation_type="task_continuation",
            task_id=continuation_task_id,
            deadline=deadline,
            streaming=False,
        )
        resolved_response_format = self._response_format_default(response_format, response_type, None)
        if resolved_response_format is None:
            request = BridgeTaskContinueRequest(
                type="continue",
                parent_task_id=parent_task_id,
                message=message,
                deadline=deadline,
            )
        else:
            request = BridgeTaskContinueRequest(
                type="continue",
                parent_task_id=parent_task_id,
                message=message,
                deadline=deadline,
                response_format=resolved_response_format,
            )
        return await self._spawn_and_wrap_response(
            operation_name="task_continuation",
            task_id=continuation_task_id,
            request=request,
            streaming=False,
            response_type=response_type,
            task_logger=task_logger.bind(parent_task_id=parent_task_id),
        )

    async def _submit_task_internal(
        self,
        action: str,
        capabilities: list[Capability],
        routing_policy: RoutingPolicy | None = None,
        response_type: type[T] | None = None,
        response_schema: dict[str, JsonValue | None] | None = None,
        response_format: ResponseFormat | None = None,
        streaming: bool = False,  # noqa: FBT001, FBT002
        deadline: datetime | None = None,
        request_id: str | None = None,
        _task_type: str = "completion",
        attachments: list[PreparedAttachment] | None = None,
    ) -> "AgentResponse[T]":
        """Internal task submission: spawn task, return AgentResponse."""
        routing_policy_obj = routing_policy or (
            RoutingPolicy(required_capabilities=capabilities) if capabilities else None
        )
        task_id_preview = request_id or str(uuid4())

        deadline, task_logger = self._prepare_task_submission(
            operation_type="task_submission",
            task_id=task_id_preview,
            deadline=deadline,
            streaming=streaming,
        )

        task_logger = task_logger.bind(capabilities=[capability.value for capability in capabilities])
        if routing_policy_obj:
            task_logger = task_logger.bind(
                has_required_capabilities=bool(routing_policy_obj.required_capabilities),
                has_preferred_capabilities=bool(routing_policy_obj.preferred_capabilities),
            )

        task_logger.info("Task submission details", action_length=len(action))
        resolved_response_format = self._response_format_default(response_format, response_type, response_schema)
        bridge_attachments = (
            [
                Attachment(
                    key=attachment.key,
                    filename=attachment.filename,
                    description=attachment.description,
                )
                for attachment in attachments
            ]
            if attachments
            else []
        )
        request = BridgeTaskCreateRequest(
            type="create",
            action_prompt=action,
            deadline=deadline,
            routing_policy=routing_policy_obj,
            response_format=resolved_response_format,
            response_stream=streaming,
            attachments=bridge_attachments,
        )

        return await self._spawn_and_wrap_response(
            operation_name="task_submission",
            task_id=task_id_preview,
            request=request,
            streaming=streaming,
            response_type=response_type,
            task_logger=task_logger,
        )

    async def clear_cache(self) -> None:
        """Clear all cached activities."""
        cache = await self._ensure_cache()
        await cache.clear()

    async def cleanup_expired_cache(self) -> int:
        """Remove expired cache entries and return count removed."""
        cache = await self._ensure_cache()
        return await cache.cleanup_expired()

    def _component_health(self) -> ComponentHealth:
        """Single source of truth for per-component health.

        An absent optional component (scheduler/cache/events not yet wired)
        reports healthy: not-yet-initialized is not a failure. Scheduler and
        events report their real state when present; cache is reported healthy
        by presence only (its liveness is not probed here). If the scheduler's
        ``.closed`` getter raises, health stays True so the endpoint never
        crashes.
        """
        scheduler_healthy = cache_healthy = events_healthy = True
        try:
            if self._scheduler:
                scheduler_healthy = not self._scheduler.closed
            if self._events_client:
                events_healthy = self._events_client.is_connected
        except Exception as e:  # noqa: BLE001
            logger.warning("Error checking health status", error=str(e))
        return {
            "sdk": True,
            "scheduler": scheduler_healthy,
            "cache": cache_healthy,
            "events": events_healthy,
        }

    def _update_health_metrics(self, components: ComponentHealth | None = None) -> None:
        """Update health metrics for all components in one place.

        Args:
            components: Optional dict of component_name -> is_healthy mappings.
                       Defaults to the shared ``_component_health`` snapshot so
                       the init-time metrics and ``get_health_status`` agree.
        """
        if components is None:
            components = self._component_health()

        # Record health metrics for each component
        for component, is_healthy in components.items():
            self._observability.record_operation(
                "component_health",
                "healthy" if is_healthy else "unhealthy",
                component=component,
                value=1 if is_healthy else -1,
            )

    def get_health_status(self) -> dict[str, object]:
        """Return a local component snapshot and record its metrics.

        This is diagnostic state, not a remote service readiness probe. Optional
        components that have not been initialized report healthy. Cache health
        means only that the SDK has not observed a local cache-state failure;
        this method performs no backend I/O. If component inspection itself
        raises, the pre-inspection healthy value is retained.
        """
        components = self._component_health()
        self._update_health_metrics(components)

        overall_healthy = all(components.values())

        return {
            "status": "healthy" if overall_healthy else "unhealthy",
            "components": components,
            "uptime_seconds": int(time.time() - self._startup_time),
            "inflight_tasks": len(self._inflight_tasks),
            "registered_tools": len(self._tools),
        }

    # Public properties for test and introspection access
    @property
    def observability(self) -> SDKObservability:
        """SDK observability instance."""
        return self._observability

    @property
    def events_client(self) -> CloudEventsClient | None:
        """Lower-level HTTP/SSE client after first connection, otherwise ``None``."""
        return self._events_client

    @events_client.setter
    def events_client(self, value: CloudEventsClient | None) -> None:
        """Set the cloud events client (used for DI in tests)."""
        self._events_client = value

    @property
    def libraries(self) -> LibrariesClient:
        """Core Library and document management client."""
        return self._libraries

    @property
    def registered_tools(self) -> list[RegisteredTool]:
        """Registered tool manifests in deterministic name order."""
        return [self._tools[name].tool.model_copy(deep=True) for name in sorted(self._tools)]

    def _registered_tool_manifest(self) -> tuple[RegisteredTool, ...]:
        """Return the private immutable-membership manifest used by lifecycle publication."""
        return tuple(self._tools[name].tool for name in sorted(self._tools))

    @property
    def cache(self) -> CacheBackend[CacheableValue] | None:
        """Cache backend."""
        return self._cache

    @cache.setter
    def cache(self, value: CacheBackend[CacheableValue] | None) -> None:
        """Set the cache backend."""
        self._cache = value

    @property
    def scheduler(self) -> aiojobs.Scheduler | None:
        """Job scheduler."""
        return self._scheduler

    @property
    def heartbeat_task(self) -> asyncio.Task[None] | None:
        """The heartbeat task, or None if not started."""
        return self._lifecycle.heartbeat_task

    @property
    def backpressure_controller(self) -> BackpressureController:
        """Process-local Task dependency circuit-breaker controller.

        Activity queue bounds live in the same configuration, but this
        controller does not impose a universal limit on Task streams, Library
        operations, or Tool delivery.
        """
        return self._backpressure_controller

    async def _cleanup_scheduler(self) -> None:
        """Close scheduler with graceful shutdown."""
        if self._scheduler and not self._scheduler.closed:
            try:
                # Use wait_and_close for graceful shutdown with longer timeout
                await asyncio.wait_for(self._scheduler.wait_and_close(), timeout=3.0)
            except (asyncio.CancelledError, RuntimeError, AttributeError, asyncio.TimeoutError):
                # Force close if graceful close fails or times out
                try:
                    if self._scheduler and not self._scheduler.closed:
                        await self._scheduler.close()
                except Exception:  # noqa: BLE001
                    pass  # Ignore force close errors
            finally:
                # Clear the scheduler reference
                self._scheduler = None

                # Give scheduler time to fully cleanup
                await asyncio.sleep(0.01)

    async def _cleanup_inflight_tasks(self) -> None:
        """Cancel and wait for in-flight task runners."""
        if not self._inflight_tasks:
            return

        in_flight = list(self._inflight_tasks.values())
        for task in in_flight:
            if not task.done():
                task.cancel()

        with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(
                asyncio.gather(*in_flight, return_exceptions=True),
                timeout=5.0,
            )

        self._inflight_tasks.clear()

    async def _cleanup_tool_result_tasks(self) -> None:
        """Cancel and wait for in-flight tool result submissions."""
        if not self._tool_result_tasks:
            return

        tasks = list(self._tool_result_tasks)
        # Graceful drain: give in-flight tool work the configured window to COMPLETE
        # before cancelling, so a rolling deploy does not cancel a tool mid-side-effect
        # (graceful_shutdown_timeout, default 0.0 = cancel immediately as before).
        if self._graceful_shutdown_timeout > 0:
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=self._graceful_shutdown_timeout,
                )
        for task in tasks:
            if not task.done():
                task.cancel()

        with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=5.0,
            )

        self._tool_result_tasks.clear()
        self._scheduled_tool_uses.clear()

    async def cleanup(self) -> None:
        """Close resources owned directly by this SDK instance.

        This cancels Task runners and Tool-result work, stops lifecycle tasks,
        and closes the scheduler, network transport, cache, and dedicated Tool
        executor. It does not flush or shut down process-global OpenTelemetry
        providers and does not call
        :meth:`SDKObservability.cleanup <dualeai.observability.SDKObservability.cleanup>`.
        """
        # Cancel in-flight task runners first so they stop touching the bridge.
        await self._cleanup_inflight_tasks()
        await self._cleanup_tool_result_tasks()

        # Release the sync-tool thread pool. wait=False: a running (possibly
        # deadline-orphaned) sync tool cannot be cancelled, so do not block shutdown
        # on it; cancel_futures drops any still-queued sync-tool calls.
        if self._sync_tool_executor is not None:
            self._sync_tool_executor.shutdown(wait=False, cancel_futures=True)
            self._sync_tool_executor = None

        # Then cleanup lifecycle tasks + deregister (owned by the lifecycle manager)
        await self._lifecycle.aclose()

        # Cleanup scheduler with enhanced error suppression
        await self._cleanup_scheduler()

        # Cleanup events client connection
        async with self._events_client_lock:
            if self._events_client:
                with contextlib.suppress(Exception):
                    await self._events_client.disconnect()
                self._events_client = None

        if self._cache:
            with contextlib.suppress(Exception):
                await self._cache.close()
            self._cache = None

        # Give async tasks a moment to complete cleanup with shortened delay
        await asyncio.sleep(0.01)

    async def __aenter__(self) -> Self:
        """Async context manager entry."""
        if self.auto_start:
            await self._lifecycle.start()
        return self

    async def __aexit__(
        self, _exc_type: type[BaseException] | None, _exc_val: BaseException | None, _exc_tb: types.TracebackType | None
    ) -> None:
        """Async context manager exit - ensure cleanup."""
        await self.cleanup()
