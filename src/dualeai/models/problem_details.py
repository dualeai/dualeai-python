"""Auto-generated Pydantic models from the resolved schema compiler graph. Do not edit manually."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Union

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    GetJsonSchemaHandler,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
)
from pydantic.json_schema import JsonSchemaValue, SkipJsonSchema
from pydantic_core import CoreSchema
from typing_extensions import TypeAliasType

from dualeai.models import problem_error as problem_error_module

__all__: list[str] = ["ErrorCategory", "ProblemDetails"]
_PortableJsonValue = TypeAliasType(
    "_PortableJsonValue",
    Union[
        StrictBool,
        Annotated[StrictInt, Field(ge=-9007199254740991, le=9007199254740991)],
        Annotated[StrictFloat, Field(ge=-9007199254740991, le=9007199254740991, allow_inf_nan=False)],
        StrictStr,
        Annotated[list["_PortableJsonValue"], Field(strict=True)],
        Annotated[dict[StrictStr, "_PortableJsonValue"], Field(strict=True)],
        None,
    ],
)


def _reject_explicit_null(value: object) -> object:
    if value is None:
        raise ValueError("explicit null is not allowed; omit the field instead")
    return value


class ErrorCategory(str, Enum):
    """Broad category for routing logic — drives operator dashboards and SDK retry decisions. RFC 9457 §3.2 envelope extension."""

    transient = "transient"
    upstream = "upstream"
    integrity = "integrity"
    config = "config"

    @staticmethod
    def __get_pydantic_json_schema__(core_schema: CoreSchema, handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        json_schema = handler.resolve_ref_schema(handler(core_schema))
        json_schema.update(
            {
                "description": "Broad category for routing logic — drives operator dashboards and SDK retry decisions. RFC 9457 §3.2 envelope extension."
            }
        )
        return json_schema


class ProblemDetails(BaseModel):
    """RFC 9457 Problem Details for HTTP APIs and NATS-bound SDK error wire format. Standard error shape across HTTP responses and NATS task error events."""

    model_config = ConfigDict(extra="allow", title="ProblemDetails", json_schema_extra=None)
    __pydantic_extra__: dict[str, _PortableJsonValue] = Field(init=False)
    type: Annotated[
        Union[StrictStr, SkipJsonSchema[None]],
        BeforeValidator(_reject_explicit_null),
        Field(
            description="URI identifying problem type (RFC 9457). Use 'about:blank' when no documentation exists.",
            json_schema_extra={"default": "about:blank"},
        ),
    ] = Field(default_factory=lambda: None, validate_default=False, exclude_if=lambda value: value is None)
    title: Annotated[
        Annotated[StrictStr, Field(max_length=200)],
        Field(description="Stable human-readable summary of the error type (must not change between occurrences)"),
    ]
    status: Annotated[
        Annotated[StrictInt, Field(ge=-9007199254740991, le=9007199254740991), Field(ge=100, le=599)],
        Field(description="HTTP-style status code (used for severity classification on NATS channel too)"),
    ]
    detail: Annotated[
        Annotated[StrictStr, Field(max_length=1000)],
        Field(description="Human-readable explanation specific to this occurrence"),
    ]
    instance: Annotated[
        Union[StrictStr, None],
        Field(
            description="URI or path identifying this specific occurrence (typically request path or resource ID)",
            json_schema_extra={"default": None},
        ),
    ] = None
    error_code: Annotated[
        Annotated[StrictStr, Field(pattern="^[A-Z][A-Z0-9_]*$")],
        Field(
            description="Machine-readable error code (e.g. RENDERER_CASCADE_TIMEOUT, BILLING_LIMIT_EXCEEDED). SDK consumers branch on this.",
            examples=[
                "TASK_DEADLINE_EXCEEDED",
                "BILLING_LIMIT_EXCEEDED",
                "TOOL_VALIDATION_FAILED",
                "AUTHORIZATION_FAILED",
                "RESOURCE_NOT_FOUND",
                "INTERNAL_ROUTING",
            ],
        ),
    ]
    errors: Annotated[
        Union[Annotated[list[problem_error_module.ProblemError], Field(strict=True)], SkipJsonSchema[None]],
        BeforeValidator(_reject_explicit_null),
        Field(
            description="Detailed error breakdown. Each entry adds context (field path, ai_hints).",
            json_schema_extra={"default": []},
        ),
    ] = Field(default_factory=lambda: None, validate_default=False, exclude_if=lambda value: value is None)
    retryable: Annotated[
        Union[StrictBool, None],
        Field(
            description="Whether retrying this operation may succeed. RFC 9457 §3.2 extension at envelope level per Cloudflare Mar 2026 convention.",
            json_schema_extra={"default": None},
        ),
    ] = None
    retry_after_seconds: Annotated[
        Union[Annotated[StrictInt, Field(ge=-9007199254740991, le=9007199254740991), Field(ge=0, le=3600)], None],
        Field(
            description="Seconds to wait before retry. RFC 9457 §3.2 envelope extension.",
            json_schema_extra={"default": None},
        ),
    ] = None
    owner_action_required: Annotated[
        Union[StrictBool, None],
        Field(
            description="True if a human operator or tenant admin must intervene. RFC 9457 §3.2 envelope extension.",
            json_schema_extra={"default": None},
        ),
    ] = None
    error_category: Annotated[
        Union[ErrorCategory, None],
        Field(
            description="Broad category for routing logic — drives operator dashboards and SDK retry decisions. RFC 9457 §3.2 envelope extension.",
            json_schema_extra={"default": None},
        ),
    ] = None
    request_id: Annotated[
        Union[StrictStr, None], Field(description="Request ID for tracing", json_schema_extra={"default": None})
    ] = None
    trace_id: Annotated[
        Union[StrictStr, None], Field(description="OpenTelemetry trace ID", json_schema_extra={"default": None})
    ] = None
    span_id: Annotated[
        Union[StrictStr, None], Field(description="OpenTelemetry span ID", json_schema_extra={"default": None})
    ] = None
