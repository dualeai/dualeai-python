"""Task response handling for terminal results and optional SSE content events.

This module provides the AgentResponse class that wraps an
``asyncio.Task`` running the bridge SSE iteration.

``response.task.cancel()`` cancels the local stream runner and closes its
connection; it is not a platform stop request. ``response.stop()`` submits a
stop request and returns its acceptance receipt, which does not by itself prove
that the Task has stopped.

Terminal, streaming, continuation, and stop behavior is covered by
``tests/test_feature_results.py``, ``tests/test_streaming_callbacks.py``,
``tests/test_feature_multiturn.py``, and ``tests/test_task_stop.py``.
``tests/test_feature_multiturn.py::TestUnitMultiTurnConversation::test_next_sends_only_the_criteria_the_caller_sets``
covers continuation defaults; no dedicated automated test covers parent result
validation or stream-runner failures during streaming.
"""

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import TYPE_CHECKING, Generic, TypeVar, overload

import pydantic
import structlog
from pydantic import BaseModel, TypeAdapter

from dualeai.exceptions import (
    DualeAIError,
    TaskStoppedError,
    ValidationError,
)
from dualeai.models.bridge import (
    BridgeContentDeltaResponse,
    BridgeContentResetResponse,
    BridgeTaskCompletedResponse,
    BridgeTaskErrorResponse,
    BridgeTaskStoppedResponse,
)
from dualeai.models.llm_result import LLMResult
from dualeai.models.problem_details import ProblemDetails
from dualeai.models.response_format import ResponseFormat
from dualeai.models.routing_policy import RoutingPolicy
from dualeai.models.task_stop import TaskStopAccepted
from dualeai.utils import get_type_name

if TYPE_CHECKING:
    from dualeai.attachments import PreparedAttachment
    from dualeai.sdk import DualeAISDK

logger = structlog.get_logger(__name__)

T = TypeVar("T")

# Terminal type returned by an EventsClient task operation and awaited by response.task.
TerminalEvent = BridgeTaskCompletedResponse | BridgeTaskErrorResponse | BridgeTaskStoppedResponse
StreamingContentEvent = BridgeContentDeltaResponse | BridgeContentResetResponse


def _exception_from_terminal(terminal: BridgeTaskErrorResponse, task_id: str) -> DualeAIError:
    """Lift the RFC 9457 ProblemDetails on the terminal event into a single typed SDK error.

    The SDK does NOT re-bucket platform error codes into an artificial
    timeout/auth/connection taxonomy. The canonical wire identifier is
    ``terminal.data.error_code`` (e.g. ``TASK_DEADLINE_EXCEEDED``,
    ``BILLING_LIMIT_EXCEEDED``, ``LLM_NO_PROGRESS``, ``INTERNAL_ERROR``) and
    callers branch on it directly. ``error_code`` is a string, and the public
    errors page ("Errors and reliability", https://duale.ai/en/docs/sdk/errors)
    lists every code. Additional envelope fields (``retryable``,
    ``retry_after_seconds``, ``owner_action_required``, ``error_category``) live
    on ``exc.problem_details`` so retry logic reads them without an SDK-side
    mapping table. When ``errors`` is present, each item can carry ``ai_hints``
    at ``exc.problem_details.errors[i].ai_hints``.

    Example:
        try:
            await response.model()
        except DualeAIError as err:
            pd = err.problem_details
            if pd is not None and pd.error_code == "TASK_DEADLINE_EXCEEDED":
                ...
    """
    data: ProblemDetails = terminal.data
    exc = DualeAIError(data.detail, error_code=data.error_code, task_id=task_id)
    exc.problem_details = data
    return exc


