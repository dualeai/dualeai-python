"""Auto-generated Pydantic models from the resolved schema compiler graph. Do not edit manually."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from re import fullmatch
from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import (
    AwareDatetime,
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

from dualeai.models import agent_id as agent_id_module
from dualeai.models import attachment as attachment_module
from dualeai.models import json_value as json_value_module
from dualeai.models import priority_class as priority_class_module
from dualeai.models import problem_details as problem_details_module
from dualeai.models import response_format as response_format_module
from dualeai.models import routing_policy as routing_policy_module
from dualeai.models import task_id as task_id_module
from dualeai.models import tool as tool_module
from dualeai.models import tool_call_id as tool_call_id_module

__all__: list[str] = [
    "AgentDeregistrationMessage",
    "AgentHeartbeatMessage",
    "AgentHeartbeatResponse",
    "AgentRegistrationMessage",
    "Bridge",
    "BridgeContentDeltaResponse",
    "BridgeContentResetResponse",
    "BridgeSSEEvent",
    "BridgeTaskCompletedResponse",
    "BridgeTaskContinueRequest",
    "BridgeTaskCreateRequest",
    "BridgeTaskErrorResponse",
    "BridgeTaskResult",
    "BridgeTaskStoppedResponse",
    "BridgeToolResultError",
    "BridgeToolResultSuccess",
    "BridgeToolResultsRequest",
    "BridgeToolUseResponse",
    "HeartbeatStatus",
    "RegisteredTool",
    "TaskInstruction",
]
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
    if fullmatch("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", value) is None:
        raise ValueError("string is not a canonical uuid")
    return value


def _validate_string_constraints_3(value: object) -> object:
    if not isinstance(value, str):
        if not isinstance(value, datetime):
            raise ValueError("date-time input must be an RFC 3339 string or a datetime")
        return value
    if fullmatch("\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d+)?(?:Z|[+-]\\d{2}:\\d{2})", value) is None:
        raise ValueError("string is not a canonical date-time")
    return value


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


def _reject_explicit_null(value: object) -> object:
    if value is None:
        raise ValueError("explicit null is not allowed; omit the field instead")
    return value


Bridge = TypeAliasType(
    "Bridge",
    Annotated[
        _PortableJsonValue,
        Field(
            title="Bridge",
            description="Public HTTP and SSE contracts for creating and continuing Tasks, submitting Tool Results, and managing customer-hosted Tools for an Agent Identity.",
        ),
    ],
)


class AgentDeregistrationMessage(BaseModel):
    """Reports that one SDK process is shutting down. Its Tools stay available for a short time after it stops."""

    model_config = ConfigDict(extra="forbid", title="AgentDeregistrationMessage", json_schema_extra=None)
    agent_id: Annotated[
        agent_id_module.AgentId,
        Field(
            description="Agent Identity this lifecycle request acts on. The caller needs permission for this action on it."
        ),
    ]
    process_id: Annotated[
        Annotated[UUID, BeforeValidator(_validate_string_constraints_2)],
        Field(description="Identifier of this SDK process, the same on each of its requests."),
    ]
    deregistration_publication_id: Annotated[
        Annotated[UUID, BeforeValidator(_validate_string_constraints_2)],
        Field(description="Identifier of this shutdown notice. Reuse it when you retry the same notice."),
    ]
    reason: Annotated[
        Annotated[StrictStr, Field(min_length=1, max_length=100)],
        Field(description="Short reason why the SDK process is shutting down."),
    ]
    time: Annotated[
        Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_3)],
        Field(description="Time when this shutdown notice was created."),
    ]


class HeartbeatStatus(str, Enum):
    """Whether this SDK process can serve registered Tool Calls."""

    healthy = "healthy"
    degraded = "degraded"
    unhealthy = "unhealthy"

    @property
    def display_title(self) -> Union[str, None]:
        return {HeartbeatStatus.healthy: None, HeartbeatStatus.degraded: None, HeartbeatStatus.unhealthy: None}[self]

    @property
    def description(self) -> str:
        return {
            HeartbeatStatus.healthy: "Renews Tool availability.",
            HeartbeatStatus.degraded: "Renews Tool availability.",
            HeartbeatStatus.unhealthy: "Does not renew Tool availability.",
        }[self]

    @staticmethod
    def __get_pydantic_json_schema__(core_schema: CoreSchema, handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        json_schema = _EnumSchemaMetadata(
            (
                ("healthy", None, "Renews Tool availability."),
                ("degraded", None, "Renews Tool availability."),
                ("unhealthy", None, "Does not renew Tool availability."),
            )
        ).__get_pydantic_json_schema__(core_schema, handler)
        json_schema.update(
            {
                "description": "Whether this SDK process can serve registered Tool Calls.",
                "examples": ["healthy", "degraded", "unhealthy"],
            }
        )
        return json_schema


class AgentHeartbeatMessage(BaseModel):
    """Reports whether one SDK process can continue serving its registered Tools."""

    model_config = ConfigDict(extra="forbid", title="AgentHeartbeatMessage", json_schema_extra=None)
    agent_id: Annotated[
        agent_id_module.AgentId,
        Field(
            description="Agent Identity this lifecycle request acts on. The caller needs permission for this action on it."
        ),
    ]
    process_id: Annotated[
        Annotated[UUID, BeforeValidator(_validate_string_constraints_2)],
        Field(description="Identifier of this SDK process, the same on each of its requests."),
    ]
    status: Annotated[
        HeartbeatStatus,
        Field(
            description="Readiness reported for this SDK process. `healthy` and `degraded` renew Tool availability; `unhealthy` does not."
        ),
    ]
    time: Annotated[
        Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_3)],
        Field(description="Time when this heartbeat was created."),
    ]
    config_hash: Annotated[
        Annotated[StrictStr, Field(pattern="^[a-f0-9]{64}$")],
        Field(description="Fingerprint of the Tool set that this process last registered."),
    ]
    heartbeat_publication_id: Annotated[
        Annotated[UUID, BeforeValidator(_validate_string_constraints_2)],
        Field(description="Identifier of this heartbeat. Reuse it when you retry the same heartbeat."),
    ]


class AgentHeartbeatResponse(BaseModel):
    """Times at which Duale AI received and answered the heartbeat."""

    model_config = ConfigDict(extra="forbid", title="AgentHeartbeatResponse", json_schema_extra=None)
    server_received_at: Annotated[
        Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_3)],
        Field(description="Time when the Platform received the heartbeat request."),
    ]
    server_sent_at: Annotated[
        Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_3)],
        Field(description="Time when the Platform sent the heartbeat response."),
    ]
    client_sent_at: Annotated[
        Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_3)],
        Field(description="The heartbeat's `time`, returned unchanged."),
    ]


class RegisteredTool(BaseModel):
    """Registration entry for a customer-hosted Tool associated with an Agent Identity. `tool` describes the callable Tool, and `timeout_seconds` limits how long its result can be accepted."""

    model_config = ConfigDict(extra="forbid", title="RegisteredTool", json_schema_extra=None)
    tool: Annotated[
        tool_module.Tool,
        Field(
            description="Model-facing Tool Definition, including its name, purpose, and parameter contract. Duale AI reserves every top-level argument name that starts with `__`: a Tool that uses one stays registered but is never offered to a model."
        ),
    ]
    timeout_seconds: Annotated[
        Annotated[
            StrictFloat, Field(ge=-9007199254740991, le=9007199254740991, allow_inf_nan=False), Field(le=3600, gt=0)
        ],
        Field(
            description="Maximum time for one registered customer Tool Call to return its result. The SDK stops waiting at that time, but it cannot interrupt a synchronous callable that has already started."
        ),
    ]


class AgentRegistrationMessage(BaseModel):
    """Registers the complete set of Tools that one SDK process serves for its Agent Identity."""

    model_config = ConfigDict(extra="forbid", title="AgentRegistrationMessage", json_schema_extra=None)
    agent_id: Annotated[
        agent_id_module.AgentId,
        Field(
            description="Agent Identity this lifecycle request acts on. The caller needs permission for this action on it."
        ),
    ]
    process_id: Annotated[
        Annotated[UUID, BeforeValidator(_validate_string_constraints_2)],
        Field(description="Identifier of this SDK process, the same on each of its requests."),
    ]
    tools: Annotated[
        Annotated[list[RegisteredTool], Field(strict=True, max_length=4096)],
        Field(description="Complete set of customer Tool registrations served by this SDK process."),
    ]
    config_hash: Annotated[
        Annotated[StrictStr, Field(pattern="^[a-f0-9]{64}$")],
        Field(description="Fingerprint of the complete Tool set in this registration."),
    ]
    manifest_publication_id: Annotated[
        Annotated[UUID, BeforeValidator(_validate_string_constraints_2)],
        Field(description="Identifier of this registration. Reuse it when you retry the same registration."),
    ]
    time: Annotated[
        Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_3)],
        Field(description="Time when this registration was created."),
    ]
    sdk_version: Annotated[
        Annotated[StrictStr, Field(min_length=1, max_length=100)],
        Field(description="Version of the SDK that created this registration."),
    ]


class BridgeContentDeltaResponse(BaseModel):
    """SSE `content.delta` event carrying one fragment of the streamed answer text."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    type: Annotated[Literal["content.delta"], Field(description="Identifies this payload as a streamed content chunk.")]
    delta: Annotated[StrictStr, Field(description="Text fragment at this position of the streamed answer.")]
    generation: Annotated[
        Annotated[StrictInt, Field(ge=-9007199254740991, le=9007199254740991), Field(ge=1)],
        Field(
            description="Stream attempt that produced this fragment. A higher generation replaces every fragment from earlier generations; display only the highest generation received."
        ),
    ]
    sequence: Annotated[
        Annotated[StrictInt, Field(ge=-9007199254740991, le=9007199254740991), Field(ge=0)],
        Field(
            description="Position of this fragment within its generation. Events can arrive out of order; display fragments in ascending sequence."
        ),
    ]


