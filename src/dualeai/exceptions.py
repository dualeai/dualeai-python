"""Custom exceptions for Duale AI SDK with rich context and debugging support."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from dualeai.constants import ErrorMessages
from dualeai.messages import ErrorContext
from dualeai.models.problem_details import ProblemDetails
from dualeai.utils import get_exception_type_name

if TYPE_CHECKING:
    from dualeai.models.library import LibraryDocumentCreateResponse

# Pre-create TypeAdapter for performance optimization (5-11x improvement)
_error_context_adapter: TypeAdapter[ErrorContext] = TypeAdapter(ErrorContext)
ErrorContextInput = ErrorContext | Mapping[str, object] | None


def _build_error_context(context: ErrorContextInput, **additional_fields: object) -> dict[str, object]:
    """Build error context dictionary with consistent handling.

    Args:
        context: Base context (ErrorContext or dict)
        **additional_fields: Additional fields to include in context

    Returns:
        Dict with merged context and additional fields
    """
    # Handle context conversion
    full_context: dict[str, object] = (
        _error_context_adapter.dump_python(context) if isinstance(context, ErrorContext) else dict(context or {})
    )

    # Add additional fields
    full_context |= {key: value for key, value in additional_fields.items() if value is not None}

    return full_context


class DualeAIError(Exception):
    """Base exception for all Duale AI SDK errors with context support."""

    @staticmethod
    def _create_default_context() -> ErrorContext:
        """Create default error context with minimal values."""
        return ErrorContext(
            error_code="UNKNOWN_ERROR",
            operation="unknown_operation",
        )

    def __init__(
        self,
        message: str,
        context: ErrorContextInput = None,
        **additional_fields: object,
    ) -> None:
        """Initialize DualeAIError.

        Args:
            message: Error message
            context: Additional context about the error (ErrorContext or dict)
            **additional_fields: Additional fields to merge into context
        """
        super().__init__(message)
        self.message = message
        # RFC 9457 ProblemDetails attached when the SDK lifts an error event
        # from the bridge `task.error` SSE channel (see `response._exception_from_terminal`).
        # `None` when the exception originates client-side (validation, transport,
        # config).
        #
        # Cloudflare RFC 9457 §3.2 extension fields live on the envelope:
        #     exc.problem_details.retryable
        #     exc.problem_details.retry_after_seconds
        #     exc.problem_details.owner_action_required
        #     exc.problem_details.error_category
        #
        # Per-error context (field path, ai_hints) lives on
        # `exc.problem_details.errors[i]`. Do NOT read the ext fields from
        # `errors[i]` — they are envelope-only (older SDK docs incorrectly
        # placed them under `errors[0]`).
        self.problem_details: ProblemDetails | None = None

        # Build context with additional fields if provided
        resolved_context: ErrorContextInput = context
        if additional_fields:
            resolved_context = _build_error_context(context, **additional_fields)

        # Convert dict to ErrorContext if needed for type safety
        if isinstance(resolved_context, Mapping):
            self.context = _error_context_adapter.validate_python(resolved_context)
        elif resolved_context is None:
            self.context = self._create_default_context()
        else:
            self.context = resolved_context

    def __str__(self) -> str:
        """String representation with context."""
        context_dict = _error_context_adapter.dump_python(self.context, exclude_none=True)
        if context_dict:
            context_str = ", ".join(f"{k}={v!r}" for k, v in context_dict.items())
            return f"{self.message} (context: {context_str})"
        return self.message


class BusinessError(DualeAIError):
    """Raised when a business rule is violated."""


class LibraryUploadError(DualeAIError):
    """A local or object-store upload failed before the batch completed."""

    def __init__(
        self,
        message: str,
        *,
        attachment_key: str,
        part_number: int | None = None,
        completed_receipts: Mapping[str, LibraryDocumentCreateResponse] | None = None,
    ) -> None:
        receipts = dict(completed_receipts or {})
        super().__init__(
            message,
            attachment_key=attachment_key,
            part_number=part_number,
            completed_count=len(receipts),
        )
        self.attachment_key = attachment_key
        self.part_number = part_number
        self.completed_receipts = receipts

    def with_completed_receipts(
        self,
        receipts: Mapping[str, LibraryDocumentCreateResponse],
    ) -> LibraryUploadError:
        """Return the same failure enriched with receipts completed by its batch."""
        enriched = LibraryUploadError(
            self.message,
            attachment_key=self.attachment_key,
            part_number=self.part_number,
            completed_receipts=receipts,
        )
        enriched.problem_details = self.problem_details
        return enriched


class RoutingError(DualeAIError):
    """Raised when agent routing fails."""

    def __init__(
        self,
        message: str,
        skills: list[str] | None = None,
        context: ErrorContextInput = None,
    ):
        """Initialize RoutingError.

        Args:
            message: Error message
            skills: Skills that were required for routing
            context: Additional context
        """
        super().__init__(message, context, required_skills=skills)
        self.skills = skills


class TaskTimeoutError(DualeAIError):
    """Raised when a task execution times out."""

    def __init__(
        self,
        message: str,
        task_id: str | None = None,
        timeout_seconds: float | None = None,
        context: ErrorContextInput = None,
    ):
        """Initialize TaskTimeoutError.

        Args:
            message: Error message
            task_id: ID of the task that timed out
            timeout_seconds: Timeout value in seconds
            context: Additional context
        """
        super().__init__(message, context, task_id=task_id, timeout_seconds=timeout_seconds)
        self.task_id = task_id
        self.timeout_seconds = timeout_seconds


class ActivityTimeoutError(TaskTimeoutError):
    """Raised when an activity execution times out."""

    def __init__(
        self,
        message: str,
        activity_name: str | None = None,
        timeout_seconds: float | None = None,
        context: ErrorContextInput = None,
    ):
        """Initialize ActivityTimeoutError.

        Args:
            message: Error message
            activity_name: Name of the activity that timed out
            timeout_seconds: Timeout value in seconds
            context: Additional context
        """
        super().__init__(message, task_id=None, timeout_seconds=timeout_seconds, context=context)
        self.activity_name = activity_name


class CacheError(DualeAIError):
    """Raised when cache operations fail."""


class CacheConnectionError(CacheError):
    """Raised when cache connection fails."""

    def __init__(
        self,
        message: str,
        backend_type: str | None = None,
        connection_url: str | None = None,
        context: ErrorContextInput = None,
    ):
        """Initialize CacheConnectionError.

        Args:
            message: Error message
            backend_type: Type of cache backend (redis, sqlite)
            connection_url: Connection URL (sanitized)
            context: Additional context
        """
        # Sanitize URL to remove credentials
        sanitized_url = None
        if connection_url:
            sanitized_url = connection_url.split("@")[-1] if "@" in connection_url else connection_url

        super().__init__(message, context, backend_type=backend_type, connection_url=sanitized_url)
        self.backend_type = backend_type


class CacheSerializationError(CacheError):
    """Raised when cache serialization/deserialization fails."""

    def __init__(
        self,
        message: str,
        cache_key: str | None = None,
        data_type: str | None = None,
        context: ErrorContextInput = None,
    ):
        """Initialize CacheSerializationError.

        Args:
            message: Error message
            cache_key: Cache key that failed
            data_type: Type of data being serialized
            context: Additional context
        """
        super().__init__(message, context, cache_key=cache_key, data_type=data_type)
        self.cache_key = cache_key


class MessagingError(DualeAIError):
    """Raised when messaging operations fail."""


class StreamingError(MessagingError):
    """Streaming quality degraded beyond acceptable threshold.

    Raised when message drop percentage exceeds configured limit,
    indicating network issues or consumer performance problems.
    """


class MessagingConnectionError(MessagingError):
    """Raised when transport connection fails."""

    def __init__(
        self,
        message: str,
        endpoint: str | None = None,
        context: ErrorContextInput = None,
    ):
        """Initialize MessagingConnectionError.

        Args:
            message: Error message
            endpoint: Transport endpoint URL
            context: Additional context
        """
        super().__init__(message, context, endpoint=endpoint)
        self.endpoint = endpoint


class DualeAIConnectionError(MessagingError):
    """Raised when connection to HTTP bridge or other services fails."""


class DualeAIAuthError(DualeAIError):
    """Raised when authentication fails (invalid/expired API key, missing credentials)."""


class TransportUnavailableError(MessagingConnectionError):
    """Raised when transport backend is unavailable or connection is lost.

    Provides user-friendly error messages for common connection issues.
    """

    def __init__(
        self,
        original_error: Exception | None = None,
        endpoint: str | None = None,
        context: ErrorContextInput = None,
    ):
        """Initialize TransportUnavailableError with helpful context.

        Args:
            original_error: The original transport error
            endpoint: Transport endpoint that failed
            context: Additional error context
        """
        # Parse the original error to provide helpful guidance
        error_str = str(original_error) if original_error else "Unknown error"

        # Build user-friendly error message based on common errors
        if "Connect call failed" in error_str or "Connection refused" in error_str:
            message = ErrorMessages.CONNECTION_FAILED + " Please try again later."
        elif error_str and "connection closed" in error_str.lower():
            message = ErrorMessages.CONNECTION_LOST
        elif error_str and "empty response from server" in error_str.lower():
            message = "Service is not responding. The service may be starting up. Please wait and retry."
        elif "unexpected EOF" in error_str:
            message = ErrorMessages.CONNECTION_INTERRUPTED
        else:
            message = "Unable to communicate with service. Please try again later."

        context_fields = _build_error_context(context)
        context_fields["metadata"] = {
            "original_error": error_str,
            "error_type": get_exception_type_name(original_error) if original_error else "Unknown",
            "endpoint": endpoint or "unknown",
        }

        super().__init__(message, endpoint=endpoint, context=context_fields)
        self.original_error = original_error


class DualeAITimeoutError(TaskTimeoutError):
    """Raised when an operation times out."""


class TaskStoppedError(DualeAIError):
    """Raised when an agent stopped the task before it produced a result.

    A stop is not a failure of the task, so it carries no problem details — only
    the reason the caller supplied.
    """

    def __init__(
        self,
        reason: str,
        task_id: str | None = None,
        context: ErrorContextInput = None,
    ):
        """Initialize with the reason recorded on the stop.

        Args:
            reason: Why the task was stopped.
            task_id: Task that was stopped.
            context: Additional error context.
        """
        context_fields = _build_error_context(context)
        if task_id is not None:
            context_fields["metadata"] = {"task_id": task_id}
        super().__init__(f"Task stopped: {reason}", context_fields)
        self.reason = reason
        self.task_id = task_id


class AgentRegistrationError(DualeAIError):
    """Raised when agent registration fails."""

    def __init__(
        self,
        message: str,
        agent_id: str | None = None,
        agent_name: str | None = None,
        context: ErrorContextInput = None,
    ):
        """Initialize AgentRegistrationError.

        Args:
            message: Error message
            agent_id: ID of the agent
            agent_name: Name of the agent
            context: Additional context
        """
        super().__init__(message, context, agent_id=agent_id, agent_name=agent_name)
        self.agent_id = agent_id
        self.agent_name = agent_name


class TaskSubmissionError(DualeAIError):
    """Raised when task submission fails."""

    def __init__(
        self,
        message: str,
        action: str | None = None,
        skills: list[str] | None = None,
        context: ErrorContextInput = None,
    ):
        """Initialize TaskSubmissionError.

        Args:
            message: Error message
            action: Action that was being submitted
            skills: Skills required for the task
            context: Additional context
        """
        super().__init__(message, context, action=action, required_skills=skills)
        self.action = action
        self.skills = skills


class ConfigurationError(DualeAIError):
    """Raised when SDK configuration is invalid."""

    def __init__(
        self,
        message: str,
        config_key: str | None = None,
        config_value: str | float | bool | None = None,  # noqa: FBT001
        context: ErrorContextInput = None,
    ):
        """Initialize ConfigurationError.

        Args:
            message: Error message
            config_key: Configuration key that's invalid
            config_value: Invalid configuration value
            context: Additional context
        """
        super().__init__(message, context, config_key=config_key, config_value=config_value)
        self.config_key = config_key
        self.config_value = config_value


class ValidationError(DualeAIError):
    """Raised when data validation fails."""

    def __init__(
        self,
        message: str,
        field_name: str | None = None,
        field_value: str | float | bool | list[str] | dict[str, str | int | float | bool] | None = None,  # noqa: FBT001
        validation_rule: str | None = None,
        context: ErrorContextInput = None,
    ):
        """Initialize ValidationError.

        Args:
            message: Error message
            field_name: Name of the field that failed validation
            field_value: Value that failed validation
            validation_rule: Rule that was violated
            context: Additional context
        """
        super().__init__(
            message, context, field_name=field_name, field_value=field_value, validation_rule=validation_rule
        )
        self.field_name = field_name
        self.field_value = field_value
        self.validation_rule = validation_rule
