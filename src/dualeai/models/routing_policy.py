"""Auto-generated Pydantic models from the resolved schema compiler graph. Do not edit manually."""

from __future__ import annotations

from typing import Annotated, Union

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictFloat, StrictInt
from pydantic.json_schema import SkipJsonSchema

from dualeai.models import skill_enum as skill_enum_module

__all__: list[str] = ["RoutingPolicy"]


def _reject_explicit_null(value: object) -> object:
    if value is None:
        raise ValueError("explicit null is not allowed; omit the field instead")
    return value


class RoutingPolicy(BaseModel):
    """Task-scoped soft preferences used to rank eligible Model Routes. They do not make a route eligible or exclude an otherwise eligible route."""

    model_config = ConfigDict(
        extra="forbid",
        title="RoutingPolicy",
        json_schema_extra={
            "examples": [
                {"target_accuracy": 0.9},
                {
                    "target_accuracy": 0.85,
                    "cost_sensitivity": 0.2,
                    "speed_preference": 0.7,
                    "priority_level": 5,
                    "required_skills": ["code", "analysis"],
                },
                {
                    "target_accuracy": 0.95,
                    "cost_sensitivity": 0.9,
                    "priority_level": 0,
                    "required_skills": ["reasoning", "analysis"],
                    "preferred_skills": ["agentic", "long_context"],
                },
            ]
        },
    )
    target_accuracy: Annotated[
        Union[
            Annotated[
                StrictFloat,
                Field(ge=-9007199254740991, le=9007199254740991, allow_inf_nan=False),
                Field(ge=0.0, le=1.0),
            ],
            None,
        ],
        Field(
            description="Desired emphasis on differences in model capability. 0.0 and null apply no extra emphasis, while values toward 1.0 increasingly favor the highest available capability. Use higher values when mistakes carry greater business cost.",
            examples=[0.7, 0.85, 0.95, 1.0, None],
            json_schema_extra={"default": None},
        ),
    ] = None
    target_permissiveness: Annotated[
        Union[
            Annotated[
                StrictFloat,
                Field(ge=-9007199254740991, le=9007199254740991, allow_inf_nan=False),
                Field(ge=0.0, le=1.0),
            ],
            None,
        ],
        Field(
            description="Desired willingness to answer sensitive but allowed requests. 0.0 favors models that more often withhold or qualify answers, 0.5 balances withholding and directness, and 1.0 favors direct engagement. This preference does not measure protection against harmful requests and is not a safety guardrail. null applies no specific permissiveness preference.",
            examples=[0.2, 0.5, 0.8, None],
            json_schema_extra={"default": None},
        ),
    ] = None
    cost_sensitivity: Annotated[
        Union[
            Annotated[
                StrictFloat,
                Field(ge=-9007199254740991, le=9007199254740991, allow_inf_nan=False),
                Field(ge=0.0, le=1.0),
            ],
            None,
        ],
        Field(
            description="Importance of lower estimated request cost. 0.0 and null add no general preference between base model tariffs, while values toward 1.0 increasingly favor cheaper routes and may trade model capability for a large price reduction. The router still accounts for known provider prompt-cache savings and exact response-cache hits at every value. This is a soft routing preference, not a spending limit.",
            examples=[0.0, 0.2, 0.5, 0.8, 1.0, None],
            json_schema_extra={"default": None},
        ),
    ] = None
    speed_preference: Annotated[
        Union[
            Annotated[
                StrictFloat,
                Field(ge=-9007199254740991, le=9007199254740991, allow_inf_nan=False),
                Field(ge=0.0, le=1.0),
            ],
            None,
        ],
        Field(
            description="Desired emphasis on faster responses. 0.0 gives measured latency no preference, 0.5 applies the normal balance, and 1.0 gives it the strongest preference. null applies the normal balance.",
            examples=[0.2, 0.5, 0.7, 0.9, None],
            json_schema_extra={"default": None},
        ),
    ] = None
    priority_level: Annotated[
        Union[
            Annotated[StrictInt, Field(ge=-9007199254740991, le=9007199254740991), Field(ge=-10, le=10)],
            SkipJsonSchema[None],
        ],
        BeforeValidator(_reject_explicit_null),
        Field(
            description="Task priority from -10 to 10. Lower values reduce the preference for lower measured latency, 0 applies the normal balance, and higher values strengthen it. Cost preference is controlled separately by cost_sensitivity.",
            json_schema_extra={"default": 0},
        ),
    ] = Field(default_factory=lambda: None, validate_default=False, exclude_if=lambda value: value is None)
    required_skills: Annotated[
        Union[Annotated[list[skill_enum_module.SkillEnum], Field(strict=True)], SkipJsonSchema[None]],
        BeforeValidator(_reject_explicit_null),
        Field(
            description="Skills the selected model should possess. Routing prefers models with evidence for every listed skill and falls back to the best available route when the full request cannot be satisfied.",
            json_schema_extra={"default": []},
        ),
    ] = Field(default_factory=lambda: None, validate_default=False, exclude_if=lambda value: value is None)
    preferred_skills: Annotated[
        Union[Annotated[list[skill_enum_module.SkillEnum], Field(strict=True)], SkipJsonSchema[None]],
        BeforeValidator(_reject_explicit_null),
        Field(
            description="Skills that improve model preference but do not exclude other available routes.",
            json_schema_extra={"default": []},
        ),
    ] = Field(default_factory=lambda: None, validate_default=False, exclude_if=lambda value: value is None)