class BridgeContentResetResponse(BaseModel):
    """Signals that a new stream attempt starts: discard every fragment from earlier generations."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    type: Annotated[
        Literal["content.reset"], Field(description="Identifies this payload as a streamed content replacement.")
    ]
    generation: Annotated[
        Annotated[StrictInt, Field(ge=-9007199254740991, le=9007199254740991), Field(ge=1)],
        Field(description="Stream attempt that starts with this replacement."),
    ]


class BridgeTaskResult(BaseModel):
    """Answer of a completed Task."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    completion: Annotated[
        Union[StrictStr, None],
        Field(description="Text answer of the Task. Null when the Task returns a structured answer."),
    ]
    validated_data: Annotated[
        Union[Annotated[dict[StrictStr, json_value_module.JsonValue], Field(strict=True)], None],
        Field(
            description="Structured answer that matches the Task's JSON Schema response format. Null when the Task returns a text answer."
        ),
    ]


class BridgeTaskCompletedResponse(BaseModel):
    """SSE `task.completed` event carrying the completed Task Result."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    type: Annotated[Literal["task.completed"], Field(description="Identifies this payload as Task completion.")]
    timestamp: Annotated[
        Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_3)],
        Field(description="Time when this event was emitted."),
    ]
    result: Annotated[BridgeTaskResult, Field(description="Completed Task Result.")]


class BridgeTaskErrorResponse(BaseModel):
    """SSE `task.error` event carrying structured Problem Details for a failed Task. Use the stable `error_code` for branching; optional fields state whether a retry may succeed, how long to wait, and whether owner action is required."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    type: Annotated[Literal["task.error"], Field(description="Identifies this payload as a Task failure.")]
    timestamp: Annotated[
        Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_3)],
        Field(description="Time when this event was emitted."),
    ]
    data: Annotated[
        problem_details_module.ProblemDetails,
        Field(
            description="Structured Problem Details for this failure, including a stable `error_code` and optional retry and owner-action guidance."
        ),
    ]


