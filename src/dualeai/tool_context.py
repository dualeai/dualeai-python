"""Per-invocation context exposed to a running ``@tool`` callable.

A read-only handle giving the callable its ``tool_call_id``, the current attempt
number, and the absolute deadline. Tools read it with :func:`current_tool_context`.

``tool_call_id`` is issued by the model provider and passed through, so it is
retry-constant but NOT platform-minted and NOT guaranteed unique: the router's
own dispatch key scopes it by route span
(``sdk-tool-dispatch:{tenant_id}:{parent_route_span_id}:{tool_call_id}``) because
provider-local ids can repeat across turns of one task. Build an idempotency key
from ``(task_id, tool_call_id)`` plus an input digest, never from
``tool_call_id`` alone and never from ``attempt``, which restarts at 1 on every
redelivery.

The deadline is enforced by a forced ``asyncio.wait_for`` cancellation; this
context lets a callable cooperate — checkpoint before a side effect, or skip work
it cannot finish — via :meth:`ToolContext.remaining_seconds` /
:meth:`ToolContext.is_expiring`.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

_CURRENT_TOOL_CONTEXT: contextvars.ContextVar[ToolContext | None] = contextvars.ContextVar(
    "dualeai_current_tool_context",
    default=None,
)


@dataclass(frozen=True)
class ToolContext:
    """Read-only context for the currently executing tool call."""

    tool_call_id: str
    """Provider-issued call id. Stable across retries of one call, NOT unique.

    Never use it alone as an idempotency key. Pair it with ``task_id`` and an
    input digest. Nothing guarantees that two genuine requests differ even then:
    the provider may reuse an id, and every turn of one conversation shares a
    task. Give a repeatable effect its own discriminator in the tool input."""

    task_id: str
    """The task/conversation stream this tool call belongs to."""

    attempt: int
    """1-based attempt number (1 on the first run; increments on opt-in retry)."""

    deadline_at: datetime
    """Absolute deadline for this call; all attempts share it."""

    def remaining_seconds(self) -> float:
        """Seconds left before the deadline (never negative)."""
        return max(0.0, (self.deadline_at - datetime.now(timezone.utc)).total_seconds())

    def is_expiring(self, within_seconds: float = 0.5) -> bool:
        """True when less than ``within_seconds`` remain — checkpoint before side effects."""
        return self.remaining_seconds() <= within_seconds


def current_tool_context() -> ToolContext | None:
    """Return the context for the tool call in progress, or ``None`` outside a tool."""
    return _CURRENT_TOOL_CONTEXT.get()


@contextmanager
def _bind_tool_context(context: ToolContext) -> Iterator[None]:
    """Bind ``context`` for the duration of one callable invocation."""
    token = _CURRENT_TOOL_CONTEXT.set(context)
    try:
        yield
    finally:
        _CURRENT_TOOL_CONTEXT.reset(token)
