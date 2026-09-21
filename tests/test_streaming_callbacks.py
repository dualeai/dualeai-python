"""Streaming-callback wiring: regression tests.

Mocks at the transport-Protocol boundary (``MockHTTPTransport``) so
the REAL ``CloudEventsClient.run_task`` callback dispatch and the SDK's
spawn-time delta/tool-use buffer callbacks execute. Network I/O above
the protocol is the only mock; everything below is exercised.

Regression guard: pre-fix, ``events_client.run_task`` only branched
on terminal events and silently dropped
``BridgeContentDeltaResponse`` and ``BridgeToolUseResponse`` events.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from dualeai import DualeAISDK, tool
from dualeai.exceptions import DualeAIError
from dualeai.models.bridge import (
    BridgeContentDeltaResponse,
    BridgeContentResetResponse,
    BridgeSSEEvent,
    BridgeTaskCompletedResponse,
    BridgeTaskErrorResponse,
    BridgeToolUseResponse,
)
from dualeai.models.json_value import JsonValue
from dualeai.models.llm_result import LLMResult
from dualeai.models.problem_details import ProblemDetails
from dualeai.orchestrator import ask
from tests.mocks.mock_http import MockHTTPTransport

StreamingContentEvent = BridgeContentDeltaResponse | BridgeContentResetResponse


def _expect_only_deltas(events: list[StreamingContentEvent]) -> list[BridgeContentDeltaResponse]:
    """Narrow a public content-event stream after asserting its event kinds."""
    deltas = [event for event in events if isinstance(event, BridgeContentDeltaResponse)]
    assert len(deltas) == len(events)
    return deltas


def _delta_event(event_id: int, content: str) -> BridgeSSEEvent:
    return BridgeSSEEvent(
        id=f"{event_id}:1",
        data=BridgeContentDeltaResponse(
            type="content.delta",
            timestamp=datetime.now(timezone.utc),
            delta=content,
        ),
        timestamp=datetime.now(timezone.utc),
    )


def _reset_event(event_id: int, generation: int) -> BridgeSSEEvent:
    return BridgeSSEEvent(
        id=f"{event_id}:1",
        data=BridgeContentResetResponse(
            type="content.reset",
            timestamp=datetime.now(timezone.utc),
            generation=generation,
        ),
        timestamp=datetime.now(timezone.utc),
    )


def _completed_event(event_id: int, completion: str = "done") -> BridgeSSEEvent:
    return BridgeSSEEvent(
        id=f"{event_id}:1",
        data=BridgeTaskCompletedResponse(
            type="task.completed",
            timestamp=datetime.now(timezone.utc),
            result=LLMResult(completion=completion, cache_hit=False),
        ),
        timestamp=datetime.now(timezone.utc),
    )


def _error_event(
    event_id: int,
    code: str,
    message: str,
    *,
    status: int = 500,
    title: str | None = None,
) -> BridgeSSEEvent:
    """Build a task.error SSE event with a ProblemDetails payload for tests.

    `status` and `title` are caller-supplied so tests can model the real wire
    response shape without an SDK-side translation table. The SDK does not
    re-bucket `error_code` into status families.
    """
    return BridgeSSEEvent(
        id=f"{event_id}:1",
        data=BridgeTaskErrorResponse(
            type="task.error",
            timestamp=datetime.now(timezone.utc),
            data=ProblemDetails(
                title=title or code.replace("_", " ").title(),
                status=status,
                detail=message,
                error_code=code,
            ),
        ),
        timestamp=datetime.now(timezone.utc),
    )


def _content_filter_error_event(
    event_id: int,
    message: str,
    *,
    extension: dict[str, JsonValue | None] | None = None,
) -> BridgeSSEEvent:
    """Build one opaque content-filter event with optional future extensions."""
    problem: dict[str, JsonValue | None] = {
        "title": "No acceptable model response",
        "status": 400,
        "detail": message,
        "error_code": "CONTENT_FILTERED",
        "retryable": False,
    }
    if extension is not None:
        problem.update(extension)
    return BridgeSSEEvent(
        id=f"{event_id}:1",
        data=BridgeTaskErrorResponse(
            type="task.error",
            timestamp=datetime.now(timezone.utc),
            data=ProblemDetails.model_validate(problem),
        ),
        timestamp=datetime.now(timezone.utc),
    )


def _tool_use_event(event_id: int, name: str, tool_input: dict[str, JsonValue | None]) -> BridgeSSEEvent:
    return BridgeSSEEvent(
        id=f"{event_id}:1",
        data=BridgeToolUseResponse(
            type="tool.use",
            timestamp=datetime.now(timezone.utc),
            tool_call_id=f"tc_{event_id}",
            name=name,
            input=tool_input,
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        ),
        timestamp=datetime.now(timezone.utc),
    )


@pytest.mark.unit
class TestStreamingCallbackWiring:
    """Regression: deltas reach the user via response.stream() and
    via the events_client callback channel."""

    async def test_response_stream_yields_real_time_deltas_from_bridge(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """End-to-end: SSE deltas → events_client callback → response queue → user.

        Pre-fix the consumer would yield zero deltas; post-fix it
        yields all 3.
        """
        task_id = "test-stream-001"
        mock_transport.inject_events(
            task_id,
            [
                _delta_event(1, "Hello"),
                _delta_event(2, " world"),
                _delta_event(3, "!"),
                _completed_event(4, completion="Hello world!"),
            ],
        )

        response = await ask(
            action="say hi",
            streaming=True,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )

        collected = [event async for event in response.stream()]

        assert [delta.delta for delta in _expect_only_deltas(collected)] == ["Hello", " world", "!"]
        result = await response.model()
        assert result == "Hello world!"

    async def test_streaming_false_yields_no_deltas_even_if_emitted(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """With streaming=False, deltas are dropped (callbacks not wired)."""
        task_id = "test-stream-no-stream"
        mock_transport.inject_events(
            task_id,
            [
                _delta_event(1, "ignored"),
                _completed_event(2, completion="ignored"),
            ],
        )

        response = await ask(
            action="say hi",
            streaming=False,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )

        collected = [event async for event in response.stream()]

        assert collected == []
        assert await response.model() == "ignored"

    async def test_replay_after_completion(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """Iterating stream() a second time replays buffered deltas."""
        task_id = "test-stream-replay"
        mock_transport.inject_events(
            task_id,
            [
                _delta_event(1, "a"),
                _delta_event(2, "b"),
                _delta_event(3, "c"),
                _completed_event(4, completion="abc"),
            ],
        )

        response = await ask(
            action="seq",
            streaming=True,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )

        first_events = [event async for event in response.stream()]
        second_events = [event async for event in response.stream()]

        assert [delta.delta for delta in _expect_only_deltas(first_events)] == ["a", "b", "c"]
        assert [delta.delta for delta in _expect_only_deltas(second_events)] == ["a", "b", "c"]

    async def test_reset_withdraws_failed_output_from_live_and_replay_views(
        self,
        minimal_mock_sdk: DualeAISDK,
        mock_transport: MockHTTPTransport,
    ) -> None:
        """Live consumers see the reset; later replay contains only replacement output."""
        task_id = "test-stream-replacement"
        mock_transport.inject_events(
            task_id,
            [
                _delta_event(1, "withdrawn"),
                _reset_event(2, generation=2),
                _delta_event(3, "replacement"),
                _completed_event(4, completion="replacement"),
            ],
        )

        response = await ask(
            action="replace a failed stream",
            streaming=True,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )

        live_events = [event async for event in response.stream()]
        assert len(live_events) == 3
        first_delta, reset, replacement_delta = live_events
        assert isinstance(first_delta, BridgeContentDeltaResponse)
        assert first_delta.delta == "withdrawn"
        assert isinstance(reset, BridgeContentResetResponse)
        assert reset.generation == 2
        assert isinstance(replacement_delta, BridgeContentDeltaResponse)
        assert replacement_delta.delta == "replacement"

        replayed = [event async for event in response.stream()]
        assert len(replayed) == 1
        assert isinstance(replayed[0], BridgeContentDeltaResponse)
        assert replayed[0].delta == "replacement"

    async def test_error_terminal_propagates_to_model(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """task.error after deltas raises DualeAIError carrying ProblemDetails."""
        task_id = "test-stream-err"
        mock_transport.inject_events(
            task_id,
            [
                _delta_event(1, "partial"),
                _error_event(2, "TASK_DEADLINE_EXCEEDED", "deadline exceeded", status=504),
            ],
        )

        response = await ask(
            action="hi",
            streaming=True,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )

        collected = [event async for event in response.stream()]
        assert [delta.delta for delta in _expect_only_deltas(collected)] == ["partial"]

        with pytest.raises(DualeAIError) as exc_info:
            await response.model()
        assert exc_info.value.problem_details is not None
        assert exc_info.value.problem_details.error_code == "TASK_DEADLINE_EXCEEDED"

    async def test_tool_use_events_do_not_appear_in_delta_stream(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """tool.use events go to a separate queue, not response.stream()."""
        task_id = "test-stream-tool"

        @tool(
            sdk=minimal_mock_sdk,
            description="Search the local test index.",
            timeout=timedelta(seconds=10),
        )
        async def search(query: str) -> dict[str, str]:
            return {"query": query}

        mock_transport.inject_events(
            task_id,
            [
                _delta_event(1, "calling"),
                _tool_use_event(2, "search", {"query": "hi"}),
                _delta_event(3, " done"),
                _completed_event(4, completion="calling done"),
            ],
        )

        response = await ask(
            action="search",
            streaming=True,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )

        collected = [event async for event in response.stream()]

        assert [delta.delta for delta in _expect_only_deltas(collected)] == ["calling", " done"]


@pytest.mark.unit
class TestTaskCancellation:
    """response.task.cancel() propagates to bridge."""

    async def test_task_cancel_propagates_to_bridge_iteration(self, minimal_mock_sdk: DualeAISDK) -> None:
        """Cancelling response.task interrupts the SSE iteration."""
        task_id = "test-cancel-001"
        # No terminal event injected — bridge iteration suspends.

        response = await ask(
            action="hi",
            streaming=True,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )

        response.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await response.model()

        assert response.task.cancelled()

    async def test_double_cancel_is_idempotent(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """Cancelling an already-completed task is a no-op."""
        task_id = "test-cancel-002"
        mock_transport.inject_events(task_id, [_completed_event(1, completion="ok")])

        response = await ask(action="hi", request_id=task_id, sdk=minimal_mock_sdk)
        await response.model()

        response.task.cancel()
        response.task.cancel()
        assert response.task.done()


@pytest.mark.unit
class TestErrorCodeDispatch:
    """task.error events surface as DualeAIError carrying the canonical
    RFC 9457 `ProblemDetails.error_code` on `.problem_details`. The SDK does
    not re-bucket platform codes into an artificial taxonomy — callers
    branch on `exc.problem_details.error_code` directly.
    """

    @pytest.mark.parametrize(
        ("error_code", "detail", "status", "expected_message_fragment"),
        [
            pytest.param(
                "TASK_DEADLINE_EXCEEDED",
                "Task deadline exceeded by 5.00s",
                504,
                "deadline",
                id="deadline-exceeded",
            ),
            pytest.param("AUTHORIZATION_FAILED", "access denied", 403, None, id="authorization-failed"),
            pytest.param(
                "CONTENT_FILTERED",
                "No selected model produced an acceptable response.",
                400,
                "acceptable response",
                id="content-filtered",
            ),
            pytest.param("INTERNAL_ROUTING", "something broke", 500, "something broke", id="unknown-code"),
        ],
    )
    async def test_error_code_is_preserved(
        self,
        minimal_mock_sdk: DualeAISDK,
        mock_transport: MockHTTPTransport,
        error_code: str,
        detail: str,
        status: int,
        expected_message_fragment: str | None,
    ) -> None:
        task_id = f"test-error-{error_code.lower()}"
        mock_transport.inject_events(
            task_id,
            [_error_event(1, error_code, detail, status=status)],
        )

        response = await ask(action="hi", request_id=task_id, sdk=minimal_mock_sdk)
        with pytest.raises(DualeAIError) as exc_info:
            await response.model()
        assert exc_info.value.problem_details is not None
        assert exc_info.value.problem_details.error_code == error_code
        if expected_message_fragment is not None:
            assert expected_message_fragment in str(exc_info.value).lower()

    async def test_content_filter_preserves_unknown_problem_extensions(
        self,
        minimal_mock_sdk: DualeAISDK,
        mock_transport: MockHTTPTransport,
    ) -> None:
        task_id = "test-content-filter-extensions"
        mock_transport.inject_events(
            task_id,
            [
                _content_filter_error_event(
                    1,
                    "No selected model produced an acceptable response.",
                    extension={"support_reference": "case-123"},
                )
            ],
        )

        response = await ask(action="hi", request_id=task_id, sdk=minimal_mock_sdk)
        with pytest.raises(DualeAIError) as exc_info:
            await response.model()

        assert exc_info.value.problem_details is not None
        assert exc_info.value.problem_details.model_extra == {"support_reference": "case-123"}


@pytest.mark.unit
class TestBoundaryConditions:
    """Edge cases for stream() and model()."""

    async def test_completion_immediately_no_deltas(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """First event is task.completed → stream() yields nothing."""
        task_id = "test-no-deltas"
        mock_transport.inject_events(task_id, [_completed_event(1, completion="instant")])

        response = await ask(
            action="hi",
            streaming=True,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )

        collected = [event async for event in response.stream()]

        assert collected == []
        assert await response.model() == "instant"

    async def test_many_small_deltas_all_delivered(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """100 deltas all reach the consumer in order."""
        task_id = "test-many-deltas"
        n_deltas = 100
        events = [_delta_event(i, f"chunk-{i}") for i in range(1, n_deltas + 1)]
        events.append(_completed_event(n_deltas + 1, completion="done"))
        mock_transport.inject_events(task_id, events)

        response = await ask(
            action="bulk",
            streaming=True,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )

        collected = [event async for event in response.stream()]

        assert len(collected) == n_deltas
        assert [delta.delta for delta in _expect_only_deltas(collected)] == [
            f"chunk-{i}" for i in range(1, n_deltas + 1)
        ]

    async def test_unicode_delta_content_preserved(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """Emoji + RTL + multi-byte sequences survive the queue."""
        task_id = "test-unicode"
        contents = ["café", " 🚀", " עברית", " 中文"]
        events = [_delta_event(i, c) for i, c in enumerate(contents, start=1)]
        events.append(_completed_event(len(events) + 1, completion="".join(contents)))
        mock_transport.inject_events(task_id, events)

        response = await ask(
            action="i18n",
            streaming=True,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )

        collected = [event async for event in response.stream()]

        assert [delta.delta for delta in _expect_only_deltas(collected)] == contents

    async def test_empty_string_delta_is_yielded(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """An empty delta is still surfaced (not silently dropped)."""
        task_id = "test-empty-delta"
        mock_transport.inject_events(
            task_id,
            [
                _delta_event(1, ""),
                _delta_event(2, "real"),
                _completed_event(3, completion="real"),
            ],
        )

        response = await ask(
            action="hi",
            streaming=True,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )

        collected = [event async for event in response.stream()]
        assert [delta.delta for delta in _expect_only_deltas(collected)] == ["", "real"]