class BridgeTaskStoppedResponse(BaseModel):
    """SSE `task.stopped` event emitted when an accepted Stop takes effect. It replaces `task.completed` or `task.error` for that Task."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    type: Annotated[Literal["task.stopped"], Field(description="Identifies this payload as a stopped Task.")]
    timestamp: Annotated[
        Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_3)],
        Field(description="Time when this event was emitted."),
    ]
    reason: Annotated[
        Annotated[StrictStr, Field(min_length=1, max_length=500, pattern="^[^\\u0000]*$")],
        Field(description="Reason supplied with the stop request."),
    ]


class BridgeToolUseResponse(BaseModel):
    """SSE `tool.use` event requesting execution of a Tool."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    type: Annotated[Literal["tool.use"], Field(description="Identifies this payload as a Tool Call.")]
    timestamp: Annotated[
        Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_3)],
        Field(description="Time when this event was emitted."),
    ]
    tool_call_id: Annotated[
        tool_call_id_module.ToolCallId,
        Field(description="Identifier to return unchanged in the corresponding successful or failed Tool Result."),
    ]
    name: Annotated[StrictStr, Field(description="Name requested by the Tool Call.")]
    input: Annotated[
        Annotated[dict[StrictStr, json_value_module.JsonValue], Field(strict=True)],
        Field(description="Arguments carried by the Tool Call."),
    ]
    deadline_at: Annotated[
        Union[Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_3)], None],
        Field(
            description="Absolute deadline for returning the Tool Result for a registered customer Tool Call. `null` when this Tool Call is not routed to a registered customer Tool."
        ),
    ]


