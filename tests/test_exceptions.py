"""Active SDK exception hierarchy and structured context behavior."""

import pytest

import dualeai.exceptions as sdk_exceptions
from dualeai.exceptions import (
    BusinessError,
    ConfigurationError,
    DualeAIAuthError,
    DualeAIConnectionError,
    DualeAIError,
    LibraryUploadError,
    MessagingError,
    TaskStoppedError,
    ValidationError,
)
from dualeai.messages import ErrorContext


@pytest.mark.unit
def test_inactive_compatibility_errors_are_absent() -> None:
    inactive = {
        "RoutingError",
        "TaskTimeoutError",
        "ActivityTimeoutError",
        "CacheError",
        "CacheConnectionError",
        "CacheSerializationError",
        "StreamingError",
        "MessagingConnectionError",
        "TransportUnavailableError",
        "DualeAITimeoutError",
        "AgentRegistrationError",
        "TaskSubmissionError",
    }
    assert not {name for name in inactive if hasattr(sdk_exceptions, name)}


@pytest.mark.unit
class TestExceptionHierarchy:
    """Inheritance contract that SDK consumers rely on for ``except`` clauses."""

    def test_all_sdk_errors_inherit_from_dualeai_error(self):
        """Every named SDK exception must be a subclass of DualeAIError."""
        assert issubclass(DualeAIError, Exception)
        for exc_class in (
            BusinessError,
            ConfigurationError,
            DualeAIAuthError,
            DualeAIConnectionError,
            LibraryUploadError,
            MessagingError,
            TaskStoppedError,
            ValidationError,
        ):
            assert issubclass(exc_class, DualeAIError), f"{exc_class.__name__} must inherit from DualeAIError"


@pytest.mark.unit
class TestDualeAIErrorBase:
    """Behavior of the ``DualeAIError`` base class itself."""

    def test_message_is_exposed_on_args_and_attribute(self):
        """``message`` attribute and ``args`` both carry the raw message."""
        error = DualeAIError("boom")
        assert error.message == "boom"
        assert error.args == ("boom",)

    def test_default_context_when_none_provided(self):
        """When no context is passed, a default ``ErrorContext`` is materialised."""
        error = DualeAIError("boom")
        assert isinstance(error.context, ErrorContext)
        assert error.context.error_code == "UNKNOWN_ERROR"
        assert error.context.operation == "unknown_operation"

    def test_dict_context_is_coerced_to_error_context(self):
        """A plain dict ``context=`` is validated into an ``ErrorContext`` instance."""
        error = DualeAIError("boom", context={"error_code": "E1", "operation": "save"})
        assert isinstance(error.context, ErrorContext)
        assert error.context.error_code == "E1"
        assert error.context.operation == "save"

    def test_typed_context_is_kept_as_is(self):
        """An ``ErrorContext`` instance passed through is preserved (no copy)."""
        ctx = ErrorContext(error_code="E1", operation="op")
        error = DualeAIError("boom", context=ctx)
        assert error.context is ctx

    def test_str_includes_context_fields(self):
        """``__str__`` appends a structured ``context: ...`` segment when context exists."""
        error = DualeAIError("boom", context={"error_code": "E1", "operation": "save"})
        rendered = str(error)
        assert rendered.startswith("boom (context: ")
        # Both fields visible in the structured trailer
        assert "error_code='E1'" in rendered
        assert "operation='save'" in rendered

    def test_str_with_empty_message_still_shows_context(self):
        """Even with empty ``message``, the default context trailer is rendered."""
        error = MessagingError("")
        rendered = str(error)
        assert "context:" in rendered

    def test_problem_details_initialised_to_none(self):
        """Client-side errors carry no RFC 9457 ProblemDetails by default."""
        error = DualeAIError("boom")
        assert error.problem_details is None


@pytest.mark.unit
class TestConfigurationError:
    """ConfigurationError lifts ``config_key`` and ``config_value``."""

    def test_attributes_round_trip(self):
        error = ConfigurationError("invalid timeout", config_key="timeout", config_value=-1)
        assert error.config_key == "timeout"
        assert error.config_value == -1


@pytest.mark.unit
class TestValidationError:
    """ValidationError lifts ``field_name``, ``field_value`` and ``validation_rule``."""

    def test_attributes_round_trip(self):
        error = ValidationError(
            "invalid",
            field_name="email",
            field_value="bad-email",
            validation_rule="must_contain_at",
        )
        assert error.field_name == "email"
        assert error.field_value == "bad-email"
        assert error.validation_rule == "must_contain_at"

    def test_typed_context_round_trips(self):
        """ValidationError accepts a typed ``ErrorContext`` and exposes its fields."""
        ctx = ErrorContext(error_code="E_VAL", operation="ingest", request_id="req-123")
        error = ValidationError("invalid", context=ctx)
        # ``context`` is preserved as typed object — fields accessible directly
        assert error.context.request_id == "req-123"
        assert error.context.error_code == "E_VAL"
        assert error.context.operation == "ingest"


@pytest.mark.unit
class TestBusinessError:
    """BusinessError merges arbitrary ``context`` fields into ``ErrorContext``."""

    def test_context_fields_round_trip(self):
        ctx = ErrorContext(error_code="PAYMENT_DECLINED", operation="charge")
        error = BusinessError("payment declined", context=ctx)
        assert error.context.error_code == "PAYMENT_DECLINED"
        assert error.context.operation == "charge"
