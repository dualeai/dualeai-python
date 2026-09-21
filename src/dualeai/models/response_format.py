"""Auto-generated Pydantic models from the resolved schema compiler graph. Do not edit manually."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Union

from pydantic import BaseModel, ConfigDict, Field, GetJsonSchemaHandler, StrictStr
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema
from typing_extensions import TypeAliasType

from dualeai.models import json_value as json_value_module

__all__: list[str] = ["JsonSchemaResponseFormat", "PredefinedResponseFormat", "ResponseFormat"]


class PredefinedResponseFormat(str, Enum):
    """Predefined format types for common response structures"""

    text = "text"
    json = "json"
    markdown = "markdown"
    html = "html"
    xml = "xml"
    yaml = "yaml"
    csv = "csv"

    @staticmethod
    def __get_pydantic_json_schema__(core_schema: CoreSchema, handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        json_schema = handler.resolve_ref_schema(handler(core_schema))
        json_schema.update({"description": "Predefined format types for common response structures"})
        return json_schema


class JsonSchemaResponseFormat(BaseModel):
    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    json_schema: Annotated[
        Annotated[dict[StrictStr, json_value_module.JsonValue], Field(strict=True)],
        Field(
            description="JSON Schema for structured response validation",
            examples=[
                {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "age": {"type": "integer", "minimum": 0}},
                    "required": ["name", "age"],
                }
            ],
        ),
    ]
    description: Annotated[
        Union[Annotated[StrictStr, Field(max_length=500)], None],
        Field(description="Human-readable description of the expected format, or null when none is supplied."),
    ] = None


ResponseFormat = TypeAliasType(
    "ResponseFormat",
    Annotated[
        Union["PredefinedResponseFormat", "JsonSchemaResponseFormat"],
        Field(
            title="ResponseFormat",
            description="Response format specification for Task Results.",
            examples=[
                "json",
                "markdown",
                {
                    "json_schema": {
                        "type": "object",
                        "properties": {
                            "invoice_number": {"type": "string"},
                            "total": {"type": "number"},
                            "items": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {"description": {"type": "string"}, "amount": {"type": "number"}},
                                },
                            },
                        },
                        "required": ["invoice_number", "total"],
                    },
                    "description": "Invoice data extraction format",
                },
            ],
        ),
    ],
)
