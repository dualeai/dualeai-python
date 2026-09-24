"""Package-defined exceptions and their structured context values.

Public SDK calls can also raise built-in exceptions, Pydantic validation
errors, and ``asyncio.CancelledError`` as documented by each operation.

Exception hierarchy and context behavior is covered by
``tests/test_exceptions.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from dualeai.messages import ErrorContext
from dualeai.models.problem_details import ProblemDetails

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
    """Base class for package-defined SDK exceptions with structured context."""

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
        # RFC 9457 ProblemDetails attached when the SDK lifts an error response
        # or a `task.error` SSE event (see `response._exception_from_terminal`).
        # `None` when the exception originates client-side (validation, transport,
        # config).
        #
        # Platform extension fields live on the ProblemDetails envelope:
        #     exc.problem_details.retryable
        #     exc.problem_details.retry_after_seconds
        #     exc.problem_details.owner_action_required
        #     exc.problem_details.error_category
        #
        # Per-error context (field path, ai_hints) lives on
        # `exc.problem_details.errors[i]`. Envelope extension fields are not
        # fields of `errors[i]`.
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
    """A non-authentication client request was rejected by the platform.

    This includes business-rule and request-validation responses; inspect
    ``problem_details`` and its ``error_code`` when the server supplied them.
    """


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


class MessagingError(DualeAIError):
    """Base class for package-defined transport and streaming errors."""


class DualeAIConnectionError(MessagingError):
    """Raised when transport fails or a service response cannot be used."""


class DualeAIAuthError(DualeAIError):
    """An HTTP request was rejected as unauthorized.

    Missing or malformed local configuration fails earlier with Pydantic
    validation (or ``RuntimeError`` when the SDK loads defaults).
    """


class TaskStoppedError(DualeAIError):
    """Raised when ``AgentResponse.model()`` observes ``task.stopped``.

    A stop is not a failure of the task, so it carries no problem details — only
    the reason supplied by the terminal event. The SDK does not infer whether
    the initiating stop request came from this process or another source.
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
