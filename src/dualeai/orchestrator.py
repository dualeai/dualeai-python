"""Convenience function for Task submission.

One convenience function, ``ask``, forwards to ``DualeAISDK.submit_task`` and
derives the observability ``task_type`` from the requested capabilities.
The helper's result-type default is unproved.
"""

from datetime import datetime
from typing import TYPE_CHECKING, TypeVar, overload

from dualeai.models.capability import Capability
from dualeai.models.priority_class import PriorityClass
from dualeai.models.response_format import ResponseFormat
from dualeai.models.routing_policy import RoutingPolicy
from dualeai.response import AgentResponse

if TYPE_CHECKING:
    from dualeai.attachments import PreparedAttachment
    from dualeai.sdk import DualeAISDK

T = TypeVar("T")


@overload
async def ask(
    action: str,
    capabilities: list[Capability] | None,
    res: type[T],
    response_format: ResponseFormat | None = None,
    routing: RoutingPolicy | None = None,
    *,
    priority: PriorityClass = PriorityClass.standard,
    streaming: bool = False,
    deadline: datetime | None = None,
    attachments: "list[PreparedAttachment] | None" = None,
    task_id: str | None = None,
    sdk: "DualeAISDK",
) -> "AgentResponse[T]": ...


@overload
async def ask(
    action: str,
    capabilities: list[Capability] | None = None,
    *,
    res: type[T],
    response_format: ResponseFormat | None = None,
    routing: RoutingPolicy | None = None,
    priority: PriorityClass = PriorityClass.standard,
    streaming: bool = False,
    deadline: datetime | None = None,
    attachments: "list[PreparedAttachment] | None" = None,
    task_id: str | None = None,
    sdk: "DualeAISDK",
) -> "AgentResponse[T]": ...


@overload
async def ask(
    action: str,
    capabilities: list[Capability] | None = None,
    res: None = None,
    response_format: ResponseFormat | None = None,
    routing: RoutingPolicy | None = None,
    *,
    priority: PriorityClass = PriorityClass.standard,
    streaming: bool = False,
    deadline: datetime | None = None,
    attachments: "list[PreparedAttachment] | None" = None,
    task_id: str | None = None,
    sdk: "DualeAISDK",
) -> "AgentResponse[object]": ...


async def ask(
    action: str,
    capabilities: list[Capability] | None = None,
    res: type[T] | None = None,
    response_format: ResponseFormat | None = None,
    routing: RoutingPolicy | None = None,
    *,
    priority: PriorityClass = PriorityClass.standard,
    streaming: bool = False,
    deadline: datetime | None = None,
    attachments: "list[PreparedAttachment] | None" = None,
    task_id: str | None = None,
    sdk: "DualeAISDK",
) -> "AgentResponse[T] | AgentResponse[object]":
    """Submit a root Task and return its response handle.

    ``await ask(...)`` returns after the local SSE runner is created, not after
    the Task completes. Canceling ``response.task`` closes that local stream; use
    ``response.stop()`` for a separate platform stop request. Await
    ``response.model()`` to translate terminal errors and validate the result.

    Args:
        action: Non-empty instruction for the Task.
        capabilities: Optional routing capabilities. When ``routing`` is absent they become
            ``required_capabilities``; they also select an observability ``task_type``.
        res: Optional Python/Pydantic type for local result validation. When no
            explicit ``response_format`` is supplied, its JSON Schema is sent in
            the request.
        response_format: Explicit wire response format; takes precedence over
            schema derivation from ``res``.
        routing: Explicit routing policy. When present it takes precedence over
            the policy otherwise derived from ``capabilities``.
        priority: Task scheduling class. Standard favors throughput; fast favors
            lower waiting time for the same work and uses the Fast Task price.
        streaming: Request content delta/reset events for ``response.stream()``.
            The Platform streams only a free-text answer: no ``res`` or
            ``response_format``, or the ``text`` or ``markdown`` format. No
            test in this repository enforces this Platform behavior.
        deadline: Optional timezone-aware absolute deadline. Omission uses the
            SDK's default Task timeout.
        attachments: Prepared attachments already uploaded for the same Task id.
            This function does not upload or verify their remote state.
        task_id: Optional root Task id, the Task identifier that the developer or
            the SDK selects. When attachments are used, pass the same id used for
            upload. Omission generates UUID4.
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

        # Streaming: events can arrive out of order; order them by generation and sequence as
        # https://github.com/dualeai/dualeai-python/blob/main/examples/README.md#order-and-replace-streamed-content
        # shows.
        response = await ask("Generate story", streaming=True, sdk=sdk)
        async for event in response.stream():
            render(event)  # your ordering view
        story = await response.model()
    """
    # Capability→task_type derivation: observability metric only.
    task_type = "completion"
    if capabilities:
        if Capability.code in capabilities:
            task_type = "generation"
        elif Capability.problem_solving in capabilities:
            task_type = "reasoning"

    return await sdk.submit_task(
        action=action,
        capabilities=capabilities or [],
        routing_policy=routing,
        priority=priority,
        response_type=res,
        response_format=response_format,
        streaming=streaming,
        deadline=deadline,
        task_type=task_type,
        attachments=attachments,
        task_id=task_id,
    )
