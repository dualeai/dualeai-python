"""Per-invocation context exposed to a running ``@tool`` callable.

A read-only handle giving the callable its ``tool_call_id``, the current attempt
number, and the absolute deadline. Tools read it with :func:`current_tool_context`.

``tool_call_id`` is issued by the model provider and passed through, so it is
stable across retries but not guaranteed globally unique. Scope it with
``task_id`` and, for durable side effects, a domain identifier or input digest.
Do not use ``attempt`` in the key: it restarts at 1 on redelivery.

Async Tool deadlines cancel the coroutine through ``asyncio.wait_for``. A
synchronous Tool runs in a worker thread and cannot be preempted, so this context
also lets it cooperate by checking time before side effects through
:meth:`ToolContext.remaining_seconds` and :meth:`ToolContext.is_expiring`.

Context propagation and deadline helpers are covered by
``tests/test_tool_context.py`` and ``tests/test_agent_lifecycle.py``.
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
    """Read-only context for the currently executing Tool call."""

    tool_call_id: str
    """Provider-issued call id, stable across retries but not globally unique.

    Never use it alone as an idempotency key. Pair it with ``task_id`` and an
    input or domain discriminator appropriate to the side effect. Durable
    deduplication remains the Tool application's responsibility."""

    task_id: str
    """The Task stream on which this Tool call arrived."""

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
