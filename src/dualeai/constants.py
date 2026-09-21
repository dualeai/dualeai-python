"""Centralized constants for timing, limits, and configuration values."""


class TimingDefaults:
    """Centralized timing configuration for consistent timeout/retry behavior.

    These constants eliminate hardcoded values and provide platform-wide consistency
    for timeout, retry, and delay behaviors. Critical settings are optimized for
    low-latency, real-time streaming performance.

    Performance Impact:
    - Streaming timeouts (50ms) enable sub-second UI updates
    - HTTP timeouts tuned for SSE long-polling
    - Max backoff (10s) prevents excessive delays during recovery
    """

    DEFAULT_TASK_TIMEOUT_SECONDS = 1800
    """Default task deadline in seconds (30 min). Agentic workflows with web
    search, code execution, and multi-step reasoning need 5-15 min typically;
    30 min gives headroom for retries and complex orchestration. Callers can
    override via the ``deadline`` parameter on ``ask()`` / ``submit_task()``."""

    DEFAULT_MAX_JOBS = 100
    """Default concurrent in-flight task cap for the SDK scheduler."""

    HEARTBEAT_INTERVAL_SECONDS = 60.0
    """Agent heartbeat interval."""
    HEARTBEAT_JITTER_SECONDS = 15.0
    """Agent heartbeat jitter bound."""
    MAX_CONSECUTIVE_HEARTBEAT_FAILURES = 3
    """Transient heartbeat failures tolerated before serve() gives up.

    At the 60s cadence, 3 consecutive misses give up ~180s after the last
    success — inside the platform's routing band (RFC-121: online < 150s, offline
    at 240s), so a single network blip does not tear down serve() yet a genuinely
    dead link surfaces before the platform would keep routing to a zombie. A
    single auth failure is tolerated once (see
    MAX_CONSECUTIVE_HEARTBEAT_AUTH_FAILURES) so a token-rotation blip does not
    tear down serve().
    """
    MAX_CONSECUTIVE_HEARTBEAT_AUTH_FAILURES = 2
    """Consecutive auth-rejected heartbeats tolerated before serve() gives up.

    One transient 403 (a token-rotation blip or a brief Profile authz-cache deny)
    is retried on the next beat; a second consecutive 403 is a hard revocation and
    stops serve().
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
    """HTTP transport default configuration (RFC-051)."""

    ACCEPT_SSE = "text/event-stream"
    """Accept header for SSE streams."""

    ACCEPT_ENCODING = "zstd, gzip, deflate"
    """Accept-Encoding header — RFC 8878 zstd support."""


class ErrorMessages:
    """Common error message templates for consistency."""

    CONNECTION_LOST = "Lost connection to API. This is usually temporary. Please retry your request."
    """Connection lost error message."""

    CONNECTION_FAILED = "Unable to connect to API. The service may be temporarily unavailable."
    """Connection failed error message."""

    CONNECTION_INTERRUPTED = "Connection to API was interrupted. Please retry your request."
    """Connection interrupted error message."""


class CacheLimits:
    """Cache system limits and performance thresholds."""

    MAX_KEY_LENGTH: int = 250
    """Practical limit for Redis keys."""


class DisplayLimits:
    """Truncation limits for logging and display."""

    ERROR_PREVIEW_LENGTH: int = 200
    """Maximum length for error preview in logs."""
