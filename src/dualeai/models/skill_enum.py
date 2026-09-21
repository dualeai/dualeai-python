"""Auto-generated Pydantic models from the resolved schema compiler graph. Do not edit manually."""

from __future__ import annotations

from enum import Enum

from pydantic import GetJsonSchemaHandler
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema

__all__: list[str] = ["SkillEnum"]


class SkillEnum(str, Enum):
    """Enumeration of available skills for routing policy"""

    general = "general"
    reasoning = "reasoning"
    analysis = "analysis"
    math = "math"
    code = "code"
    instruction_following = "instruction_following"
    agentic = "agentic"
    long_context = "long_context"
    finance = "finance"
    cyber_defensive = "cyber_defensive"
    cyber_offensive = "cyber_offensive"
    medical = "medical"
    legal = "legal"

    @staticmethod
    def __get_pydantic_json_schema__(core_schema: CoreSchema, handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        json_schema = handler.resolve_ref_schema(handler(core_schema))
        json_schema.update(
            {
                "title": "SkillEnum",
                "description": "Enumeration of available skills for routing policy",
                "examples": [
                    "code",
                    "analysis",
                    "math",
                    "reasoning",
                    "agentic",
                    "finance",
                    "cyber_defensive",
                    "cyber_offensive",
                    "medical",
                    "legal",
                ],
            }
        )
        return json_schema
