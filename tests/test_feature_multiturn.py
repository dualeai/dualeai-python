"""Multi-turn conversation feature tests.

Tests multi-turn conversations with the real SDK and a transport-protocol test double.

Architecture:
- Uses minimal_mock_sdk fixture which creates REAL DualeAISDK
- The transport protocol is replaced; HTTP serialization is tested separately
- REAL: submit_task, continuation orchestration, CloudEventsClient, circuit breaker, AgentResponse
- ALL SDK code runs for real - no SDK method mocking
"""

import asyncio
from datetime import datetime, timezone

import pytest

from dualeai import DualeAISDK
from dualeai.events.http_transport import HTTPTransportResponseError
from dualeai.exceptions import BusinessError, DualeAIError
from dualeai.models.bridge import BridgeTaskContinueRequest
from dualeai.models.problem_details import ProblemDetails
from dualeai.orchestrator import ask
from dualeai.response import AgentResponse
from tests.mocks.mock_http import require_mock_http_transport

DEADLINE = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)


async def _complete_response(response: AgentResponse[object], sdk: DualeAISDK) -> None:
    """Inject and await a successful terminal event for one mock response."""
    transport = require_mock_http_transport(sdk)
    transport.inject_event(response.task_id, transport.create_task_completed_event())
    await response.task


