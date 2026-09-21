"""Auto-generated Pydantic models from the resolved schema compiler graph. Do not edit manually."""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, GetJsonSchemaHandler
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema

__all__: list[str] = ["ReservedToolName", "ReservedToolNames"]


def _validate_unique_strings_1(value: object) -> object:
    if isinstance(value, list) and all(isinstance(item, str) for item in value) and (len(value) != len(set(value))):
        raise ValueError("array items must be unique")
    return value


class ReservedToolName(str, Enum):
    """A platform-owned Tool name that customers cannot register or define for a Task."""

    exec_agent = "exec_agent"
    finish = "finish"
    list_agents = "list_agents"

    @staticmethod
    def __get_pydantic_json_schema__(core_schema: CoreSchema, handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        json_schema = handler.resolve_ref_schema(handler(core_schema))
        json_schema.update(
            {"description": "A platform-owned Tool name that customers cannot register or define for a Task."}
        )
        return json_schema


class ReservedToolNames(BaseModel):
    """Platform-owned Tool names that cannot be used in customer Tool registrations or per-Task Tool Definitions. A request using one of these names is rejected."""

    model_config = ConfigDict(
        extra="forbid",
        title="ReservedToolNames",
        json_schema_extra={"examples": [{"names": ["exec_agent", "finish", "list_agents"]}]},
    )
    names: Annotated[
        Annotated[
            list[ReservedToolName],
            Field(strict=True, min_length=3, max_length=3, json_schema_extra={"uniqueItems": True}),
            BeforeValidator(_validate_unique_strings_1),
        ],
        Field(
            description="Complete set of platform-owned Tool names rejected in customer Tool registrations and per-Task Tool Definitions."
        ),
    ]
