"""Auto-generated Pydantic models from the resolved schema compiler graph. Do not edit manually."""

from __future__ import annotations

from enum import Enum

from pydantic import GetJsonSchemaHandler
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema

__all__: list[str] = ["PriorityClass"]


class PriorityClass(str, Enum):
    """Task scheduling class. Standard favors throughput; fast favors lower waiting time for the same work."""

    standard = "standard"
    fast = "fast"

    @staticmethod
    def __get_pydantic_json_schema__(core_schema: CoreSchema, handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        json_schema = handler.resolve_ref_schema(handler(core_schema))
        json_schema.update(
            {
                "title": "PriorityClass",
                "description": "Task scheduling class. Standard favors throughput; fast favors lower waiting time for the same work.",
            }
        )
        return json_schema
