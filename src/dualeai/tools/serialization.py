"""Normalize a tool return value into the bridge result shape (pure).

A ``str``, mapping, or Pydantic model passes through; any other JSON value is wrapped
as ``{"result": ...}``; a non-serializable return raises with the offending type named.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping

from pydantic import BaseModel, ValidationError

from dualeai._wire import dump_wire_model
from dualeai.models.json_value import JsonValue
from dualeai.tools._json import json_schema_adapter


def tool_success_output(result: object) -> dict[str, JsonValue | None] | str:
    """Validate and normalize a tool return value against the bridge result shape.

    A ``str``, mapping, or Pydantic model passes through directly. Any other
    JSON-serializable return (``None``, scalar, list, dataclass) is wrapped as
    ``{"result": value}`` so a tool need not hand-wrap every return. A non-serializable
    return raises with the offending type named.
    """
    if isinstance(result, BaseModel):
        # dump_wire_model already validates through the identical JSON-object
        # adapter; a second walk here could never fail.
        return dump_wire_model(result)
    if dataclasses.is_dataclass(result) and not isinstance(result, type):
        result = dataclasses.asdict(result)
    if isinstance(result, str):
        return result
    # A mapping validates as-is; any other JSON value (None, scalar, list) is wrapped
    # as {"result": ...}. Both paths share one JSON-serializability check so a
    # non-serializable nested value raises the same offending-type error.
    candidate = dict(result) if isinstance(result, Mapping) else {"result": result}
    try:
        return json_schema_adapter.validate_python(candidate)
    except (ValidationError, ValueError, TypeError) as exc:
        raise TypeError(
            f"Tool returned {type(result).__name__}; a tool result must be JSON-serializable "
            '(a str, a mapping, a Pydantic model, or a value wrappable as {"result": ...}).'
        ) from exc