@pytest.mark.unit
class TestUnitMultiTurnConversation:
    """Test SDK orchestration against its typed transport protocol boundary."""

    async def test_pipe_operator_equivalent_to_next(self, minimal_mock_sdk: DualeAISDK):
        """Test that pipe operator (|) works as syntactic sugar for next()."""
        # Create initial response
        initial_response = await ask(action="Test action", sdk=minimal_mock_sdk)
        requests_before = len(require_mock_http_transport(minimal_mock_sdk).get_requests())
        await _complete_response(initial_response, minimal_mock_sdk)

        # Use pipe operator (| returns coroutine, must await)
        continued_response = await (initial_response | "continue with pipe")

        # Verify new AgentResponse returned
        assert isinstance(continued_response, AgentResponse)
        assert continued_response.task_id is not None
        assert continued_response.task_id != initial_response.task_id

        transport = require_mock_http_transport(minimal_mock_sdk)
        requests_after = len(transport.get_requests())
        assert requests_after > requests_before
        continuation_record = transport.get_requests()[-1]
        continuation_request = continuation_record["request"]
        assert isinstance(continuation_request, BridgeTaskContinueRequest)
        assert continuation_record["task_id"] == continued_response.task_id
        assert continuation_request.parent_task_id == initial_response.task_id
        assert continuation_request.message == "continue with pipe"

        assert not continued_response.task.done()

    async def test_chained_continuations_link_each_child_to_previous_task(self, minimal_mock_sdk: DualeAISDK):
        """Each child names the preceding public task as its parent."""
        # Create initial response
        initial_response = await ask(action="Start chain", sdk=minimal_mock_sdk)
        requests_after_initial = len(require_mock_http_transport(minimal_mock_sdk).get_requests())

        # Chain continuations - REAL SDK handles each
        await _complete_response(initial_response, minimal_mock_sdk)
        continued_1 = await initial_response.next(message="first step")
        await _complete_response(continued_1, minimal_mock_sdk)
        continued_2 = await continued_1.next(message="second step")
        await _complete_response(continued_2, minimal_mock_sdk)
        continued_3 = await continued_2.next(message="third step")

        # Verify each continuation has unique task_id
        task_ids_set = {
            initial_response.task_id,
            continued_1.task_id,
            continued_2.task_id,
            continued_3.task_id,
        }
        assert len(task_ids_set) == 4, "All task IDs should be unique"

        # Verify all continuations were sent via HTTP
        requests_final = len(require_mock_http_transport(minimal_mock_sdk).get_requests())
        assert requests_final >= requests_after_initial + 3  # 3 continuations
        continuation_requests = require_mock_http_transport(minimal_mock_sdk).get_requests()[requests_after_initial:]
        links: list[tuple[str, str]] = []
        for record in continuation_requests:
            request = record["request"]
            assert isinstance(request, BridgeTaskContinueRequest)
            links.append((record["task_id"], request.parent_task_id))
        assert links == [
            (continued_1.task_id, initial_response.task_id),
            (continued_2.task_id, continued_1.task_id),
            (continued_3.task_id, continued_2.task_id),
        ]

        assert all(response.task.done() for response in (initial_response, continued_1, continued_2))
        assert not continued_3.task.done()

    async def test_agent_response_has_sdk_reference(self, minimal_mock_sdk: DualeAISDK):
        """Test that AgentResponse maintains reference to SDK for tenant context."""
        # Call ask
        response = await ask(action="Test task", sdk=minimal_mock_sdk)

        # Verify response has SDK reference
        assert response.sdk is minimal_mock_sdk
        assert isinstance(response, AgentResponse)

    async def test_response_preserves_streaming_flag(self, minimal_mock_sdk: DualeAISDK):
        """Test that AgentResponse preserves streaming flag from initial call."""
        # Non-streaming response
        response_no_stream = await ask(action="No stream", streaming=False, sdk=minimal_mock_sdk)
        assert response_no_stream.streaming is False

        # Streaming response
        response_stream = await ask(action="Stream", streaming=True, sdk=minimal_mock_sdk)
        assert response_stream.streaming is True

    async def test_next_waits_for_parent_terminal_before_sending_child(self, minimal_mock_sdk: DualeAISDK):
        """No child request is sent while the parent response is still pending."""
        initial_response = await ask(action="Initial", sdk=minimal_mock_sdk)
        transport = require_mock_http_transport(minimal_mock_sdk)
        requests_before = len(transport.get_requests())

        next_call = asyncio.create_task(initial_response.next(message="Continue"))
        await asyncio.sleep(0)

        assert not next_call.done()
        assert len(transport.get_requests()) == requests_before

        transport.inject_event(initial_response.task_id, transport.create_task_completed_event())
        continued_response = await next_call
        assert continued_response.task_id != initial_response.task_id
        assert len(transport.get_requests()) == requests_before + 1

    async def test_cancelling_one_next_waiter_preserves_parent_and_sibling(
        self,
        minimal_mock_sdk: DualeAISDK,
    ) -> None:
        """One caller may stop waiting without cancelling shared parent work."""
        initial_response = await ask(action="Initial", sdk=minimal_mock_sdk)
        transport = require_mock_http_transport(minimal_mock_sdk)
        requests_before = len(transport.get_requests())
        cancelled_waiter = asyncio.create_task(initial_response.next(message="Cancelled branch"))
        surviving_waiter = asyncio.create_task(initial_response.next(message="Surviving branch"))
        await asyncio.sleep(0)

        cancelled_waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled_waiter

        assert not initial_response.task.cancelled()
        assert not surviving_waiter.done()
        assert len(transport.get_requests()) == requests_before

        transport.inject_event(initial_response.task_id, transport.create_task_completed_event())
        continued_response = await surviving_waiter

        assert continued_response.task_id != initial_response.task_id
        continuation_requests = transport.get_requests()[requests_before:]
        assert len(continuation_requests) == 1
        request = continuation_requests[0]["request"]
        assert isinstance(request, BridgeTaskContinueRequest)
        assert request.parent_task_id == initial_response.task_id

    async def test_next_wait_for_timeout_preserves_parent_for_a_later_continuation(
        self,
        minimal_mock_sdk: DualeAISDK,
    ) -> None:
        """A local wait timeout must not cancel the shared parent response."""
        initial_response = await ask(action="Initial", sdk=minimal_mock_sdk)
        transport = require_mock_http_transport(minimal_mock_sdk)
        requests_before = len(transport.get_requests())

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(initial_response.next(message="Timed branch"), timeout=0.01)

        assert not initial_response.task.cancelled()
        assert len(transport.get_requests()) == requests_before

        transport.inject_event(initial_response.task_id, transport.create_task_completed_event())
        continued_response = await initial_response.next(message="Later branch")

        assert continued_response.task_id != initial_response.task_id
        assert len(transport.get_requests()) == requests_before + 1

    async def test_next_propagates_parent_error_without_sending_child(self, minimal_mock_sdk: DualeAISDK):
        """A failed parent blocks continuation and preserves its typed error."""
        initial_response = await ask(action="Initial", sdk=minimal_mock_sdk)
        transport = require_mock_http_transport(minimal_mock_sdk)
        requests_before = len(transport.get_requests())
        transport.inject_event(
            initial_response.task_id,
            transport.create_task_error_event("Parent failed", error_code="PARENT_FAILED"),
        )

        with pytest.raises(DualeAIError) as exc_info:
            await initial_response.next(message="Continue")

        assert exc_info.value.problem_details is not None
        assert exc_info.value.problem_details.error_code == "PARENT_FAILED"
        assert len(transport.get_requests()) == requests_before

    async def test_continuation_child_error_preserves_problem_details(self, minimal_mock_sdk: DualeAISDK) -> None:
        """A terminal error on C is raised by C's AgentResponse with its typed code."""
        transport = require_mock_http_transport(minimal_mock_sdk)
        parent = await ask(action="Start", request_id="parent-task-123", sdk=minimal_mock_sdk)
        await _complete_response(parent, minimal_mock_sdk)
        continued_response = await parent.next(message="Continue", deadline=DEADLINE)
        transport.inject_event(
            continued_response.task_id,
            transport.create_task_error_event("Child failed", error_code="CHILD_FAILED"),
        )

        with pytest.raises(DualeAIError) as exc_info:
            await continued_response.model()

        assert exc_info.value.problem_details is not None
        assert exc_info.value.problem_details.error_code == "CHILD_FAILED"

    async def test_continuation_http_rejection_uses_continuation_translation(
        self,
        minimal_mock_sdk: DualeAISDK,
    ) -> None:
        """A Bridge rejection keeps its typed problem and continuation-specific prefix."""
        transport = require_mock_http_transport(minimal_mock_sdk)
        parent = await ask(action="Start", request_id="parent-task-123", sdk=minimal_mock_sdk)
        await _complete_response(parent, minimal_mock_sdk)
        problem = ProblemDetails(
            title="Invalid continuation",
            status=422,
            detail="The parent cannot be continued",
            error_code="INVALID_CONTINUATION",
            retryable=False,
        )
        transport.fail_next_task(
            HTTPTransportResponseError(
                "HTTP 422",
                problem_details=problem,
            )
        )
        response = await parent.next(message="Continue", deadline=DEADLINE)

        with pytest.raises(BusinessError, match="Task continuation failed") as exc_info:
            await response.model()

        assert exc_info.value.problem_details is problem

    async def test_concurrent_sibling_continuations_share_the_same_parent(self, minimal_mock_sdk: DualeAISDK):
        """Concurrent next calls mint unique children without changing the parent."""
        initial_response = await ask(action="Initial", sdk=minimal_mock_sdk)
        await _complete_response(initial_response, minimal_mock_sdk)
        transport = require_mock_http_transport(minimal_mock_sdk)
        requests_before = len(transport.get_requests())

        first, second = await asyncio.gather(
            initial_response.next(message="First branch"),
            initial_response.next(message="Second branch"),
        )

        assert first.task_id != second.task_id
        continuation_requests = transport.get_requests()[requests_before:]
        assert len(continuation_requests) == 2
        requests = [record["request"] for record in continuation_requests]
        assert all(isinstance(request, BridgeTaskContinueRequest) for request in requests)
        assert {request.parent_task_id for request in requests if isinstance(request, BridgeTaskContinueRequest)} == {
            initial_response.task_id
        }

    async def test_next_propagates_parent_cancellation_without_sending_child(self, minimal_mock_sdk: DualeAISDK):
        """A cancelled parent cannot create a continuation child."""
        initial_response = await ask(action="Initial", sdk=minimal_mock_sdk)
        transport = require_mock_http_transport(minimal_mock_sdk)
        requests_before = len(transport.get_requests())

        initial_response.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await initial_response.next(message="Continue")

        assert len(transport.get_requests()) == requests_before