class BridgeSSEEvent(BaseModel):
    """SSE envelope for one Task event. The `data.type` value identifies the payload shape."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    id: Annotated[
        Annotated[StrictStr, Field(pattern="^[1-9][0-9]*:[1-9][0-9]*$")],
        Field(
            description="Opaque replay cursor for this event. To resume after it, send this exact value in the `Last-Event-ID` header."
        ),
    ]
    data: Annotated[
        Annotated[
            Union[
                BridgeContentDeltaResponse,
                BridgeContentResetResponse,
                BridgeToolUseResponse,
                BridgeTaskCompletedResponse,
                BridgeTaskErrorResponse,
                BridgeTaskStoppedResponse,
            ],
            Field(discriminator="type"),
        ],
        Field(description="Task event payload. Use its `type` value to distinguish the payload shape."),
    ]
    timestamp: Annotated[
        Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_3)],
        Field(description="Time when the SDK parsed this event."),
    ]


TaskInstruction = TypeAliasType(
    "TaskInstruction",
    Annotated[
        Annotated[StrictStr, Field(min_length=1, max_length=100000, pattern="^[^\\u0000]*$")],
        Field(
            title="TaskInstruction",
            description="Instruction a Task carries out, with the context it needs: non-empty text of at most 100,000 characters, without NUL characters. Treat user input, retrieved text, and document content in it as untrusted: none of them changes the Task's permissions.",
            examples=[
                "Extract key information from the document and summarize",
                "Analyze the provided data and identify trends",
                "Generate a comprehensive report based on the context",
            ],
        ),
    ],
)


class BridgeTaskContinueRequest(BaseModel):
    """Creates a new Task that continues the task history of `parent_task_id`, a Task of the same Agent Identity. The new Task runs under that Agent Identity and inherits that task history. A setting this request omits takes the parent Task's value; the deadline and the priority are always the new Task's own. The parent can have completed, failed, or stopped, or can still be running."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    type: Annotated[Literal["continue"], Field(description="Identifies this request as Task continuation.")]
    task_id: Annotated[
        task_id_module.TaskId,
        Field(
            description="Identifier of the new continuation Task, chosen by the caller. It must differ from `parent_task_id`. Read the continuation Task's events and stop it with this identifier. A repeated request with the same identifier starts no second Task."
        ),
    ]
    priority: Annotated[
        Union[priority_class_module.PriorityClass, SkipJsonSchema[None]],
        BeforeValidator(_reject_explicit_null),
        Field(
            description="Scheduling class for this continuation Task. Omission selects standard even when the parent used fast. Fast reduces waiting time for the same work and uses the Fast Task price.",
            json_schema_extra={"default": "standard"},
        ),
    ] = Field(default_factory=lambda: None, validate_default=False, exclude_if=lambda value: value is None)
    parent_task_id: Annotated[
        task_id_module.TaskId,
        Field(
            description="Task of the same Agent Identity whose task history this continuation Task continues. The parent can have completed, failed, or stopped, or can still be running; a running parent is continued from its last completed step. A continuation must name an immediate parent Task submitted less than 30 days ago. Task history leaves the inherited context 30 days after the request that supplied it; the continuation uses the history that remains."
        ),
    ]
    instruction: Annotated[
        TaskInstruction,
        Field(description="Instruction for the continuation Task. It follows the inherited task history."),
    ]
    attachments: Annotated[
        Union[Annotated[list[attachment_module.Attachment], Field(strict=True)], SkipJsonSchema[None]],
        BeforeValidator(_reject_explicit_null),
        Field(
            description="Documents to add for the continuation Task. Upload each document before submitting, then send the matching attachment key, filename, and description here. The parent Task's documents stay available.",
            json_schema_extra={"default": []},
        ),
    ] = Field(default_factory=lambda: None, validate_default=False, exclude_if=lambda value: value is None)
    routing_policy: Annotated[
        routing_policy_module.RoutingPolicy,
        Field(
            description="Model-selection preferences for this request. The object is required. A preference left unset takes the parent Task's value. Only the capabilities that the parent Task's request listed are inherited."
        ),
    ]
    response_format: Annotated[
        Union[response_format_module.ResponseFormat, SkipJsonSchema[None]],
        BeforeValidator(_reject_explicit_null),
        Field(
            description="Expected format of the continuation Task Result. When omitted, the continuation Task uses the parent Task's response format. Send the `text` format for a free-text answer after a structured parent."
        ),
    ] = Field(default_factory=lambda: None, validate_default=False, exclude_if=lambda value: value is None)
    response_stream: Annotated[
        Union[StrictBool, SkipJsonSchema[None]],
        BeforeValidator(_reject_explicit_null),
        Field(
            description="Stream the answer text while it is written. When omitted, the continuation Task streams if the parent Task did. Only free-text answers stream: no response format, or the `text` or `markdown` format. Streamed fragments can arrive out of order and can be replaced, so use them only for display and use the completed Task Result for processing."
        ),
    ] = Field(default_factory=lambda: None, validate_default=False, exclude_if=lambda value: value is None)
    deadline: Annotated[
        Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_3)],
        Field(
            description="Requested absolute deadline for the continuation Task, as a date-time with an offset (`Z` or `±hh:mm`). Near this time, the Task stops new work, such as Tool Calls, and answers from the work done so far. The deadline is best effort: the Task Outcome can arrive a few minutes after it.",
            examples=["2025-08-30T15:00:00Z", "2025-08-30T15:00:00.000Z"],
        ),
    ]


