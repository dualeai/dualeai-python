"""Centralized timing, limit, and configuration values.

Values used as public defaults are covered by ``tests/test_constants.py`` and
the feature tests that consume them.
"""


class TimingDefaults:
    """Default SDK deadlines, lifecycle intervals, and retry delays."""

    DEFAULT_TASK_TIMEOUT_SECONDS = 1800
    """Default task deadline in seconds (30 min). Agentic workflows with web
    search, code execution, and multi-step reasoning need 5-15 min typically;
    30 min gives headroom for retries and complex orchestration. Callers can
    override via the ``deadline`` parameter on ``ask()`` / ``submit_task()``."""

    DEFAULT_MAX_JOBS = 100
    """Default activity concurrency and, unless overridden, Tool concurrency."""

    HEARTBEAT_INTERVAL_SECONDS = 60.0
    """Agent heartbeat interval."""
    HEARTBEAT_JITTER_SECONDS = 15.0
    """Agent heartbeat jitter bound."""
    MAX_CONSECUTIVE_HEARTBEAT_FAILURES = 3
    """Transient heartbeat failures tolerated before serve() gives up.

    With the default interval, three consecutive failures stop the lifecycle
    loop after roughly three minutes plus jitter. Authentication failures use
    the separate, lower limit below.
    """
    MAX_CONSECUTIVE_HEARTBEAT_AUTH_FAILURES = 2
    """Consecutive auth-rejected heartbeats tolerated before serve() gives up.

    One rejected heartbeat is retried on the next interval; a second
    consecutive rejection stops ``serve()``.
    """
    REGISTRATION_REFRESH_INTERVAL_SECONDS = 600.0
    """Full manifest anti-entropy refresh interval."""
    REGISTRATION_REFRESH_JITTER_SECONDS = 60.0
    """Full manifest anti-entropy refresh jitter bound."""
    TOOL_USE_DEDUP_REPLAY_MARGIN_SECONDS = 300.0
    """How long to remember completed tool-use ids after their deadline."""
    TOOL_RETRY_BACKOFF_MULTIPLIER_SECONDS = 0.1
    """Full-jitter exponential backoff base for opt-in tool retry (gRPC initialBackoff)."""
    TOOL_RETRY_BACKOFF_MAX_SECONDS = 1.0
    """Full-jitter exponential backoff ceiling for opt-in tool retry (gRPC maxBackoff)."""

    TEST_TIMEOUT_SECONDS = 5.0
    """Default test timeout."""


class HTTPDefaults:
    """HTTP transport header defaults."""

    ACCEPT_SSE = "text/event-stream"
    """Accept header for SSE streams."""


class CacheLimits:
    """Cache system limits and performance thresholds."""

    MAX_KEY_LENGTH: int = 250
    """Practical limit for Redis keys."""


class DisplayLimits:
    """Truncation limits for logging and display."""

    ERROR_PREVIEW_LENGTH: int = 200
    """Maximum length for error preview in logs."""
