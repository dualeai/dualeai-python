"""Agent response handling with HTTP bridge streaming support.

This module provides the AgentResponse class that wraps an
``asyncio.Task`` running the bridge SSE iteration.

Key Features:
- SSE streaming via bridge API (real-time deltas)
- Type-safe response validation with Pydantic models
- ``response.task.cancel()`` closes the local bridge connection only; the
  platform keeps running the task. ``response.stop()`` stops the work itself.
"""

import asyncio
import contextlib
from collections.abc import AsyncGenerator, Coroutine
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
from dualeai.models.task_stop import TaskStopAccepted
from dualeai.utils import get_type_name

if TYPE_CHECKING:
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
    ``BILLING_LIMIT_EXCEEDED``, ``AUTHORIZATION_FAILED``,
    ``RESOURCE_NOT_FOUND``, ``TOOL_VALIDATION_FAILED``,
    ``LOOP_DETECTED``, ``INTERNAL_ROUTING``) and callers branch on it
    directly. Cloudflare extension fields (``retryable``,
    ``retry_after_seconds``, ``owner_action_required``, ``error_category``,
    ``ai_hints``) live on ``exc.problem_details`` so retry logic reads them
    without an SDK-side mapping table.

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
    """Response from an agent execution with HTTP bridge streaming support.

    Generic over T, the expected response type.
    """

    def __init__(
        self,
        task_id: str,
        task: asyncio.Task[TerminalEvent],
        expected_type: type[T] | None = None,
        *,
        sdk: "DualeAISDK",
        streaming: bool = False,
        delta_queue: asyncio.Queue[StreamingContentEvent] | None = None,
        deltas: list[BridgeContentDeltaResponse] | None = None,
    ) -> None:
        """Initialize agent response.

        Args:
            task_id: ID of the task being executed.
            task: The ``asyncio.Task`` running the bridge SSE
                iteration (returned by
                :meth:`DualeAISDK._spawn_run_task`). It awaits to a
                ``BridgeTaskCompletedResponse`` or
                ``BridgeTaskErrorResponse``.
            expected_type: Expected result type for ``model()``
                validation.
            sdk: SDK instance — kept on the response so ``next()`` can
                start a continuation.
            streaming: Whether streaming deltas were requested.
            delta_queue / deltas: Shared streaming buffers populated by
                the spawn-time callbacks. SDK passes these in so the spawn
                callback can close over them without needing the response
                object yet.
        """
        self.task_id: str = task_id
        self.task: asyncio.Task[TerminalEvent] = task
        self.expected_type: type[T] | None = expected_type
        self.sdk: DualeAISDK = sdk
        self.streaming: bool = streaming
        # LLMResult cached after first model() call.
        self._llm_result: LLMResult | None = None
        self._llm_result_loaded: bool = False
        self._llm_result_lock: asyncio.Lock = asyncio.Lock()
        # Streaming delivery: live queue (event-driven) + replay list. Keep
        # both absent for non-streaming responses; tool-use delivery is
        # scheduled directly by DualeAISDK and needs no response-owned queue.
        self._delta_queue = delta_queue
        self._deltas = deltas

    async def _await_terminal(self, *, isolate_waiter_cancellation: bool = False) -> "TerminalEvent":
        """Await the terminal event, optionally preserving shared task ownership."""
        try:
            if isolate_waiter_cancellation:
                return await asyncio.shield(self.task)
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
            logger.error("Task failed", task_id=self.task_id, error=str(task_err))
            raise

    def _unwrap_llm_result(self, result: LLMResult, *, warn_on_empty: bool = False) -> object:
        """Cache the LLMResult and return its inner payload.

        Order: ``validated_data`` (router-validated against the
        expected schema) → ``completion`` (raw LLM text) → the wrapper
        itself. Both model and stream extraction funnel through here so
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
                # Never include result CONTENT (PII). Type metadata only.
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
            DualeAIError: If the router emitted a `task.error` terminal event. The
                raised instance carries the canonical RFC 9457 ProblemDetails on
                `exc.problem_details` (with `error_code`, `detail`, and Cloudflare
                extension fields `retryable`, `retry_after_seconds`,
                `owner_action_required`, `error_category`, `ai_hints`). Callers
                branch on `exc.problem_details.error_code` directly — there is
                no SDK-side re-mapping table.
            ValidationError: If result doesn't match expected_type schema.
        """
        terminal = await self._await_terminal()

        if isinstance(terminal, BridgeTaskStoppedResponse):
            # A stop is not a task failure, so it carries no problem details.
            raise TaskStoppedError(terminal.reason, task_id=self.task_id)
        if isinstance(terminal, BridgeTaskErrorResponse):
            raise _exception_from_terminal(terminal, self.task_id)
        result = self._extract_result(terminal)

        logger.debug(
            # Never log result CONTENT (PII). Type metadata only.
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

        Yields content deltas as the bridge produces them. A content reset
        tells consumers to discard previously rendered deltas before applying
        later replacement output. Backed by ``asyncio.Queue`` (event-driven,
        no busy-poll).

        Replay: iterating ``stream()`` after the task is done yields
        the buffered ``_deltas`` list — useful for re-rendering on UI
        re-mount.

        With ``streaming=False`` this method just awaits the task and
        returns without yielding.
        """
        if not self.streaming:
            await self.task
            return

        if self._delta_queue is None or self._deltas is None:
            raise RuntimeError("Streaming response is missing its content buffers")

        # Replay path after the terminal task has completed.
        if self.task.done():
            for delta in self._deltas:
                yield delta
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
            while not self._delta_queue.empty():
                yield self._delta_queue.get_nowait()

            if self.task.done():
                return

            get_task: asyncio.Task[StreamingContentEvent] = asyncio.create_task(self._delta_queue.get())
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
        """Check if the response was served from cache."""
        await self._ensure_llm_result_loaded()
        return self._llm_result.cache_hit if self._llm_result else None

    async def llm_metrics(self) -> LLMResult | None:
        """Get the full LLM metrics object."""
        await self._ensure_llm_result_loaded()
        return self._llm_result

    async def next(
        self,
        message: str,
        *,
        res: type[T] | None = None,
        deadline: datetime | None = None,
        response_format: ResponseFormat | None = None,
    ) -> "AgentResponse[T]":
        """Continue this task with the next message, maintaining conversation context.

        Waits for this response to finish successfully before the SDK creates
        and submits a distinct child task. A parent error or cancellation is
        propagated without submitting a child.

        The platform preserves the canonical conversation lineage, routing
        context, and original attachment references. Each model call receives
        the history allowed by its context budget and checkpoints. Every call
        creates an independent, non-streaming child of this response; it does
        not mutate this parent or another sibling continuation.

        Args:
            message: New user turn for the child task.
            res: Optional result type for local validation. When
                ``response_format`` is absent, the SDK also derives its JSON
                Schema for the request.
            deadline: Timezone-aware child deadline. Omission uses the SDK task
                timeout; the platform bounds it by the parent route deadline.
            response_format: Explicit wire response format. It takes precedence
                over schema derivation from ``res``.

        Returns:
            An ``AgentResponse`` whose ``task_id`` is the new UUID4 child ID.

        Raises:
            DualeAIError: If the parent finished with a task error.
            ValueError: If ``deadline`` is timezone-naive.

        Cancelling this waiter does not cancel the shared parent task or other
        callers waiting to create sibling continuations.

        Example:
            response = await ask(action="What's 2+2?", sdk=sdk)
            result = await response.model()  # "4"

            continued = await response.next(message="and times two?")
            final = await continued.model()  # "8"
        """
        terminal = await self._await_terminal(isolate_waiter_cancellation=True)
        if isinstance(terminal, BridgeTaskStoppedResponse):
            # A stop is not a task failure, so it carries no problem details.
            raise TaskStoppedError(terminal.reason, task_id=self.task_id)
        if isinstance(terminal, BridgeTaskErrorResponse):
            raise _exception_from_terminal(terminal, self.task_id)
        return await self.sdk._continue_task(  # noqa: SLF001 - paired SDK response implementation
            parent_task_id=self.task_id,
            message=message,
            response_type=res,
            deadline=deadline,
            response_format=response_format,
        )

    async def stop(self, reason: str) -> TaskStopAccepted:
        """Stop this task and every task started under it.

        Unlike :meth:`next`, this does not wait for the task to finish — waiting
        would defeat the purpose. It returns as soon as the platform accepts the
        request; the task then ends with a stopped result, so awaiting
        :meth:`model` raises :class:`TaskStoppedError` carrying this reason.

        A stopped task is still billed for what it already consumed, and work
        already sent to a model provider or a tool is not undone.

        Args:
            reason: Why the task is being stopped. Required, non-empty.

        Returns:
            ``TaskStopAccepted``, naming the task and the moment of acceptance.

        Example:
            response = await ask(action="Analyse this contract", sdk=sdk)
            await response.stop(reason="Wrong document supplied")

            try:
                await response.model()
            except TaskStoppedError as stopped:
                print(stopped.reason)
        """
        return await self.sdk.stop_task(self.task_id, reason)

    def __or__(self, message: str) -> Coroutine[object, object, "AgentResponse[T]"]:
        """Pipe operator for continuing conversations."""
        return self.next(message)
