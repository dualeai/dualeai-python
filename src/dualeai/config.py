"""Pydantic settings for the Duale AI SDK.

Task requests authenticate with ``token`` at ``endpoint``. Library operations
also require ``tenant_id`` because it is part of their URL. Hosted-tool
lifecycle operations require ``agent_id``; the attachment convenience API can
instead use an explicit agent id or the sole legacy agent registered locally.

Local validation and environment-loading behavior is covered by
``tests/test_config.py`` and ``tests/test_token_removal_validation.py``.
"""

from pathlib import Path

from dotenv import find_dotenv
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ObservabilityConfig(BaseModel):
    """OpenTelemetry observability configuration."""

    endpoint: str = Field(
        description=(
            "Base OTLP/HTTP endpoint for SDK traces and metrics. The SDK does not "
            "install an OTLP log exporter; host-application logging remains separate."
        ),
    )
    token: str | None = Field(
        default=None,
        description=(
            "Bearer token sent to the OTLP endpoint. Setting it enables SDK telemetry; "
            "do not reuse the Duale AI API token."
        ),
    )

    def get_headers(self) -> dict[str, str]:
        """Get OTLP headers with authentication token if available."""
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}


class DualeAIConfig(BaseSettings):
    """Configuration loaded from arguments, environment variables, and ``.env``.

    Task transport uses HTTP and Server-Sent Events:
    - POST /v1/tasks/{task_id} → SSE stream (create task, type=create)
    - POST /v1/tasks/{task_id} → SSE stream (tool results, type=tool_results)
    - POST /v1/tasks/{task_id} → SSE stream (continue, type=continue)
    - GET /v1/tasks/{task_id} → SSE stream (reconnect)

    ``tenant_id`` and ``agent_id`` are not interchangeable with the API token:
    Library URL construction needs ``tenant_id`` and hosted-tool lifecycle calls
    need ``agent_id``. Ordinary task requests need only the token and endpoint.

    Environment variables:
    - DUALEAI_TOKEN: API token for HTTP bridge authentication; starts with dualeai_ [required]
    - DUALEAI_ENDPOINT: Duale AI API endpoint URL [optional]
    - DUALEAI_TENANT_ID: Library tenant path segment [required for Library operations]
    - DUALEAI_AGENT_ID: Provisioned identity [required for hosted Tools unless passed to the SDK]
    - DUALEAI_REDIS_URL: Redis server URL for caching [optional]
    - DUALEAI_OBSERVABILITY__ENDPOINT: OTLP endpoint URL [optional]
    - DUALEAI_OBSERVABILITY__TOKEN: OTLP authentication token [optional]
    """

    model_config = SettingsConfigDict(
        env_file=find_dotenv(),  # Search for .env file iteratively in parent directories
        env_nested_delimiter="__",  # Support OBSERVABILITY__ENDPOINT format
        env_prefix="DUALEAI_",  # Prefix specific to Duale AI SDK
        extra="ignore",  # Ignore extra env vars
        use_attribute_docstrings=True,
    )

    # Task API authentication.
    # Default None — pydantic-settings fills from DUALEAI_TOKEN env var.
    # Validator ensures a valid token is present after all sources are loaded.
    token: str | None = Field(
        default=None,
        min_length=10,
        max_length=256,
        description=(
            "API token for HTTP bridge authentication; starts with dualeai_. "
            "Provided through the Duale AI workspace access handoff. "
            "Set via DUALEAI_TOKEN environment variable."
        ),
    )

    @field_validator("token")
    @classmethod
    def validate_token_present_and_format(cls, v: str | None) -> str:
        """Validate token is provided (from env or constructor) and has correct format."""
        if v is None:
            raise ValueError(
                "token is required. Set DUALEAI_TOKEN environment variable or pass token= to DualeAIConfig(). "
                "Obtain a token through your Duale AI workspace access handoff."
            )
        if not v.startswith("dualeai_"):
            raise ValueError(
                "token must start with 'dualeai_'. Obtain a valid token through your Duale AI workspace access handoff."
            )
        return v

    # Duale AI API endpoint
    endpoint: str = Field(
        default="https://api.duale.ai",
        description="Shared API endpoint for task, lifecycle, and Library requests",
    )

    tenant_id: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Tenant URL segment required by Library operations; ordinary task requests do not read this field."
        ),
    )

    agent_id: str | None = Field(
        default=None,
        min_length=3,
        max_length=50,
        pattern="^[a-zA-Z][a-zA-Z0-9_-]*$",
        description=(
            "Provisioned agent identifier used by hosted-tool lifecycle calls. "
            "Attachment uploads do not select it automatically; pass sdk.agent_id explicitly when desired."
        ),
    )

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, v: str) -> str:
        """Validate endpoint is valid HTTP(S) URL."""
        if not v.startswith(("http://", "https://")):
            raise ValueError("endpoint must be an HTTP or HTTPS URL")
        return v.rstrip("/")

    # Cache configuration
    redis_url: str = Field(
        default="redis://localhost:8010",
        description="Redis server URL for caching; use rediss:// for TLS",
    )

    @field_validator("redis_url")
    @classmethod
    def validate_redis_url(cls, v: str) -> str:
        """Validate Redis URL format."""
        if not v.startswith(("redis://", "rediss://")):
            raise ValueError("Redis URL must start with redis:// or rediss://")
        return v

    sqlite_path: Path | None = Field(
        default=None,
        description="SQLite database path for local caching fallback",
    )

    # SDK configuration
    debug: bool = Field(
        default=False,
        description="Enable debug mode for verbose logging",
    )

    # Observability configuration (consolidated)
    observability: ObservabilityConfig = Field(
        # 4318 is the OTLP/HTTP port; the SDK ships HTTP exporters (4317 is gRPC).
        default_factory=lambda: ObservabilityConfig(endpoint="http://localhost:4318"),
        description="OpenTelemetry observability configuration",
    )

    @property
    def otel_endpoint(self) -> str:
        """Shortcut for observability.endpoint."""
        return self.observability.endpoint

    # The OTLP/HTTP exporters use an explicitly passed endpoint AS-IS (per-signal
    # paths are appended only to the generic env-var fallback), so the SDK must
    # hand each exporter its own /v1/<signal> URL.
    @property
    def otel_traces_endpoint(self) -> str:
        """Span exporter URL: base observability endpoint plus /v1/traces."""
        return f"{self.observability.endpoint.rstrip('/')}/v1/traces"

    @property
    def otel_metrics_endpoint(self) -> str:
        """Metric exporter URL: base observability endpoint plus /v1/metrics."""
        return f"{self.observability.endpoint.rstrip('/')}/v1/metrics"

    @property
    def otel_headers(self) -> dict[str, str]:
        """Shortcut for observability headers."""
        return self.observability.get_headers()