class AgentResponse(Generic[T]):
    """Handle one Task's content events and terminal result.

    ``T`` is the optional local result type used by :meth:`model`. Iterating
    :meth:`stream` exposes content deltas and resets in arrival order, each with
    a ``generation``; call :meth:`model`
    afterward to apply terminal error and result-validation semantics.
    """

    def __init__(
        self,
        task_id: str,
        task: asyncio.Task[TerminalEvent],
        expected_type: type[T] | None = None,
        *,
        sdk: "DualeAISDK",
        accepted: asyncio.Event,
        delta_queue: asyncio.Queue[StreamingContentEvent] | None = None,
        deltas: list[StreamingContentEvent] | None = None,
    ) -> None:
        """Initialize agent response.

        Args:
            task_id: ID of the task being executed.
            task: The ``asyncio.Task`` running SSE iteration for a Task created
                by ``DualeAISDK.submit_task`` or ``AgentResponse.next``. It
                resolves to a completed, error, or stopped terminal event.
            expected_type: Expected result type for ``model()``
                validation.
            sdk: SDK instance — kept on the response so ``next()`` can
                start a continuation.
            accepted: Set when the Platform accepts this Task's request, or
                when this response's runner ends; ``next`` waits on it.
            delta_queue / deltas: Shared streaming buffers populated by
                the spawn-time callbacks. SDK passes these in so the spawn
                callback can close over them without needing the response
                object yet. ``streaming`` is True exactly when both are given.
        """
        self.task_id: str = task_id
        self.task: asyncio.Task[TerminalEvent] = task
        self.expected_type: type[T] | None = expected_type
        self.sdk: DualeAISDK = sdk
        self._accepted: asyncio.Event = accepted
        self.streaming: bool = delta_queue is not None and deltas is not None
        # LLMResult cached after first model() call.
        self._llm_result: LLMResult | None = None
        self._llm_result_loaded: bool = False
        self._llm_result_lock: asyncio.Lock = asyncio.Lock()
        # Streaming delivery: live queue (event-driven) + replay list. Keep
        # both absent for non-streaming responses; tool-use delivery is
        # scheduled directly by DualeAISDK and needs no response-owned queue.
        self._delta_queue = delta_queue
        self._deltas = deltas

    async def _await_terminal(self) -> "TerminalEvent":
        """Await the terminal event of this response's runner."""
        try:
            return await self.task
        except asyncio.CancelledError:
            if self.task.cancelled():
                logger.warning("Task was cancelled", task_id=self.task_id)
            else:
                logger.debug("Task waiter was cancelled", task_id=self.task_id)
            raise
        except asyncio.TimeoutError as timeout_err:
            logger.error(
                "Task timed out waiting for result",
                task_id=self.task_id,
                error=str(timeout_err),
            )
            raise
        except Exception as task_err:
            logger.error("Task stream failed", task_id=self.task_id, error=str(task_err))
            raise

    def _unwrap_llm_result(self, result: LLMResult, *, warn_on_empty: bool = False) -> object:
        """Cache the LLMResult and return its inner payload.

        Order: ``validated_data`` (when supplied in the terminal envelope) →
        ``completion`` (raw LLM text) → the wrapper itself. Both model and
        stream extraction funnel through here so
        the LLMResult cache and the field-precedence rule live in one
        place.
        """
        self._llm_result = result
        self._llm_result_loaded = True
        if result.validated_data is not None:
            return result.validated_data
        if result.completion is not None:
            return result.completion
        if warn_on_empty:
            logger.warning("LLMResult has neither validated_data nor completion", result=result)
        return result

    def _extract_result(self, terminal: BridgeTaskCompletedResponse) -> object:
        """Extract data from LLMResult wrapper or pass through."""
        result = terminal.result
        if not isinstance(result, LLMResult):
            return result

        logger.debug(
            "SDK processing LLMResult object",
            task_id=self.task_id,
            has_validated_data=result.validated_data is not None,
            has_completion=result.completion is not None,
        )
        return self._unwrap_llm_result(result, warn_on_empty=True)

    def _validation_error(self, result: object, error: Exception) -> ValidationError:
        """Build the standard schema-mismatch ValidationError.

        ``expected_type`` is guaranteed non-None at every call site —
        callers all branch out earlier when there's nothing to validate
        against.
        """
        assert self.expected_type is not None  # Caller invariant.
        return ValidationError(
            f"Result validation failed for type {self.expected_type.__name__}",
            context={
                # The complete result is not attached directly. Pydantic's
                # validation text can still contain rejected input fragments.
                "expected_type": self.expected_type.__name__,
                "result_type": get_type_name(result),
                "validation_error": str(error),
            },
        )

    def _validate_pydantic_model(self, result: object) -> T:
        """Validate result against Pydantic model."""
        expected_type = self.expected_type
        assert expected_type is not None
        if not issubclass(expected_type, BaseModel):
            raise AttributeError(f"Type {expected_type.__name__} is not a Pydantic model")
        try:
            validated = expected_type.model_validate(result)
            if isinstance(validated, expected_type):
                return validated
            raise TypeError(f"Pydantic returned {type(validated).__name__}, expected {expected_type.__name__}")
        except AttributeError:
            raise
        except Exception as e:
            if isinstance(result, str):
                return self._try_json_validation(result, e)
            raise self._validation_error(result, e) from e

    def _try_json_validation(self, result: str, original_error: Exception) -> T:
        """Try to validate result as JSON string."""
        expected_type = self.expected_type
        assert expected_type is not None
        if not issubclass(expected_type, BaseModel):
            raise AttributeError(f"Type {expected_type.__name__} is not a Pydantic model")
        try:
            validated = expected_type.model_validate_json(result)
            if isinstance(validated, expected_type):
                return validated
            raise TypeError(f"Pydantic returned {type(validated).__name__}, expected {expected_type.__name__}")
        except (pydantic.ValidationError, ValueError, AttributeError) as json_err:
            logger.debug(
                "Result is string but JSON parsing/validation failed",
                task_id=self.task_id,
                json_error=str(json_err),
                result_preview=result[:100],
            )
            raise self._validation_error(result, original_error) from original_error

    def _validate_basic_type(self, result: object) -> T:
        """Validate and convert basic types."""
        expected_type = self.expected_type
        assert expected_type is not None

        if expected_type in (int, float, str, bool) and isinstance(result, int | float | str | bool | dict):
            if isinstance(result, dict):
                result = bool(result) if expected_type is bool else str(result)
            try:
                adapter: TypeAdapter[T] = TypeAdapter(expected_type)
                return adapter.validate_python(result)
            except (ValueError, TypeError) as e:
                raise ValidationError(
                    f"Type conversion failed: {e}",
                    context={
                        "expected_type": expected_type.__name__,
                        "result": result,
                        "conversion_error": str(e),
                    },
                ) from e

        if isinstance(result, expected_type):
            adapter: TypeAdapter[T] = TypeAdapter(expected_type)
            return adapter.validate_python(result)

        raise ValidationError(
            f"Result type mismatch: expected {expected_type.__name__}, got {get_type_name(result)}",
            context={
                "expected_type": expected_type.__name__,
                "actual_type": get_type_name(result),
                "result": result,
            },
        )

    @overload
    async def model(self: "AgentResponse[T]") -> T: ...

    @overload
    async def model(self: "AgentResponse[None]") -> object: ...

    async def model(self) -> T | object:
        """Get the result by awaiting task completion.

        Returns:
            The task result, validated against expected_type if provided.

        Raises:
            TaskStoppedError: If the stream ended with ``task.stopped``.
            DualeAIError: If the platform emitted a ``task.error`` terminal event. The
                raised instance carries the canonical RFC 9457 ProblemDetails on
                ``exc.problem_details`` (with ``error_code``, ``detail``, and
                extension fields ``retryable``, ``retry_after_seconds``,
                ``owner_action_required``, ``error_category``). When ``errors``
                is present, each item can carry ``ai_hints`` at
                ``exc.problem_details.errors[i].ai_hints``. Callers branch on
                ``exc.problem_details.error_code`` directly — there is
                no SDK-side re-mapping table.
            DualeAIConnectionError: If Task observation fails after applicable
                recovery attempts. This does not prove a failed remote Task.
                The default HTTP transport supplies diagnostics in ``exc.context``;
                ``exc.problem_details`` can be absent for a local failure.
            ValidationError: If the result does not match ``expected_type``.
                Its context, and debug-level validation logs, can contain
                rejected result fragments; treat both as potentially sensitive.
            asyncio.CancelledError: If the local Task runner is cancelled.

        Exceptions raised by the underlying stream or transport task propagate
        unchanged.
        """
        terminal = await self._await_terminal()

        if isinstance(terminal, BridgeTaskStoppedResponse):
            # A stop is not a task failure, so it carries no problem details.
            raise TaskStoppedError(terminal.reason, task_id=self.task_id)
        if isinstance(terminal, BridgeTaskErrorResponse):
            raise _exception_from_terminal(terminal, self.task_id)
        result = self._extract_result(terminal)

        logger.debug(
            # This log call includes type metadata only.
            "SDK received result - checking type",
            task_id=self.task_id,
            result_type=get_type_name(result),
        )

        if not self.expected_type:
            return result

        try:
            return self._validate_pydantic_model(result)
        except (ValidationError, AttributeError):
            return self._validate_basic_type(result)

    async def _ensure_llm_result_loaded(self) -> None:
        """Singleton loader — ensures LLMResult is loaded exactly once."""
        if self._llm_result_loaded:
            return

        async with self._llm_result_lock:
            if self._llm_result_loaded:
                return

            try:
                terminal = await self.task
                if isinstance(terminal, BridgeTaskCompletedResponse) and isinstance(terminal.result, LLMResult):
                    self._unwrap_llm_result(terminal.result)
                logger.debug(
                    "LLMResult loaded via singleton loader",
                    task_id=self.task_id,
                    has_llm_result=self._llm_result is not None,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "Failed to load LLMResult in singleton loader",
                    task_id=self.task_id,
                    error=str(e),
                )
            finally:
                self._llm_result_loaded = True

    async def stream(self) -> AsyncGenerator[StreamingContentEvent, None]:
        """Stream real-time content changes from agent execution.

        Yields content deltas and resets in arrival order, which can differ
        from answer order. Keep only the highest ``generation``, which a reset
        starts, and display its deltas by ``sequence``. Backed by
        ``asyncio.Queue`` (event-driven, no busy-poll).

        Replay: iterating ``stream()`` after the task is done yields every
        buffered content event, resets included, in arrival order — useful
        for re-rendering on UI re-mount.
        ``tests/test_streaming_callbacks.py::TestStreamingCallbackWiring::test_replay_repeats_every_content_event_in_arrival_order``
        enforces this.

        With ``streaming=False`` this method just awaits the task and returns
        without yielding. Terminal ``task.error`` and ``task.stopped`` events
        end iteration; this method does not translate them into
        ``DualeAIError`` or ``TaskStoppedError``. Call :meth:`model` for the
        authoritative terminal result, including stream-runner exceptions and
        cancellation. With ``streaming=True``, a failed or cancelled runner
        ends iteration without propagating its exception; with
        ``streaming=False``, awaiting that runner propagates it.
        """
        delta_queue, deltas = self._delta_queue, self._deltas
        if delta_queue is None or deltas is None:
            await self.task
            return

        # Replay path after the terminal task has completed.
        if self.task.done():
            for event in deltas:
                yield event
            return

        # Live path. Two phases per iteration:
        #
        # 1. Fast drain — yield everything already queued without
        #    spawning a new wait. Bursts (LLM token streams arrive
        #    in tight clusters) collapse into N ``get_nowait`` calls
        #    instead of N ``asyncio.wait`` rounds with their per-call
        #    Task/set allocations + cancellation overhead.
        # 2. Slow wait — only if the queue is empty AND the task is
        #    still running, race the next ``queue.get`` against
        #    ``self.task`` completion.
        while True:
            while not delta_queue.empty():
                yield delta_queue.get_nowait()

            if self.task.done():
                return

            get_task: asyncio.Task[StreamingContentEvent] = asyncio.create_task(delta_queue.get())
            done, _pending = await asyncio.wait(
                {get_task, self.task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if get_task in done:
                yield get_task.result()
            else:
                # Task completed first; cancel the pending queue.get.
                get_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await get_task

    async def cache_hit(self) -> bool | None:
        """Return the terminal LLM result's cache flag when available.

        ``None`` means the completed result was not an ``LLMResult`` or no
        completed result was available. Terminal error/stop events and ordinary
        stream-runner exceptions also produce ``None`` here; call :meth:`model`
        when that distinction matters. Local Task cancellation still propagates.
        """
        await self._ensure_llm_result_loaded()
        return self._llm_result.cache_hit if self._llm_result else None

    async def llm_metrics(self) -> LLMResult | None:
        """Return the terminal ``LLMResult`` envelope when available.

        ``LLMResult`` exposes completion, validated data, cache status, and
        Tool calls, but no token, cost, or latency measurements. Terminal
        error/stop events and ordinary stream-runner exceptions return ``None``;
        local Task cancellation propagates.
        """
        await self._ensure_llm_result_loaded()
        return self._llm_result

    async def next(
        self,
        message: str,
        *,
        res: type[T] | None = None,
        deadline: datetime | None = None,
        response_format: ResponseFormat | None = None,
        streaming: bool | None = None,
        routing: RoutingPolicy | None = None,
        attachments: "list[PreparedAttachment] | None" = None,
        request_id: str | None = None,
    ) -> "AgentResponse[T]":
        """Start a continuation Task that carries this Task's conversation forward.

        Sends it as soon as the Platform has accepted this Task's request,
        without waiting for this Task's outcome: the Platform continues the
        conversation of this Task's last finished step, whether this Task is
        still running, completed, failed or stopped. A continuation that
        arrives before this Task finishes its first step waits for that step
        until its own deadline, then fails with ``PARENT_TASK_UNAVAILABLE``.
        The new Task has its own identifier (``request_id``, or a new UUID4),
        deadline, result and stop. A setting left as None takes this Task's
        value; attachments add documents. The new Task inherits only the
        conversation. The Platform accepts a continuation only while this
        immediate parent Task is within its own 30-day retention window.
        The continuation uses the inherited conversation context that remains
        available. Conversation content expires 30 days after the request that
        supplied it. System instructions may remain available for up to 90 days
        after the request that supplied them. Tool Calls of the last finished
        step that have no recorded reply appear to the model as 'status unknown'.
        This Task's final answer text is not part of the conversation.

        Requires ``agent:continue_task`` and ``agent:read_task`` on this Task,
        plus ``agent:read_task`` on the new Task. The new Task runs under this
        Task's Agent Identity.

        This method never raises this Task's outcome; call ``model()`` for it.
        Cancelling this call before it sends the continuation sends nothing and
        leaves this Task running.

        ``tests/test_feature_multiturn.py`` enforces the SDK side:
        ``test_next_continues_a_running_parent_at_once``,
        ``test_next_continues_after_any_parent_outcome``,
        ``test_next_waits_only_for_the_parents_acceptance``,
        ``test_cancelling_one_next_waiter_preserves_parent_and_sibling``,
        ``test_next_sends_only_the_criteria_the_caller_sets``,
        ``test_omitted_streaming_keeps_the_parents_local_buffering`` and
        ``test_continuation_http_rejection_uses_continuation_translation``; so do
        ``tests/test_http_transport_v3.py::test_task_error_context_identifies_the_operation``
        and ``tests/test_http_transport_v3.py::test_run_task_reports_acceptance_when_the_stream_starts``.
        No test in this repository enforces the Platform behavior described here.

        Args:
            message: New user turn for the continuation Task.
            res: Optional continuation result type for local validation.
                Omission does not inherit the parent's type. When
                ``response_format`` is absent, the SDK also derives the type's
                JSON Schema for the request. When neither ``res`` nor
                ``response_format`` is given, the Platform keeps this Task's
                response format.
            deadline: Timezone-aware continuation deadline. Omission uses the
                SDK's default Task timeout, not this Task's deadline.
            response_format: Explicit wire response format. It takes precedence
                over schema derivation from ``res``. None keeps this Task's
                value when ``res`` is also None.
            streaming: Request content delta/reset events for the continuation's
                ``stream()``. None keeps this Task's value, on the Platform and
                for the returned response's local buffering.
            routing: Routing Policy of the continuation Task. None keeps this
                Task's value.
            attachments: Prepared attachments already uploaded under
                ``request_id``. None adds no document.
            request_id: Task identifier of the continuation Task; upload its
                attachments under it with
                ``upload_attachments(request_id, attachments)``; None generates
                a UUID4.

        Returns:
            An ``AgentResponse`` whose ``task_id`` is ``request_id``, or a new
            UUID4.

        Raises:
            ValueError: If ``deadline`` is timezone-naive.
            asyncio.CancelledError: If this call is cancelled before it sends
                the continuation; this Task keeps running.

        A refused continuation (``AUTHORIZATION_FAILED``) raises
        ``DualeAIAuthError`` when the returned response's ``model()`` is
        awaited. A continuation of a parent that the Platform never admitted,
        or that does not exist, waits until its own deadline; ``model()`` then
        raises ``DualeAIError`` with ``PARENT_TASK_UNAVAILABLE``. A known parent
        Task that expired and whose record the Platform still holds ends the
        continuation with ``PARENT_TASK_NOT_FOUND``. A ``request_id`` already
        used by this Agent Identity names that existing Task: the returned
        response streams it, and no second Task starts.
        ``tests/test_feature_multiturn.py::TestUnitMultiTurnConversation::test_continuation_child_error_preserves_problem_details``
        enforces that ``model()`` raises the continuation's error code; no test
        in this repository enforces these Platform answers.

        Example:
            response = await ask(action="What's 2+2?", sdk=sdk)
            branch = await response.next(message="and times two?")
            result = await response.model()
            final = await branch.model()
        """
        await self._accepted.wait()
        return await self.sdk._continue_task(  # noqa: SLF001 - paired SDK response implementation
            parent_task_id=self.task_id,
            message=message,
            response_type=res,
            deadline=deadline,
            response_format=response_format,
            streaming=streaming,
            local_streaming=streaming if streaming is not None else self.streaming,
            routing_policy=routing,
            attachments=attachments,
            request_id=request_id,
        )

    async def stop(self, reason: str) -> TaskStopAccepted:
        """Submit a stop request for this Task.

        This does not wait for the Task to finish — waiting would defeat the
        purpose. The returned receipt does not prove that the Task stopped. If
        ``task.stopped`` later arrives, :meth:`model` raises
        :class:`TaskStoppedError` with the event's reason.

        Requires ``agent:stop_task`` on the Task; an Agent Identity holds it on
        its own Tasks by default. Without the permission the call raises
        ``DualeAIAuthError`` (``AUTHORIZATION_FAILED``) at once. A permitted
        stop is accepted even when the Task already ended or the Platform does
        not know it; a stop of a Task that already ended changes nothing.

        ``tests/test_task_stop.py::test_refused_stop_raises_the_authorization_error``
        enforces the refusal. No test in this repository enforces that a
        permitted stop of an ended or unknown Task is accepted.

        Args:
            reason: Why the task is being stopped. Required, non-empty.

        Returns:
            ``TaskStopAccepted`` request receipt.

        Raises:
            ValueError: If ``reason`` is empty or whitespace.
            DualeAIAuthError: If the Platform refuses the stop (403).
            DualeAIError: If the stop request fails before acceptance for any
                other reason.

        Example:
            response = await ask(action="Analyse this contract", sdk=sdk)
            await response.stop(reason="Wrong document supplied")

            try:
                await response.model()
            except TaskStoppedError as stopped:
                print(stopped.reason)
        """
        return await self.sdk.stop_task(self.task_id, reason)
