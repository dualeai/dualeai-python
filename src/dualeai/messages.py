"""Pydantic models for SDK-specific data structures.

Most wire message models (task requests, responses, heartbeats, and related
types) are generated and exposed from the ``dualeai.models`` package.

This file contains SDK-internal models that are not part of the platform schema.

Hosted-agent lifecycle registration uses the generated Bridge models.
"""

from pydantic import BaseModel, ConfigDict, Field

# Note: Agent lifecycle models are generated from the Bridge schema.
# See: dualeai.models.bridge.AgentHeartbeatMessage.


class ErrorContext(BaseModel):
    """Type-safe error context for exception handling and debugging."""

    model_config = ConfigDict(extra="allow")  # Allow extra fields for flexibility

    # Common error context patterns
    error_code: str | None = Field(default=None, description="Machine-readable error code")
    operation: str | None = Field(default=None, description="Operation that failed")
    resource_id: str | None = Field(default=None, description="Resource identifier related to error")
    user_id: str | None = Field(default=None, description="User who triggered the error")
    trace_id: str | None = Field(default=None, description="Distributed tracing identifier")
    request_id: str | None = Field(default=None, description="Request identifier for correlation")

    # Technical context
    stack_trace: str | None = Field(default=None, description="Stack trace for debugging")
    component: str | None = Field(default=None, description="Component where error occurred")
    timestamp: str | None = Field(default=None, description="Error timestamp")
    retry_attempt: int | None = Field(default=None, ge=0, description="Retry attempt number")

    # Business context
    task_id: str | None = Field(default=None, description="Task identifier if applicable")
    agent_id: str | None = Field(default=None, description="Agent identifier if applicable")

    # Infrastructure context
    service_name: str | None = Field(default=None, description="Service name where error occurred")
    version: str | None = Field(default=None, description="Service version")
    environment: str | None = Field(default=None, description="Environment (dev/staging/prod)")

    # Validation error context (from test usage)
    field: str | None = Field(default=None, description="Field name that caused validation error")
    value: str | None = Field(default=None, description="Invalid value that caused error")
    expected_type: str | None = Field(default=None, description="Expected type for validation")

    # For truly dynamic error data that doesn't fit standard fields
    metadata: dict[str, str | int | float | bool] | None = Field(
        default_factory=dict, description="Additional error metadata"
    )
