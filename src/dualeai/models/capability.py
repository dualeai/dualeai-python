"""Auto-generated Pydantic models from the resolved schema compiler graph. Do not edit manually."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Union

from pydantic import BaseModel, ConfigDict, Field, GetJsonSchemaHandler, StrictFloat
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema
from typing_extensions import TypeAliasType

__all__: list[str] = [
    "Capability",
    "CapabilityComparison",
    "CapabilityQualification",
    "CapabilityScore",
    "GeneralCapabilityComparison",
    "GeneralCapabilityScore",
    "TaskCapabilityScore",
]


@dataclass(frozen=True)
class _EnumSchemaMetadata:
    choices: tuple[tuple[str, Union[str, None], Union[str, None]], ...]

    def __get_pydantic_json_schema__(self, core_schema: CoreSchema, handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        json_schema = handler.resolve_ref_schema(handler(core_schema)).copy()
        json_schema.pop("enum", None)
        json_schema.pop("const", None)
        branches: list[JsonSchemaValue] = []
        for value, title, description in self.choices:
            branch: JsonSchemaValue = {"const": value}
            if title is not None:
                branch["title"] = title
            if description is not None:
                branch["description"] = description
            branches.append(branch)
        json_schema["oneOf"] = branches
        return json_schema


class Capability(str, Enum):
    """Work capacity a task requires of whoever executes it, used to rank eligible routes"""

    problem_solving = "problem_solving"
    math = "math"
    code = "code"
    agentic = "agentic"
    long_context = "long_context"
    finance = "finance"
    cyber_defensive = "cyber_defensive"
    cyber_offensive = "cyber_offensive"
    medical = "medical"
    legal = "legal"

    @property
    def display_title(self) -> str:
        return {
            Capability.problem_solving: "Problem solving",
            Capability.math: "Math",
            Capability.code: "Code",
            Capability.agentic: "Agentic",
            Capability.long_context: "Long context",
            Capability.finance: "Finance",
            Capability.cyber_defensive: "Defensive security",
            Capability.cyber_offensive: "Offensive security",
            Capability.medical: "Medical",
            Capability.legal: "Legal",
        }[self]

    @property
    def description(self) -> str:
        return {
            Capability.problem_solving: "Deriving conclusions through several logical or scientific steps.",
            Capability.math: "Mathematical problem solving and formal proofs.",
            Capability.code: "Generating, inspecting, modifying, debugging, or migrating software.",
            Capability.agentic: "Completing multiple steps with available Tools and feedback.",
            Capability.long_context: "Retrieving and combining details across very long documents or message histories.",
            Capability.finance: "Financial analysis, modeling, and banking workflows.",
            Capability.cyber_defensive: "Reproducing and remediating software vulnerabilities.",
            Capability.cyber_offensive: "Assessing whether a vulnerability can be exploited within an authorized scope.",
            Capability.medical: "Medical work; task-performance evidence focuses on clinical documentation.",
            Capability.legal: "Legal analysis and reviewable legal documents.",
        }[self]

    @staticmethod
    def __get_pydantic_json_schema__(core_schema: CoreSchema, handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        json_schema = _EnumSchemaMetadata(
            (
                (
                    "problem_solving",
                    "Problem solving",
                    "Deriving conclusions through several logical or scientific steps.",
                ),
                ("math", "Math", "Mathematical problem solving and formal proofs."),
                ("code", "Code", "Generating, inspecting, modifying, debugging, or migrating software."),
                ("agentic", "Agentic", "Completing multiple steps with available Tools and feedback."),
                (
                    "long_context",
                    "Long context",
                    "Retrieving and combining details across very long documents or message histories.",
                ),
                ("finance", "Finance", "Financial analysis, modeling, and banking workflows."),
                ("cyber_defensive", "Defensive security", "Reproducing and remediating software vulnerabilities."),
                (
                    "cyber_offensive",
                    "Offensive security",
                    "Assessing whether a vulnerability can be exploited within an authorized scope.",
                ),
                ("medical", "Medical", "Medical work; task-performance evidence focuses on clinical documentation."),
                ("legal", "Legal", "Legal analysis and reviewable legal documents."),
            )
        ).__get_pydantic_json_schema__(core_schema, handler)
        json_schema.update(
            {
                "title": "Capability",
                "description": "Work capacity a task requires of whoever executes it, used to rank eligible routes",
                "examples": ["code", "problem_solving", "long_context"],
            }
        )
        return json_schema


class GeneralCapabilityComparison(str, Enum):
    general = "general"

    @property
    def display_title(self) -> str:
        return {GeneralCapabilityComparison.general: "General"}[self]

    @property
    def description(self) -> str:
        return {
            GeneralCapabilityComparison.general: "Broad language and knowledge work without one dominant specialization."
        }[self]

    @staticmethod
    def __get_pydantic_json_schema__(core_schema: CoreSchema, handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        json_schema = _EnumSchemaMetadata(
            (("general", "General", "Broad language and knowledge work without one dominant specialization."),)
        ).__get_pydantic_json_schema__(core_schema, handler)
        json_schema.update({"title": "GeneralCapabilityComparison"})
        return json_schema


CapabilityComparison = TypeAliasType(
    "CapabilityComparison",
    Annotated[Union["Capability", "GeneralCapabilityComparison"], Field(title="CapabilityComparison")],
)


class CapabilityQualification(str, Enum):
    """Whether a route has a task capability, judged from its benchmark evidence against the other enabled routes."""

    yes = "yes"
    undetermined = "undetermined"
    no = "no"

    @property
    def display_title(self) -> str:
        return {
            CapabilityQualification.yes: "Yes",
            CapabilityQualification.undetermined: "Undetermined",
            CapabilityQualification.no: "No",
        }[self]

    @property
    def description(self) -> str:
        return {
            CapabilityQualification.yes: "The route's benchmark evidence places it among the stronger of the enabled routes for this capability. When the capability is required, routing prefers this route.",
            CapabilityQualification.undetermined: "The route is measured, but its measurements cannot place it against enough of the other routes to decide. When the capability is required, routing prefers this route to one without the capability, but not to one that has it.",
            CapabilityQualification.no: "The route's benchmark evidence places it outside the stronger of the enabled routes for this capability, or the route has no measurement for it.",
        }[self]

    @staticmethod
    def __get_pydantic_json_schema__(core_schema: CoreSchema, handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        json_schema = _EnumSchemaMetadata(
            (
                (
                    "yes",
                    "Yes",
                    "The route's benchmark evidence places it among the stronger of the enabled routes for this capability. When the capability is required, routing prefers this route.",
                ),
                (
                    "undetermined",
                    "Undetermined",
                    "The route is measured, but its measurements cannot place it against enough of the other routes to decide. When the capability is required, routing prefers this route to one without the capability, but not to one that has it.",
                ),
                (
                    "no",
                    "No",
                    "The route's benchmark evidence places it outside the stronger of the enabled routes for this capability, or the route has no measurement for it.",
                ),
            )
        ).__get_pydantic_json_schema__(core_schema, handler)
        json_schema.update(
            {
                "title": "CapabilityQualification",
                "description": "Whether a route has a task capability, judged from its benchmark evidence against the other enabled routes.",
            }
        )
        return json_schema


class GeneralCapabilityScore(BaseModel):
    """A route's general strength compared with the complete enabled model pool."""

    model_config = ConfigDict(extra="forbid", title="GeneralCapabilityScore", json_schema_extra=None)
    capability: Annotated[GeneralCapabilityComparison, Field(description="General summarizes overall capability.")]
    strength: Annotated[
        Union[
            Annotated[
                StrictFloat, Field(ge=-9007199254740991, le=9007199254740991, allow_inf_nan=False), Field(ge=0, le=100)
            ],
            None,
        ],
        Field(
            description="Strength for this capability compared with the other enabled routes measured for it: 100 for the strongest, 0 for the weakest, and 50 for every route when all of them tie. Null when the route has no measurement for the capability or when fewer than two enabled routes are measured. A strength depends on the configured pool and is neither a task-success probability nor a routing-selection probability."
        ),
    ]
    qualification: Annotated[
        None, Field(description="Null because general is a comparison scope that a request cannot require.")
    ]


