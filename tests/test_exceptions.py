"""Tests for SDK exception classes.

These tests target only SDK-defined behavior:

- Inheritance hierarchy (consumers catch ``DualeAIError`` to handle any SDK error)
- Typed ``__init__`` parameters that the subclasses lift into attributes
  (``task_id``, ``timeout_seconds``, ``action``, ``cache_key``, ``agent_name``,
  ``backend_type``, ``activity_name``, ``original_error``, ``skills``)
- ``__str__`` augments the message with the structured ``ErrorContext``
- ``context`` is always coerced into a typed ``ErrorContext`` (default + dict
  + already-typed paths)
- ``CacheConnectionError`` strips credentials from ``connection_url``
- ``problem_details`` is initialised to ``None`` on client-side construction

Substring fishing into pydantic / message prose (``"x" in str(error)`` after
``Error(f"... {x} ...")``) is intentionally avoided — these were tautologies
asserting that the test's own f-string contained its own value.
Python language behavior (`raise ... from`, ``setattr``) is not retested here.
"""

import pytest

from dualeai.exceptions import (
    ActivityTimeoutError,
    AgentRegistrationError,
    BusinessError,
    CacheConnectionError,
    CacheError,
    CacheSerializationError,
    ConfigurationError,
    DualeAIError,
    MessagingConnectionError,
    MessagingError,
    RoutingError,
    StreamingError,
    TaskSubmissionError,
    TaskTimeoutError,
    TransportUnavailableError,
    ValidationError,
)
from dualeai.messages import ErrorContext


@pytest.mark.unit
class TestExceptionHierarchy:
    """Inheritance contract that SDK consumers rely on for ``except`` clauses."""

    def test_all_sdk_errors_inherit_from_dualeai_error(self):
        """Every named SDK exception must be a subclass of DualeAIError."""
        # DualeAIError is the root SDK exception
        assert issubclass(DualeAIError, Exception)

        # Every concrete SDK exception must be catchable as DualeAIError
        for exc_class in (
            ActivityTimeoutError,
            AgentRegistrationError,
            BusinessError,
            CacheConnectionError,
            CacheError,
            CacheSerializationError,
            ConfigurationError,
            MessagingConnectionError,
            MessagingError,
            RoutingError,
            StreamingError,
            TaskSubmissionError,
            TaskTimeoutError,
            TransportUnavailableError,
            ValidationError,
        ):
            assert issubclass(exc_class, DualeAIError), f"{exc_class.__name__} must inherit from DualeAIError"

    def test_cache_subclasses_inherit_from_cache_error(self):
        """Cache-specific exceptions must be catchable as ``CacheError``."""
        assert issubclass(CacheConnectionError, CacheError)
        assert issubclass(CacheSerializationError, CacheError)

    def test_messaging_subclasses_inherit_from_messaging_error(self):
        """Messaging-specific exceptions must be catchable as ``MessagingError``."""
        assert issubclass(MessagingConnectionError, MessagingError)
        assert issubclass(StreamingError, MessagingError)
        assert issubclass(TransportUnavailableError, MessagingConnectionError)

    def test_activity_timeout_inherits_from_task_timeout(self):
        """ActivityTimeoutError consumers must also catch via TaskTimeoutError."""
        assert issubclass(ActivityTimeoutError, TaskTimeoutError)


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
class TestRoutingError:
    """RoutingError lifts ``skills`` into a typed attribute."""

    def test_skills_attribute_round_trips(self):
        error = RoutingError("no worker", skills=["python", "ml"])
        assert error.skills == ["python", "ml"]


@pytest.mark.unit
class TestTaskTimeoutError:
    """TaskTimeoutError lifts ``task_id`` and ``timeout_seconds``."""

    def test_attributes_round_trip(self):
        error = TaskTimeoutError("timed out", task_id="task-123", timeout_seconds=30)
        assert error.task_id == "task-123"
        assert error.timeout_seconds == 30


@pytest.mark.unit
class TestActivityTimeoutError:
    """ActivityTimeoutError adds ``activity_name`` on top of TaskTimeoutError."""

    def test_attributes_round_trip(self):
        error = ActivityTimeoutError("timed out", activity_name="data_processing", timeout_seconds=30)
        assert error.activity_name == "data_processing"
        assert error.timeout_seconds == 30
        # task_id is intentionally cleared by ActivityTimeoutError
        assert error.task_id is None


@pytest.mark.unit
class TestCacheConnectionError:
    """CacheConnectionError sanitises credentials from ``connection_url``."""

    def test_backend_type_is_stored(self):
        error = CacheConnectionError("conn failed", backend_type="redis")
        assert error.backend_type == "redis"

    def test_connection_url_credentials_are_stripped(self):
        """URLs containing ``user:pass@host`` must not leak credentials into context."""
        error = CacheConnectionError(
            "conn failed",
            backend_type="redis",
            connection_url="redis://user:secret@redis.internal:6379/0",
        )
        rendered = str(error)
        # Credentials never appear; sanitised host part does
        assert "secret" not in rendered
        assert "user" not in rendered
        assert "redis.internal:6379/0" in rendered

    def test_connection_url_without_credentials_passes_through(self):
        error = CacheConnectionError(
            "conn failed",
            backend_type="redis",
            connection_url="redis://redis.internal:6379/0",
        )
        rendered = str(error)
        assert "redis.internal:6379/0" in rendered


@pytest.mark.unit
class TestCacheSerializationError:
    """CacheSerializationError lifts ``cache_key`` to a typed attribute."""

    def test_cache_key_round_trips(self):
        error = CacheSerializationError("cannot serialize", cache_key="test_key", data_type="MyType")
        assert error.cache_key == "test_key"


@pytest.mark.unit
class TestMessagingConnectionError:
    """MessagingConnectionError lifts ``endpoint``."""

    def test_endpoint_round_trips(self):
        error = MessagingConnectionError("connect failed", endpoint="nats://localhost:4222")
        assert error.endpoint == "nats://localhost:4222"


@pytest.mark.unit
class TestTransportUnavailableError:
    """TransportUnavailableError maps low-level transport errors to friendly messages."""

    def test_connection_refused_maps_to_user_message(self):
        original = OSError("Connect call failed: ('127.0.0.1', 9999)")
        error = TransportUnavailableError(original_error=original, endpoint="https://api.example/v1")
        # The friendly user-facing prefix from ErrorMessages.CONNECTION_FAILED is present
        rendered = str(error)
        assert "Please try again later" in rendered
        assert error.original_error is original

    def test_original_error_is_retained(self):
        original = RuntimeError("boom")
        error = TransportUnavailableError(original_error=original)
        assert error.original_error is original


@pytest.mark.unit
class TestAgentRegistrationError:
    """AgentRegistrationError lifts ``agent_id`` and ``agent_name``."""

    def test_attributes_round_trip(self):
        error = AgentRegistrationError("already registered", agent_id="a-1", agent_name="DataProcessor")
        assert error.agent_id == "a-1"
        assert error.agent_name == "DataProcessor"


@pytest.mark.unit
class TestTaskSubmissionError:
    """TaskSubmissionError lifts ``action`` and ``skills``."""

    def test_attributes_round_trip(self):
        error = TaskSubmissionError("submit failed", action="process", skills=["python"])
        assert error.action == "process"
        assert error.skills == ["python"]


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