class BridgeTaskCreateRequest(BaseModel):
    """Creates a new Task with a task identifier that the developer selects. The `type` field is `create`."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    type: Annotated[Literal["create"], Field(description="Identifies this request as Task creation.")]
    task_id: Annotated[
        task_id_module.TaskId,
        Field(
            description="Identifier of the new Task, chosen by the caller. A Task identifier names a Task only within its tenant and Agent Identity. Read the Task's events and stop the Task with this identifier. A repeated request with the same identifier starts no second Task."
        ),
    ]
    priority: Annotated[
        Union[priority_class_module.PriorityClass, SkipJsonSchema[None]],
        BeforeValidator(_reject_explicit_null),
        Field(
            description="Scheduling class for this Task. Omission selects standard. Fast reduces waiting time for the same work and uses the Fast Task price.",
            json_schema_extra={"default": "standard"},
        ),
    ] = Field(default_factory=lambda: None, validate_default=False, exclude_if=lambda value: value is None)
    instruction: Annotated[TaskInstruction, Field(description="Instruction for the Task, with the context it needs.")]
    attachments: Annotated[
        Union[Annotated[list[attachment_module.Attachment], Field(strict=True)], SkipJsonSchema[None]],
        BeforeValidator(_reject_explicit_null),
        Field(
            description="Documents associated with this Task. Upload each document before submitting the Task, then send the matching attachment key, filename, and description here.",
            json_schema_extra={"default": []},
        ),
    ] = Field(default_factory=lambda: None, validate_default=False, exclude_if=lambda value: value is None)
    routing_policy: Annotated[
        routing_policy_module.RoutingPolicy,
        Field(
            description="Model-selection preferences for this request. The object is required and may be empty. Omitted fields remain unspecified in the request."
        ),
    ]
    response_format: Annotated[
        Union[response_format_module.ResponseFormat, None],
        Field(description="Expected format of a completed Task Result.", json_schema_extra={"default": None}),
    ] = None
    response_stream: Annotated[
        Union[StrictBool, SkipJsonSchema[None]],
        BeforeValidator(_reject_explicit_null),
        Field(
            description="Stream the answer text while it is written. Only free-text answers stream: no response format, or the `text` or `markdown` format. Streamed fragments can arrive out of order and can be replaced, so use them only for display and use the completed Task Result for processing.",
            json_schema_extra={"default": False},
        ),
    ] = Field(default_factory=lambda: None, validate_default=False, exclude_if=lambda value: value is None)
    deadline: Annotated[
        Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_3)],
        Field(
            description="Requested absolute deadline for the Task, as a date-time with an offset (`Z` or `±hh:mm`). Near this time, the Task stops new work, such as Tool Calls, and answers from the work done so far. The deadline is best effort: the Task Outcome can arrive a few minutes after it.",
            examples=["2025-08-30T15:00:00Z", "2025-08-30T15:00:00.000Z"],
        ),
    ]


class BridgeToolResultError(BaseModel):
    """Failed result for one Tool Call."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    type: Annotated[Literal["error"], Field(description="Identifies this Tool Result as failed.")]
    tool_call_id: Annotated[
        tool_call_id_module.ToolCallId,
        Field(description="Identifier of the Tool Call that produced this result. Match it within the owning Task."),
    ]
    message: Annotated[
        Annotated[StrictStr, Field(max_length=2048)],
        Field(description="Description of why customer-hosted Tool execution failed."),
    ]


