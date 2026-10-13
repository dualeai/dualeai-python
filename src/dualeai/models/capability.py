"""Auto-generated Pydantic models from the resolved schema compiler graph. Do not edit manually."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Union

from pydantic import BaseModel, ConfigDict, Field, GetJsonSchemaHandler, StrictFloat
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema

__all__: list[str] = ["Capability", "CapabilityScore"]


class Capability(str, Enum):
    """Work capacity a task requires of whoever executes it, used to rank eligible routes"""

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

    @property
    def description(self) -> str:
        return {
            Capability.general: "Broad language and knowledge work without one dominant specialization.",
            Capability.reasoning: "Deriving conclusions through several logical or scientific steps.",
            Capability.analysis: "Interpreting, comparing, and synthesizing supplied information.",
            Capability.math: "Mathematical problem solving and formal proofs.",
            Capability.code: "Generating, inspecting, modifying, debugging, or migrating software.",
            Capability.instruction_following: "Following detailed instructions and output rubrics.",
            Capability.agentic: "Completing multiple steps with available Tools and feedback.",
            Capability.long_context: "Using information spread across extensive supplied material.",
            Capability.finance: "Financial analysis, modeling, and banking workflows.",
            Capability.cyber_defensive: "Reproducing and remediating software vulnerabilities.",
            Capability.cyber_offensive: "Assessing whether a vulnerability can be exploited within an authorized scope.",
            Capability.medical: "Medical work; task-performance evidence focuses on clinical documentation.",
            Capability.legal: "Legal analysis and reviewable legal documents.",
        }[self]

    @staticmethod
    def __get_pydantic_json_schema__(core_schema: CoreSchema, handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        json_schema = handler.resolve_ref_schema(handler(core_schema))
        json_schema.update(
            {
                "title": "Capability",
                "description": "Work capacity a task requires of whoever executes it, used to rank eligible routes",
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


class CapabilityScore(BaseModel):
    """A capability percentage derived from the saved benchmark measurements of the complete enabled model pool."""

    model_config = ConfigDict(extra="forbid", title="CapabilityScore", json_schema_extra=None)
    capability: Annotated[
        Capability, Field(description="Capability described by this score; general summarizes overall capability.")
    ]
    value: Annotated[
        Union[
            Annotated[
                StrictFloat, Field(ge=-9007199254740991, le=9007199254740991, allow_inf_nan=False), Field(ge=0, le=100)
            ],
            None,
        ],
        Field(
            description="Capability score as a percentage. Null means usable benchmark evidence is absent; zero is a known score. Scores depend on the configured pool and are neither task-success probabilities nor routing-selection probabilities."
        ),
    ]
