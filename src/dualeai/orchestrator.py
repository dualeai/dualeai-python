"""Convenience functions for Task submission and continuation.

Thin convenience wrappers around ``DualeAISDK.submit_task`` and
``AgentResponse.next``. Both return ``AgentResponse`` directly; the
orchestrator forwards arguments and applies skill→task_type derivation.

Forwarding behavior is covered by ``tests/test_feature_ask.py`` and
``tests/test_feature_multiturn.py``.
"""

from datetime import datetime
from typing import TYPE_CHECKING, TypeVar, overload

from dualeai.models.response_format import ResponseFormat
from dualeai.models.routing_policy import RoutingPolicy
from dualeai.models.skill_enum import SkillEnum
from dualeai.response import AgentResponse

if TYPE_CHECKING:
    from dualeai.attachments import PreparedAttachment
    from dualeai.sdk import DualeAISDK

T = TypeVar("T")


async def continue_conversation(
    response: AgentResponse[T],
    message: str,
    *,
    deadline: datetime | None = None,
    response_format: ResponseFormat | None = None,
) -> AgentResponse[T]:
    """Continue a successful response with a new message.

    The response owns both its public parent ID and its SDK instance. The
    returned response carries the client-generated child ID. Waiting and sibling
    behavior match :meth:`AgentResponse.next`.

    Args:
        response: Parent response, which must finish successfully first.
        message: User message for the child Task.
        deadline: Optional timezone-aware child deadline.
        response_format: Explicit wire response format for the child.

    Returns:
        A non-streaming child response using the parent's expected result type.

    Raises:
        DualeAIError: If the parent ended with ``task.error``.
        TaskStoppedError: If the parent ended with ``task.stopped``.
        ValueError: If ``deadline`` is timezone-naive.
        asyncio.CancelledError: If this waiter is cancelled; the shared parent
            Task continues.
    """
    return await response.next(
        message=message,
        deadline=deadline,
        response_format=response_format,
    )


@overload
async def ask(
    action: str,
    skills: list[SkillEnum] | None,
    res: type[T],
    response_format: ResponseFormat | None = None,
    routing: RoutingPolicy | None = None,
    *,
    streaming: bool = False,
    deadline: datetime | None = None,
    attachments: "list[PreparedAttachment] | None" = None,
    request_id: str | None = None,
    sdk: "DualeAISDK",
) -> "AgentResponse[T]": ...


@overload
async def ask(
    action: str,
    skills: list[SkillEnum] | None = None,
    *,
    res: type[T],
    response_format: ResponseFormat | None = None,
    routing: RoutingPolicy | None = None,
    streaming: bool = False,
    deadline: datetime | None = None,
    attachments: "list[PreparedAttachment] | None" = None,
    request_id: str | None = None,
    sdk: "DualeAISDK",
) -> "AgentResponse[T]": ...


@overload
async def ask(
    action: str,
    skills: list[SkillEnum] | None = None,
    res: None = None,
    response_format: ResponseFormat | None = None,
    routing: RoutingPolicy | None = None,
    *,
    streaming: bool = False,
    deadline: datetime | None = None,
    attachments: "list[PreparedAttachment] | None" = None,
    request_id: str | None = None,
    sdk: "DualeAISDK",
) -> "AgentResponse[object]": ...


async def ask(
    action: str,
    skills: list[SkillEnum] | None = None,
    res: type[T] | None = None,
    response_format: ResponseFormat | None = None,
    routing: RoutingPolicy | None = None,
    *,
    streaming: bool = False,
    deadline: datetime | None = None,
    attachments: "list[PreparedAttachment] | None" = None,
    request_id: str | None = None,
    sdk: "DualeAISDK",
) -> "AgentResponse[T] | AgentResponse[object]":
    """Submit a root Task and return its response handle.

    ``await ask(...)`` returns after the local SSE runner is created, not after
    the Task completes. Canceling ``response.task`` closes that local stream; use
    ``response.stop()`` for a separate platform stop request. Await
    ``response.model()`` to translate terminal errors and validate the result.

    Args:
        action: Non-empty instruction for the Task.
        skills: Optional routing skills. When ``routing`` is absent they become
            ``required_skills``; they also select an observability ``task_type``.
        res: Optional Python/Pydantic type for local result validation. When no
            explicit ``response_format`` is supplied, its JSON Schema is sent in
            the request.
        response_format: Explicit wire response format; takes precedence over
            schema derivation from ``res``.
        routing: Explicit routing policy. When present it takes precedence over
            the policy otherwise derived from ``skills``.
        streaming: Request content delta/reset events for ``response.stream()``.
        deadline: Optional timezone-aware absolute deadline. Omission uses the
            SDK's default Task timeout.
        attachments: Prepared attachments already uploaded for the same Task id.
            This function does not upload or verify their remote state.
        request_id: Optional client-selected root Task id. When attachments are
            used, pass the same id used for upload. Omission generates UUID4.
        sdk: SDK instance that owns the Task stream.

    Returns:
        ``AgentResponse`` for the root Task.

    Raises:
        ValueError: If ``action`` is empty or whitespace.
        TypeError: If a supplied root ``deadline`` is timezone-naive.
        RuntimeError: If the process-local Task dependency circuit is open.

    Transport failures after the runner is created surface when ``model()`` or
    the runner Task is awaited.

    Example:
        # With type — returns AgentResponse[Invoice]
        response = await ask("Extract invoice", res=Invoice, sdk=sdk)
        invoice: Invoice = await response.model()

        # Streaming
        from dualeai import BridgeContentResetResponse

        response = await ask("Generate story", streaming=True, sdk=sdk)
        content: list[str] = []
        async for event in response.stream():
            if isinstance(event, BridgeContentResetResponse):
                content.clear()
                continue
            content.append(event.delta)
        story = await response.model()
    """
    # Skill→task_type derivation: observability metric only.
    task_type = "completion"
    if skills:
        if SkillEnum.code in skills:
            task_type = "generation"
        elif SkillEnum.analysis in skills:
            task_type = "analysis"
        elif SkillEnum.reasoning in skills:
            task_type = "reasoning"

    return await sdk.submit_task(
        action=action,
        skills=skills or [],
        routing_policy=routing,
        response_type=res,
        response_format=response_format,
        streaming=streaming,
        deadline=deadline,
        task_type=task_type,
        attachments=attachments,
        request_id=request_id,
    )
