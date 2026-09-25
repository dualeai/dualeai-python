"""Pydantic settings for the Duale AI SDK.

Task and Agent requests use the Bridge hpke-http/3 endpoint derived from the
HTTPS Gateway base. Library metadata requests use a separate protected
endpoint with bearer authorization inside HPKE; ``tenant_id`` is part of their
URL. Hosted-tool lifecycle operations require ``agent_id``. Attachment uploads
use the configured Agent ID unless a per-call ID is supplied.

Local validation and environment-loading behavior is covered by
``tests/test_config.py`` and ``tests/test_token_removal_validation.py``.
"""

from pathlib import Path
from urllib.parse import urlsplit

from dotenv import find_dotenv
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_HPKE_PSK_MIN_BYTES = 32


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

    Task operations are logical requests inside HPKE. The outer discovery GET
    and encrypted POST use the fixed /http-bridge/v1/hpke endpoint:
    - POST /http-bridge/v1/hpke/tasks/{task_id} → SSE stream (create task, type=create)
    - POST /http-bridge/v1/hpke/tasks/{task_id} → SSE stream (tool results, type=tool_results)
    - POST /http-bridge/v1/hpke/tasks/{task_id} → SSE stream (continue, type=continue)
    - GET /http-bridge/v1/hpke/tasks/{task_id} → SSE stream (reconnect)

    ``tenant_id`` and ``agent_id`` are not interchangeable with the API token:
    Library logical routes need ``tenant_id`` and hosted-tool lifecycle calls
    need ``agent_id``. Ordinary task requests need only the token and endpoint.

    Environment variables:
    - DUALEAI_TOKEN: API token for protected API calls; starts with dualeai_ [required]
    - DUALEAI_ENDPOINT: HTTPS Gateway base URL [optional]
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

    # Protected API authentication.
    # Default None — pydantic-settings fills from DUALEAI_TOKEN env var.
    # Validator ensures a valid token is present after all sources are loaded.
    token: str | None = Field(
        default=None,
        max_length=256,
        description=(
            "API token used as the hpke-http/3 PSK; starts with dualeai_ and has at least 32 UTF-8 bytes. "
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
        if len(v.encode("utf-8")) < _HPKE_PSK_MIN_BYTES:
            raise ValueError("token must contain at least 32 UTF-8 bytes for hpke-http/3")
        return v

    # Gateway base for the two protected service endpoints.
    endpoint: str = Field(
        default="https://api.duale.ai",
        description="HTTPS Gateway base URL for protected Task, Agent, and Library metadata APIs",
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
            "Attachment uploads use it unless an agent_id is supplied for that call."
        ),
    )

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, v: str) -> str:
        """Validate the HTTPS Gateway base URL."""
        parts = urlsplit(v)
        if (
            parts.scheme != "https"
            or not parts.netloc
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise ValueError("endpoint must be an absolute HTTPS URL without credentials, query, or fragment")
        if parts.path not in ("", "/"):
            raise ValueError("endpoint must be an HTTPS Gateway base URL without a path")
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
