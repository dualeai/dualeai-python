"""Auto-generated Pydantic models from the resolved schema compiler graph. Do not edit manually."""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, StrictStr
from pydantic.json_schema import SkipJsonSchema
from typing_extensions import TypeAliasType

from dualeai.models import json_value as json_value_module
from dualeai.models import tool_name as tool_name_module

__all__: list[str] = ["Parameters", "RequireToolUse", "Tool"]


def _validate_boolean_literal_1(value: object) -> object:
    if value is not False:
        raise ValueError("value must be one of (False,)")
    return value


def _reject_explicit_null(value: object) -> object:
    if value is None:
        raise ValueError("explicit null is not allowed; omit the field instead")
    return value


class Parameters(BaseModel):
    """JSON Schema of the Tool's arguments: an object schema that lists its properties and its required property names, and allows no other property."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    type: Literal["object"]
    properties: Annotated[
        dict[StrictStr, Annotated[dict[StrictStr, json_value_module.JsonValue], Field(strict=True)]], Field(strict=True)
    ]
    required: Annotated[list[StrictStr], Field(strict=True)]
    additional_properties: Annotated[
        Annotated[StrictBool, AfterValidator(_validate_boolean_literal_1), Field(json_schema_extra={"const": False})],
        Field(alias="additionalProperties"),
    ]
    field_defs: Annotated[
        Union[
            Annotated[
                dict[StrictStr, Annotated[dict[StrictStr, json_value_module.JsonValue], Field(strict=True)]],
                Field(strict=True),
            ],
            SkipJsonSchema[None],
        ],
        BeforeValidator(_reject_explicit_null),
        Field(alias="$defs"),
        Field(
            description="Named subschemas that `$ref` values in this schema point to. Omit when the schema uses no `$ref`."
        ),
    ] = Field(default_factory=lambda: None, validate_default=False, exclude_if=lambda value: value is None)


class Tool(BaseModel):
    """Definition of a Tool that a model can call: its name, its purpose and the JSON Schema of its arguments. Offering a definition does not make the Tool available or authorize its call."""

    model_config = ConfigDict(extra="forbid", title="Tool", json_schema_extra=None)
    name: Annotated[
        tool_name_module.ToolName,
        Field(description="Tool name: 1 to 64 ASCII letters, digits, underscores or hyphens."),
    ]
    description: Annotated[
        Annotated[StrictStr, Field(min_length=1, max_length=1024)], Field(description="Tool description for the model")
    ]
    parameters: Parameters


RequireToolUse = TypeAliasType(
    "RequireToolUse",
    Annotated[
        StrictBool,
        Field(
            description="Require the response to contain at least one call to a Tool Definition carried on this request. Duale AI retries a response that has no Tool Call, on another model when needed, and fails the request when no model produces one.",
            json_schema_extra={"default": False},
        ),
    ],
)
