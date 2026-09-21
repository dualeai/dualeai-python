"""Auto-generated Pydantic models from the resolved schema compiler graph. Do not edit manually."""

from __future__ import annotations

from re import fullmatch
from typing import Annotated, Union

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
)
from typing_extensions import TypeAliasType

from dualeai.models import task_id as task_id_module

__all__: list[str] = ["TaskStop", "TaskStopAccepted", "TaskStopRequest"]
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


def _validate_string_constraints_2(value: object) -> object:
    if not isinstance(value, str):
        return value
    if fullmatch("\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d+)?(?:Z|[+-]\\d{2}:\\d{2})", value) is None:
        raise ValueError("string is not a canonical date-time")
    return value


TaskStop = TypeAliasType(
    "TaskStop",
    Annotated[
        _PortableJsonValue,
        Field(title="TaskStop", description="Contracts for requesting asynchronous Task Stop processing."),
    ],
)


class TaskStopAccepted(BaseModel):
    """Confirmation that a Stop request was accepted for asynchronous processing. Acceptance does not confirm that the target exists, is eligible, or will stop. If the Stop takes effect, its terminal task.stopped outcome arrives on the Task event stream."""

    model_config = ConfigDict(
        extra="forbid",
        title="TaskStopAccepted",
        json_schema_extra={
            "examples": [{"task_id": "task-12345678901234567890", "accepted_at": "2026-08-11T10:00:05Z"}]
        },
    )
    task_id: Annotated[task_id_module.TaskId, Field(description="Task named by the accepted Stop request.")]
    accepted_at: Annotated[
        Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_2)],
        Field(description="UTC timestamp when the Platform accepted the request for asynchronous processing."),
    ]


class TaskStopRequest(BaseModel):
    """Request asynchronous Stop processing for the public Task named by the request path."""

    model_config = ConfigDict(
        extra="forbid", title="TaskStopRequest", json_schema_extra={"examples": [{"reason": "Wrong document supplied"}]}
    )
    reason: Annotated[
        Annotated[StrictStr, Field(min_length=1, max_length=500, pattern="^[^\\u0000]*$")],
        Field(
            description="Why the caller requests a Stop. The text is returned only if the Stop takes effect.",
            examples=["Wrong document supplied", "Cost ceiling reached"],
        ),
    ]
