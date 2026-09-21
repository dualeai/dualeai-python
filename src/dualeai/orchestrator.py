"""Task orchestration and response handling with HTTP bridge (RFC-051).

Thin convenience wrappers around ``DualeAISDK.submit_task`` and
``AgentResponse.next``. Both return ``AgentResponse`` directly; the
orchestrator forwards arguments and applies skill→task_type derivation.
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
    returned response carries the client-generated child ID. State preservation
    and sibling semantics match :meth:`AgentResponse.next`.
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
    """Ask for work to be done by an agent.

    Submits a task via the HTTP bridge and returns an
    ``AgentResponse`` whose ``task`` attribute drives the bridge SSE
    iteration. Cancel via ``response.task.cancel()`` propagates to
    the bridge connection.

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
