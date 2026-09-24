"""Decorators for defining tools and activities with dependency injection.

Tool registration state lives on the SDK instance; hosted-tool lifecycle
registration publishes its Tool manifest through ``sdk.serve()``.

Decorator registration and validation behavior is covered by
``tests/test_decorators_unit.py``.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import TYPE_CHECKING, ParamSpec, TypeVar, overload

from dualeai.tools._util import callable_name

if TYPE_CHECKING:
    from dualeai.sdk import CacheableValue, DualeAISDK

P = ParamSpec("P")
R = TypeVar("R")


def _ensure_async_function(func: Callable[..., object], kind: str) -> None:
    """Reject non-async decorators without changing static callable typing."""
    if not inspect.iscoroutinefunction(func):
        raise TypeError(f"{kind} function {callable_name(func)} must be async")


@overload
def activity(
    func: Callable[P, Awaitable[CacheableValue]],
    /,
) -> Callable[P, Awaitable[CacheableValue]]: ...


@overload
def activity(
    cache_ttl: timedelta | None = None,
    max_retries: int = 3,
    *,
    sdk: DualeAISDK | None = None,
) -> Callable[[Callable[P, Awaitable[CacheableValue]]], Callable[P, Awaitable[CacheableValue]]]: ...


def activity(
    cache_ttl: timedelta | Callable[P, Awaitable[CacheableValue]] | None = None,
    max_retries: int = 3,
    *,
    sdk: DualeAISDK | None = None,
) -> (
    Callable[[Callable[P, Awaitable[CacheableValue]]], Callable[P, Awaitable[CacheableValue]]]
    | Callable[P, Awaitable[CacheableValue]]
):
    """Cache and retry an async activity through a specific SDK instance.

    Caching can avoid a repeated execution for the same cache key. It does not
    make the underlying operation idempotent. Every ordinary ``Exception`` is
    eligible for retry, so a side-effecting activity must be safe for up to
    ``max_retries + 1`` executions.

    The decorator requires ``async def`` even though
    :meth:`DualeAISDK.execute_activity <dualeai.sdk.DualeAISDK.execute_activity>`
    also accepts synchronous callables. Omitting ``cache_ttl`` here selects one
    hour; passing ``None`` directly to ``execute_activity`` instead stores
    without backend expiry.

    Args:
        cache_ttl: How long to cache results. Omission or ``None`` selects one
            hour for the decorator.
        max_retries: Retries after the initial activity attempt. Use a
            non-negative integer; activity retry counts are not proactively
            validated.
        sdk: SDK instance to use. Required.

    Returns:
        Decorated async function with caching and retry behavior.

    Raises:
        TypeError: When the decorated callable is not asynchronous.
        ValueError: When ``sdk`` is omitted.
    """
    if not isinstance(cache_ttl, timedelta) and callable(cache_ttl):
        return _decorate_activity(cache_ttl, timedelta(hours=1), max_retries, sdk)

    resolved_cache_ttl = cache_ttl if cache_ttl is not None else timedelta(hours=1)

    def decorator(func: Callable[P, Awaitable[CacheableValue]]) -> Callable[P, Awaitable[CacheableValue]]:
        return _decorate_activity(func, resolved_cache_ttl, max_retries, sdk)

    return decorator


def _decorate_activity(
    func: Callable[P, Awaitable[CacheableValue]],
    cache_ttl: timedelta,
    max_retries: int,
    sdk: DualeAISDK | None,
) -> Callable[P, Awaitable[CacheableValue]]:
    _ensure_async_function(func, "Activity")
    if sdk is None:
        raise ValueError(
            "SDK instance required. Pass sdk parameter to @activity decorator. Example: @activity(sdk=my_sdk)"
        )
    activity_sdk = sdk

    @functools.wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> CacheableValue:
        return await activity_sdk.execute_activity(func, cache_ttl, max_retries, *args, **kwargs)

    return wrapper


def tool(
    *,
    sdk: DualeAISDK | None = None,
    description: str,
    timeout: timedelta,
    retries: int = 0,
    error_transform: Callable[[Exception], str] | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Declare a customer-hosted tool on an SDK instance.

    The decorated callable runs in your process when the platform language model
    calls the tool inside a conversation rooted at this agent.

    - ``async def`` or a plain ``def`` — a sync tool runs in a worker thread so a
      blocking call never stalls the SDK event loop. Parameters are validated
      against their type annotations before your code runs; the return value must
      be a ``str``, a mapping, or a Pydantic model (or any JSON value, wrapped as
      ``{"result": ...}``).
    - ``description`` is sent to the language model. Python docstrings are NOT.
      Document per-parameter constraints and hints with ``Annotated[T,
      Field(...)]`` (e.g. ``Annotated[int, Field(gt=0, le=10, description="1
      to 10")]``) — these reach the model schema; a bare docstring does not.

    The SDK compiles one parameter model at registration and reuses it for
    schema publication and invocation validation. Parameters must be named,
    annotated, and representable by Pydantic. Positional-only parameters,
    variadics, unresolved type variables, reserved Pydantic names, ambiguous
    aliases, and unexpected TOP-LEVEL input fields are rejected. An unexpected
    field inside a nested model is NOT rejected: `extra="forbid"` is set on the
    generated parameters wrapper only, and `$defs` entries keep the author's own
    config, so give every nested model its own strict config. Omitted Python defaults
    stay omitted, and booleans do not coerce to integers or integers to
    booleans.

    Idempotency (important): tool delivery is at-least-once. After an SDK process
    restart, bridge replay can re-deliver a ``tool.use`` and re-execute your
    callable, and a ``tool_timeout`` is side-effect-uncertain (your code may have
    run even when no result was recorded). Opt-in ``retries`` adds a second
    at-least-once source, and a per-attempt timeout cancels the callable mid-run,
    so a retried side effect may have partially applied. Make side-effecting
    tools idempotent or safe to retry before setting ``retries`` above 0.

    Cancellation (async tools): when the deadline fires, the callable is
    cancelled at its next ``await`` (``asyncio.CancelledError``). Put cleanup in
    ``try/finally`` — a bare ``except Exception`` will NOT catch the
    cancellation, so that cleanup is skipped; and never use
    ``except BaseException``, which swallows the cancel and breaks the deadline.

    Cancellation (sync tools): a plain ``def`` tool runs in a worker thread and
    is NOT preemptible — there is no ``await`` at which to inject
    ``CancelledError``. When the deadline fires the SDK stops waiting and records
    a ``tool_timeout``, but the thread keeps running your body to completion
    (its result is then discarded). To respect the deadline, poll
    ``current_tool_context().is_expiring()`` and checkpoint before side effects.
    On process shutdown a still-running sync side-effect thread may be killed
    mid-operation, so keep such tools idempotent.

    Error reporting: an uncaught exception is returned to the platform as
    ``<module>.<qualname>: <message>``, truncated to the wire limit. That text
    reaches the language-model provider — do not put secrets, credentials, or
    connection strings in exception messages.

    Args:
        sdk: SDK instance the tool is registered on.
        description: Model-facing description of what the tool does.
        timeout: Per-call execution deadline.
        retries: Additional retry attempts beyond the first run for a failing
            callable, bounded by ``timeout`` and the call deadline. Default 0
            (no retry). Only enable for idempotent or retry-safe tools.
        error_transform: Optional hook mapping a raised exception to the
            model-facing error message — a redaction seam for secrets/PII. When
            omitted, the default ``<module>.<qualname>: <message>`` is used.
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        if sdk is None:
            raise ValueError("SDK instance required. Pass sdk parameter to @tool decorator. Example: @tool(sdk=my_sdk)")
        tool_sdk = sdk
        tool_sdk.register_tool(
            func, description=description, timeout=timeout, retries=retries, error_transform=error_transform
        )

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            # An async tool forwards its coroutine (the caller awaits it); a sync
            # tool returns its value directly. Execution-time thread-offload for
            # sync tools is handled in register_tool.
            return func(*args, **kwargs)

        return wrapper

    return decorator
