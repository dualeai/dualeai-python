"""Validate and normalize a tool return value into the bridge result shape (pure).

A ``str`` passes through; a mapping and a Pydantic model become the dict the bridge
carries; any other JSON value is wrapped as ``{"result": ...}``. A value the shape
cannot carry raises ``TypeError``.
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

    A ``str`` passes through. A mapping becomes a plain dict; a Pydantic model
    becomes its wire dump, which uses field aliases and omits what was never set.
    Any other JSON value (``None``, scalar, list, dataclass) is wrapped as
    ``{"result": value}`` so a tool need not hand-wrap every return. Everything but
    the ``str`` is checked against the bridge's JSON shape, and a value it cannot
    carry raises ``TypeError`` naming the type that reached the check — a dataclass
    is converted to a mapping first, so it is reported as ``dict``.
    """
    if isinstance(result, BaseModel):
        # A CUSTOMER'S MODEL IS NOT A GENERATED ONE, so its dump is checked like
        # every other branch. `dump_wire_model` is one `model_dump` and nothing
        # more, and the safe-integer bound its docstring relies on is the
        # generator's — a tool author writes a plain `int` field, which carries no
        # bound at all, so a value like 2**53 + 1 reaches the check below on this
        # branch exactly as it does on the mapping branch.
        candidate = dump_wire_model(result)
    else:
        if dataclasses.is_dataclass(result) and not isinstance(result, type):
            result = dataclasses.asdict(result)
        if isinstance(result, str):
            return result
        # A mapping validates as-is; any other JSON value (None, scalar, list) is
        # wrapped as {"result": ...}.
        candidate = dict(result) if isinstance(result, Mapping) else {"result": result}
    # One JSON-shape check for every branch, so a nested value the bridge cannot
    # carry raises the same offending-type error whatever the tool returned.
    try:
        return json_schema_adapter.validate_python(candidate)
    except (ValidationError, ValueError, TypeError) as exc:
        raise TypeError(
            f"Tool returned {type(result).__name__}; a tool result must be a str, a mapping, a "
            'Pydantic model, or a value wrappable as {"result": ...}, holding only JSON values '
            "whose numbers are within +/-(2**53 - 1)."
        ) from exc
