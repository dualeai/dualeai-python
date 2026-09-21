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
    """JSON Schema for tool parameters"""

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
            description="Reusable subschema definitions referenced via $ref (faithful schemas). Optional; absent for flat/inlined schemas."
        ),
    ] = Field(default_factory=lambda: None, validate_default=False, exclude_if=lambda value: value is None)


class Tool(BaseModel):
    """Model-facing Tool Definition for requesting a typed operation. It describes the name, purpose, and arguments; it grants no dispatch eligibility, host availability, Platform access authorization, or external-action authority."""

    model_config = ConfigDict(extra="forbid", title="Tool", json_schema_extra=None)
    name: Annotated[
        tool_name_module.ToolName, Field(description="Tool name (letters, numbers, underscores, hyphens only)")
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
            description="Ask the model to call at least one of the Tool Definitions carried on this request. Providers apply it through their own tool-selection control and a few omit it in modes that cannot express it — extended thinking on Anthropic and Bedrock, for two — so it constrains the request rather than guaranteeing the outcome.",
            json_schema_extra={"default": False},
        ),
    ],
)