class BridgeToolResultSuccess(BaseModel):
    """Successful result for one Tool Call."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    type: Annotated[Literal["success"], Field(description="Identifies this Tool Result as successful.")]
    tool_call_id: Annotated[
        tool_call_id_module.ToolCallId,
        Field(description="Identifier of the Tool Call that produced this result. Match it within the owning Task."),
    ]
    output: Annotated[
        Union[Annotated[dict[StrictStr, json_value_module.JsonValue], Field(strict=True)], StrictStr],
        Field(description="Value returned by the customer-hosted Tool."),
    ]


class BridgeToolResultsRequest(BaseModel):
    """Submits Tool Results to the Task whose `tool.use` events requested them. The Task then continues. The `type` field is `tool_results`."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    type: Annotated[Literal["tool_results"], Field(description="Identifies this request as Tool Result submission.")]
    task_id: Annotated[
        task_id_module.TaskId, Field(description="Task whose `tool.use` events these Tool Results answer.")
    ]
    tool_results: Annotated[
        Annotated[
            list[
                Annotated[
                    Union[BridgeToolResultSuccess, BridgeToolResultError],
                    Field(description="Tool Result, either success or error."),
                ]
            ],
            Field(strict=True, min_length=1),
        ],
        Field(
            description="One or more Tool Results to submit. `tool_call_id` values must be distinct within the batch; duplicate values are rejected."
        ),
    ]