class TaskCapabilityScore(BaseModel):
    """A route's strength and qualification for a task capability compared with the complete enabled model pool."""

    model_config = ConfigDict(extra="forbid", title="TaskCapabilityScore", json_schema_extra=None)
    capability: Annotated[Capability, Field(description="Task capability described by this score.")]
    strength: Annotated[
        Union[
            Annotated[
                StrictFloat, Field(ge=-9007199254740991, le=9007199254740991, allow_inf_nan=False), Field(ge=0, le=100)
            ],
            None,
        ],
        Field(
            description="Strength for this capability compared with the other enabled routes measured for it: 100 for the strongest, 0 for the weakest, and 50 for every route when all of them tie. Null when the route has no measurement for the capability or when fewer than two enabled routes are measured. A strength depends on the configured pool and is neither a task-success probability nor a routing-selection probability."
        ),
    ]
    qualification: Annotated[
        CapabilityQualification,
        Field(
            description="Whether the route has this capability compared with the other enabled routes. A request that only some routes can take, such as one with images, is judged against those routes."
        ),
    ]


CapabilityScore = TypeAliasType(
    "CapabilityScore",
    Annotated[
        Union["GeneralCapabilityScore", "TaskCapabilityScore"],
        Field(
            title="CapabilityScore",
            description="A route's strength and qualification for one capability, from the saved benchmark measurements of the complete enabled model pool.",
        ),
    ],
)
