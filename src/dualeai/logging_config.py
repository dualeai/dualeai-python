"""Logging configuration for Duale AI SDK."""

import logging
import sys
import time
from collections.abc import Generator, MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar

import structlog

# Type alias for logging context values
LogContextValue = str | int | float | bool | list[str] | None
StructuredLogger = structlog.stdlib.BoundLogger

# Performance thresholds (ms)
_SLOW_THRESHOLD = 1000
_MODERATE_THRESHOLD = 100

# Context variables for automatic context propagation
_current_task_id: ContextVar[str | None] = ContextVar("task_id", default=None)
_current_tenant_id: ContextVar[str | None] = ContextVar("tenant_id", default=None)
_current_operation: ContextVar[str | None] = ContextVar("operation", default=None)


# Custom processors for structlog
def add_task_context(_: object, __: str, event_dict: MutableMapping[str, object]) -> MutableMapping[str, object]:
    """Add current task context to all log entries."""
    task_id = _current_task_id.get()
    if task_id:
        event_dict["task_id"] = task_id

    tenant_id = _current_tenant_id.get()
    if tenant_id:
        event_dict["tenant_id"] = tenant_id

    operation = _current_operation.get()
    if operation:
        event_dict["operation"] = operation

    return event_dict


def add_performance_context(_: object, __: str, event_dict: MutableMapping[str, object]) -> MutableMapping[str, object]:
    """Add performance timing context when available."""
    # Add operation timing if this is a performance log
    duration_ms = event_dict.get("duration_ms")
    if duration_ms is not None and isinstance(duration_ms, int | float):
        # Categorize performance
        if duration_ms > _SLOW_THRESHOLD:
            event_dict["perf_category"] = "slow"
        elif duration_ms > _MODERATE_THRESHOLD:
            event_dict["perf_category"] = "moderate"
        else:
            event_dict["perf_category"] = "fast"

    return event_dict


def add_flow_metadata(_: object, __: str, event_dict: MutableMapping[str, object]) -> MutableMapping[str, object]:
    """Add flow-specific metadata for better tracing."""
    # Add flow stage indicators
    flow_step = event_dict.get("flow_step")
    if flow_step is not None and isinstance(flow_step, str):
        if flow_step.endswith("_start") or flow_step == "task_submission":
            event_dict["flow_stage"] = "initiate"
        elif flow_step.endswith("_end") or "complete" in flow_step:
            event_dict["flow_stage"] = "finalize"
        else:
            event_dict["flow_stage"] = "process"

    # Add component indicator based on logger name
    logger_name = event_dict.get("logger")
    if isinstance(logger_name, str):
        if "sdk" in logger_name:
            event_dict["component"] = "sdk"
        elif "events" in logger_name:
            event_dict["component"] = "messaging"
        elif "orchestrator" in logger_name:
            event_dict["component"] = "orchestrator"
        else:
            event_dict["component"] = "system"
    else:
        event_dict["component"] = "system"

    return event_dict


