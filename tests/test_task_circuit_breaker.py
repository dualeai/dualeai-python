"""End-to-end task classification for the SDK-local circuit breaker."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from dualeai import DualeAISDK
from dualeai.backpressure import BackpressureConfig, BackpressureController
from dualeai.events.http_transport import HTTPTransportAuthError, HTTPTransportConnectionError
from dualeai.exceptions import DualeAIAuthError, DualeAIConnectionError, DualeAIError
from dualeai.models.bridge import BridgeSSEEvent, BridgeTaskCreateRequest, BridgeTaskErrorResponse
from dualeai.models.problem_details import ErrorCategory, ProblemDetails
from dualeai.models.routing_policy import RoutingPolicy
from dualeai.orchestrator import ask
from tests.mocks.mock_http import MockHTTPTransport


def _task_error(
    event_id: int,
    *,
    retryable: bool,
    status: int = 400,
    error_code: str = "TEST_TASK_ERROR",
    error_category: ErrorCategory | None = None,
) -> BridgeSSEEvent:
    now = datetime.now(timezone.utc)
    return BridgeSSEEvent(
        id=f"{event_id}:1",
        data=BridgeTaskErrorResponse(
            type="task.error",
            timestamp=now,
            data=ProblemDetails(
                title="Task failed",
                status=status,
                detail="terminal test error",
                error_code=error_code,
                retryable=retryable,
                error_category=error_category,
            ),
        ),
        timestamp=now,
    )


def _use_threshold(sdk: DualeAISDK, failures: int) -> None:
    config = BackpressureConfig(
        failure_threshold=failures,
        recovery_timeout_seconds=60.0,
    )
    sdk._backpressure_config = config
    sdk._backpressure_controller = BackpressureController(config)


@pytest.mark.unit
class TestTaskCircuitBreaker:
    """Run the real SDK while mocking only its HTTP transport."""

    @pytest.mark.parametrize("failure", ["connection", "auth", "cancel"])
    async def test_local_failure_does_not_count_a_remote_failure(
        self,
        minimal_mock_sdk: DualeAISDK,
        mock_transport: MockHTTPTransport,
        failure: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        operations: list[tuple[str, str]] = []
        monkeypatch.setattr(
            minimal_mock_sdk.observability,
            "record_operation",
            lambda operation, status, **_kwargs: operations.append((operation, status)),
        )
        if failure == "connection":
            mock_transport.fail_next_task(HTTPTransportConnectionError("stream lost"))
        elif failure == "auth":
            mock_transport.fail_next_task(HTTPTransportAuthError("denied"))
        response = await ask(action="synthetic", request_id="local-failure", sdk=minimal_mock_sdk)
        if failure == "cancel":
            response.task.cancel()
        expected = {"connection": DualeAIConnectionError, "auth": DualeAIAuthError, "cancel": asyncio.CancelledError}
        with pytest.raises(expected[failure]):
            await response.model()
        metrics = minimal_mock_sdk.backpressure_controller.metrics
        assert metrics.tasks_failed == 0
        assert metrics.tasks_completed == 0
        assert ("task_stream", "cancelled" if failure == "cancel" else "failed") in operations

    async def test_lost_stream_logs_the_local_failure(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_transport.fail_next_task(HTTPTransportConnectionError("stream lost"))
        response = await ask(action="synthetic", request_id="lost-stream-log", sdk=minimal_mock_sdk)
        for _ in range(2):
            with pytest.raises(DualeAIConnectionError):
                await response.model()
        entries = [record.msg for record in caplog.records if record.name == "dualeai.response"]
        assert entries
        assert all(isinstance(entry, dict) and entry["event"] == "Task stream failed" for entry in entries)
        assert len(mock_transport.get_requests_for_task("lost-stream-log")) == 1

    @pytest.mark.parametrize("status", [503, 418])
    async def test_retryable_terminal_errors_open_before_next_http_call(
        self,
        minimal_mock_sdk: DualeAISDK,
        mock_transport: MockHTTPTransport,
        status: int,
    ) -> None:
        _use_threshold(minimal_mock_sdk, failures=2)
        for event_id in (1, 2):
            task_id = f"retryable-{event_id}"
            mock_transport.inject_event(task_id, _task_error(event_id, retryable=True, status=status))
            response = await ask(action="retryable", request_id=task_id, sdk=minimal_mock_sdk)
            with pytest.raises(DualeAIError):
                await response.model()

        request_count = len(mock_transport.get_requests())
        assert minimal_mock_sdk.backpressure_controller.metrics.tasks_failed == 2
        with pytest.raises(RuntimeError, match="task dependency is unavailable"):
            await ask(action="rejected", request_id="retryable-3", sdk=minimal_mock_sdk)
        assert len(mock_transport.get_requests()) == request_count

    @pytest.mark.parametrize("status", [400, 503])
    async def test_non_retryable_terminal_error_does_not_open(
        self,
        minimal_mock_sdk: DualeAISDK,
        mock_transport: MockHTTPTransport,
        status: int,
    ) -> None:
        _use_threshold(minimal_mock_sdk, failures=1)
        mock_transport.inject_event(
            "business-error",
            _task_error(1, retryable=False, status=status, error_code="CONTENT_FILTERED"),
        )
        response = await ask(action="business", request_id="business-error", sdk=minimal_mock_sdk)
        with pytest.raises(DualeAIError) as exc_info:
            await response.model()
        assert exc_info.value.problem_details is not None
        assert exc_info.value.problem_details.error_code == "CONTENT_FILTERED"
        assert exc_info.value.problem_details.retryable is False

        mock_transport.inject_event("healthy", mock_transport.create_task_completed_event())
        healthy = await ask(action="healthy", request_id="healthy", sdk=minimal_mock_sdk)
        assert await healthy.model() == "Task completed"
        assert minimal_mock_sdk.backpressure_controller.metrics.tasks_failed == 1
        assert minimal_mock_sdk.backpressure_controller.metrics.tasks_completed == 1

    @pytest.mark.parametrize(
        ("error_code", "error_category"),
        [
            ("LLM_CONFIG_UNAVAILABLE", ErrorCategory.config),
            ("AUTHORIZATION_FAILED", ErrorCategory.integrity),
            ("BILLING_LIMIT_EXCEEDED", ErrorCategory.config),
        ],
    )
    async def test_non_retryable_terminal_categories_remain_neutral(
        self,
        minimal_mock_sdk: DualeAISDK,
        mock_transport: MockHTTPTransport,
        error_code: str,
        error_category: ErrorCategory,
    ) -> None:
        """The bridge retryable flag is the SDK's public classification contract."""
        _use_threshold(minimal_mock_sdk, failures=1)
        task_id = error_code.lower()
        mock_transport.inject_event(
            task_id,
            _task_error(
                1,
                retryable=False,
                error_code=error_code,
                error_category=error_category,
            ),
        )
        response = await ask(action="terminal", request_id=task_id, sdk=minimal_mock_sdk)
        with pytest.raises(DualeAIError):
            await response.model()

        healthy_id = f"healthy-after-{task_id}"
        mock_transport.inject_event(healthy_id, mock_transport.create_task_completed_event())
        healthy = await ask(action="healthy", request_id=healthy_id, sdk=minimal_mock_sdk)
        assert await healthy.model() == "Task completed"

    async def test_connection_failure_counts_as_dependency_failure(
        self,
        minimal_mock_sdk: DualeAISDK,
        mock_transport: MockHTTPTransport,
    ) -> None:
        _use_threshold(minimal_mock_sdk, failures=1)

        response = await ask(action="missing stream", request_id="no-events", sdk=minimal_mock_sdk)
        mock_transport.signal_stop()
        with pytest.raises(DualeAIConnectionError):
            await response.model()

        with pytest.raises(RuntimeError, match="task dependency is unavailable"):
            await ask(action="rejected", request_id="after-connection-error", sdk=minimal_mock_sdk)
        assert len(mock_transport.get_requests_for_task("after-connection-error")) == 0

    async def test_non_retryable_http_problem_does_not_open(
        self,
        minimal_mock_sdk: DualeAISDK,
        mock_transport: MockHTTPTransport,
    ) -> None:
        _use_threshold(minimal_mock_sdk, failures=1)
        problem = ProblemDetails(
            title="Invalid task request",
            status=422,
            detail="The request cannot be retried",
            error_code="INVALID_TASK_REQUEST",
            retryable=False,
        )
        mock_transport.fail_next_task(HTTPTransportConnectionError("HTTP 422", problem_details=problem))

        response = await ask(action="invalid", request_id="invalid-http", sdk=minimal_mock_sdk)
        with pytest.raises(DualeAIConnectionError) as error:
            await response.model()
        assert error.value.problem_details is problem

        mock_transport.inject_event("healthy-after-http", mock_transport.create_task_completed_event())
        healthy = await ask(action="healthy", request_id="healthy-after-http", sdk=minimal_mock_sdk)
        assert await healthy.model() == "Task completed"

    async def test_cancelled_admission_does_not_become_a_dependency_failure(
        self,
        minimal_mock_sdk: DualeAISDK,
        mock_transport: MockHTTPTransport,
    ) -> None:
        """Cancellation between breaker admission and acknowledgement stays neutral."""
        _use_threshold(minimal_mock_sdk, failures=1)
        await minimal_mock_sdk._ensure_scheduler()
        controller = minimal_mock_sdk._backpressure_controller
        assert controller is not None
        admission = asyncio.get_running_loop().create_future()
        admission.cancel()

        with pytest.raises(asyncio.CancelledError):
            await minimal_mock_sdk._run_task_with_circuit_breaker(
                admission=admission,
                task_id="cancelled-admission",
                request=BridgeTaskCreateRequest(
                    type="create",
                    routing_policy=RoutingPolicy(),
                    action_prompt="cancel",
                    deadline=datetime.now(timezone.utc) + timedelta(minutes=1),
                ),
                content_callback=None,
                tool_use_callback=lambda _tool_use, _event_id: None,
                accepted_callback=lambda: None,
            )

        assert controller.metrics.tasks_failed == 0
        mock_transport.inject_event("healthy-after-cancel", mock_transport.create_task_completed_event())
        healthy = await ask(action="healthy", request_id="healthy-after-cancel", sdk=minimal_mock_sdk)
        assert await healthy.model() == "Task completed"
