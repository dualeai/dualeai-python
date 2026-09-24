"""Model-facing tool-error rendering, bounded by the bridge wire schema.

Pure functions: a raised exception → the truncated ``message`` string the language
model sees. The truncation length is read from the bridge
``BridgeToolResultError.message`` ``maxLength`` (the wire contract), never a
hand-picked literal.

Rendering and truncation are covered by ``tests/test_tools_pure.py`` and
``tests/test_agent_lifecycle.py``.
"""

from __future__ import annotations

from collections.abc import Callable

from annotated_types import MaxLen

from dualeai.models.bridge import BridgeToolResultError


def _bridge_error_message_max_length() -> int:
    """Return the wire-declared max length of a tool error message.

    The size bound comes from the generated ``BridgeToolResultError.message``
    field metadata, so the SDK does not use a hand-picked literal.
    """
    for meta in BridgeToolResultError.model_fields["message"].metadata:
        if isinstance(meta, MaxLen):
            return meta.max_length
    raise RuntimeError("BridgeToolResultError.message is missing its schema max_length")


TOOL_ERROR_MESSAGE_MAX_CHARS = _bridge_error_message_max_length()


def truncate_to_wire_max(text: str) -> str:
    """Truncate to the bridge error ``message`` maxLength; an ellipsis marks the cut."""
    if len(text) > TOOL_ERROR_MESSAGE_MAX_CHARS:
        return text[: TOOL_ERROR_MESSAGE_MAX_CHARS - 1] + "…"
    return text


def format_registered_tool_error(exc: BaseException) -> str:
    """Render a tool failure as ``<module>.<qualname>: <message>`` for the wire result.

    The model receives a stable, typed-looking prefix instead of a bare
    message; the rendered string is truncated to the schema-declared maximum so an
    oversized exception string cannot exceed the wire contract. No error enum and no
    retryable flag are added — the Tool error stays a free-form ``message`` string.
    """
    qualified_name = f"{type(exc).__module__}.{type(exc).__qualname__}"
    message = str(exc)
    rendered = f"{qualified_name}: {message}" if message else qualified_name
    return truncate_to_wire_max(rendered)


def apply_tool_error(exc: Exception, transform: Callable[[Exception], str] | None) -> str:
    """Render the model-facing tool error, applying an optional redaction transform.

    Without a transform the default ``<module>.<qualname>: <message>`` is used. With
    one (a per-@tool ``error_transform``), the customer controls the model-facing text
    and the result is still truncated to the schema-declared maximum. Use the
    transform to remove secrets and internal details before they cross the wire.
    """
    if transform is None:
        return format_registered_tool_error(exc)
    rendered = transform(exc)
    if not isinstance(rendered, str):
        # A non-str return (e.g. ``return [str(e)]``) would otherwise reach the wire
        # model and raise on construction inside an except block, crashing the task
        # runner. Reject it here so the caller can fail closed to a generic message.
        raise TypeError(f"error_transform must return str, got {type(rendered).__name__}")
    return truncate_to_wire_max(rendered)
