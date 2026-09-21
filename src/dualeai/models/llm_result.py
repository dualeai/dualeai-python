"""Auto-generated Pydantic models from the resolved schema compiler graph. Do not edit manually."""

from __future__ import annotations

from typing import Annotated, Union

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, StrictStr
from pydantic.json_schema import SkipJsonSchema

from dualeai.models import json_value as json_value_module
from dualeai.models import tool_call as tool_call_module

__all__: list[str] = ["LLMResult"]


def _reject_explicit_null(value: object) -> object:
    if value is None:
        raise ValueError("explicit null is not allowed; omit the field instead")
    return value


class LLMResult(BaseModel):
    """Generated-content payload. On a completed public Task, this is the Task Result carried by the terminal event."""

    model_config = ConfigDict(extra="forbid", title="LLMResult", json_schema_extra=None)
    completion: Annotated[
        Union[StrictStr, None],
        Field(description="Text completion for standard completions", json_schema_extra={"default": None}),
    ] = None
    validated_data: Annotated[
        Union[Annotated[dict[StrictStr, json_value_module.JsonValue], Field(strict=True)], None],
        Field(description="Validated structured output for schema validations", json_schema_extra={"default": None}),
    ] = None
    cache_hit: Annotated[
        Union[StrictBool, SkipJsonSchema[None]],
        BeforeValidator(_reject_explicit_null),
        Field(description="Whether this response was served from cache", json_schema_extra={"default": False}),
    ] = Field(default_factory=lambda: None, validate_default=False, exclude_if=lambda value: value is None)
    tool_calls: Annotated[
        Union[Annotated[list[tool_call_module.ToolCall], Field(strict=True)], None],
        Field(description="Tool Calls requested by the model.", json_schema_extra={"default": None}),
    ] = None