def configure_logging(level: str | int = logging.ERROR) -> None:
    """Configure structured console logging.

    This sets up structlog with defaults that provide:
    - Structured key-value logging
    - Consistent error formatting with stack traces
    - Each exception traceback rendered exactly once
    - Console format for readability
    - Proper stdlib integration

    Args:
        level: Application logging level (DEBUG, INFO, WARNING, ERROR)
    """
    # Convert string level to int if needed using a type-safe mapping
    if isinstance(level, str):
        level_map = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL,
        }
        level = level_map.get(level.upper(), logging.ERROR)

    # Shared pre-render processors. They run as the front of the structlog chain
    # AND as ProcessorFormatter's foreign_pre_chain, so structlog-native and
    # foreign stdlib records (aiohttp, aiojobs, asyncio) carry the same columns.
    shared_processors: list[structlog.typing.Processor] = [
        # Add log level
        structlog.stdlib.add_log_level,
        # Add logger name
        structlog.stdlib.add_logger_name,
        # Always add timestamp
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
        # Custom context processors (STRUCTLOG POWER!)
        add_task_context,  # Auto-inject task context
        add_performance_context,  # Auto-categorize performance
        add_flow_metadata,  # Auto-add component/stage info
    ]

    # Add caller information in debug mode
    if level == logging.DEBUG:
        shared_processors.append(
            structlog.processors.CallsiteParameterAdder(
                parameters=[
                    structlog.processors.CallsiteParameter.FILENAME,
                    structlog.processors.CallsiteParameter.LINENO,
                    structlog.processors.CallsiteParameter.FUNC_NAME,
                ]
            )
        )

    # The chain ends at wrap_for_formatter, which hands the event dict to the
    # stdlib ProcessorFormatter on the handler — the single place exceptions
    # render. Do NOT put format_exc_info + a renderer here: BoundLogger leaves
    # exc_info on the emitted LogRecord, so a renderer in the chain plus the
    # stdlib formatter would each render the traceback (the double-output bug).
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Set up the root logger
    root_logger = logging.getLogger()

    # Clear any existing handlers
    root_logger.handlers = []

    # Console handler renders exactly once, via ProcessorFormatter: it consumes
    # record.exc_info (nulling it after capture) so the stdlib Formatter cannot
    # re-render the traceback on top of ConsoleRenderer's.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.dev.ConsoleRenderer(colors=True),
            ],
        )
    )

    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    # Set level for noisy libraries to reduce spam
    logging.getLogger("aiohttp").setLevel(max(level, logging.WARNING))
    logging.getLogger("aiojobs").setLevel(max(level, logging.WARNING))
    logging.getLogger("asyncio").setLevel(max(level, logging.WARNING))


def get_logger(name: str | None = None) -> StructuredLogger:
    """Get a structured logger instance.

    Args:
        name: Logger name (usually __name__)

    Returns:
        Structured logger instance
    """
    return structlog.get_logger(name)


# Operation start times for FlowLogger duration calculation
_timing_cache: dict[str, float] = {}


class FlowLogger:
    """Simplified flow logging using context managers and bound loggers."""

    @staticmethod
    def start(logger: StructuredLogger, operation: str, task_id: str, **context: LogContextValue) -> None:
        """Log flow start with automatic timing."""
        _timing_cache[f"{task_id}:{operation}"] = time.time()
        logger.info(f"FLOW_START: {operation}", **context)

    @staticmethod
    def step(logger: StructuredLogger, operation: str, step: str, **context: LogContextValue) -> None:
        """Log a flow step."""
        logger.info(f"FLOW_STEP: {operation} → {step}", flow_step=step, **context)

    @staticmethod
    def end(
        logger: StructuredLogger,
        operation: str,
        task_id: str,
        success: bool = True,  # noqa: FBT001, FBT002
        **context: LogContextValue,
    ) -> None:
        """Log flow end with duration calculation."""
        duration = None
        cache_key = f"{task_id}:{operation}"
        if start_time := _timing_cache.pop(cache_key, None):
            duration = round((time.time() - start_time) * 1000, 2)

        status = "SUCCESS" if success else "FAILED"
        logger.info(f"FLOW_END: {operation} → {status}", flow_status=status, duration_ms=duration, **context)


# Context managers for automatic context setting
@contextmanager
def task_context(task_id: str, tenant_id: str | None = None) -> Generator[None, None, None]:
    """Automatically set task and tenant context for all logs within the block."""
    tokens = [_current_task_id.set(task_id)]
    if tenant_id:
        tokens.append(_current_tenant_id.set(tenant_id))

    try:
        yield
    finally:
        _current_task_id.reset(tokens[0])
        if len(tokens) > 1:
            _current_tenant_id.reset(tokens[1])


@contextmanager
def operation_context(operation: str) -> Generator[None, None, None]:
    """Automatically set operation context for all logs within the block."""
    token = _current_operation.set(operation)
    try:
        yield
    finally:
        _current_operation.reset(token)


def get_bound_logger(name: str, **persistent_context: LogContextValue) -> StructuredLogger:
    """Get a structlog logger with persistent context bound to it.

    This leverages structlog's bind() functionality to create loggers
    that always include the specified context in every log message.
    """
    logger = structlog.get_logger(name)

    # Bind persistent context to the logger
    if persistent_context:
        logger = logger.bind(**persistent_context)

    return logger


def get_task_logger(name: str, task_id: str, **extra_context: LogContextValue) -> StructuredLogger:
    """Get a bound logger with task context pre-configured."""
    return get_bound_logger(name, task_id=task_id, **extra_context)
