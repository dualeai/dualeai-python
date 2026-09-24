"""Auto-generated Pydantic models from the resolved schema compiler graph. Do not edit manually."""

from __future__ import annotations

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

from dualeai.models import action_prompt as action_prompt_module
from dualeai.models import agent_id as agent_id_module
from dualeai.models import json_value as json_value_module
from dualeai.models import llm_result as llm_result_module
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
    "BridgeTaskStoppedResponse",
    "BridgeToolResultError",
    "BridgeToolResultSuccess",
    "BridgeToolResultsRequest",
    "BridgeToolUseResponse",
    "HeartbeatStatus",
    "RegisteredTool",
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
        return value
    if fullmatch("\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d+)?(?:Z|[+-]\\d{2}:\\d{2})", value) is None:
        raise ValueError("string is not a canonical date-time")
    return value


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
    """Best-effort request to report that one SDK process is shutting down. Acceptance does not immediately withdraw the process's registered Tools; their availability expires when serving heartbeats stop."""

    model_config = ConfigDict(extra="forbid", title="AgentDeregistrationMessage", json_schema_extra=None)
    agent_id: Annotated[
        agent_id_module.AgentId,
        Field(
            description="Agent Identity for this lifecycle request. It must match the identity bound to the SDK token."
        ),
    ]
    process_id: Annotated[
        Annotated[UUID, BeforeValidator(_validate_string_constraints_2)],
        Field(
            description="UUID version 7 generated once for this SDK process and reused across its lifecycle requests."
        ),
    ]
    deregistration_publication_id: Annotated[
        Annotated[UUID, BeforeValidator(_validate_string_constraints_2)],
        Field(
            description="UUID version 7 that identifies this shutdown notification. Reuse it when retrying the same notification."
        ),
    ]
    reason: Annotated[
        Annotated[StrictStr, Field(min_length=1, max_length=100)],
        Field(description="Short reason why the SDK process is shutting down."),
    ]
    time: Annotated[
        Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_3)],
        Field(description="SDK estimate of server time when this shutdown notification was created."),
    ]


class HeartbeatStatus(str, Enum):
    """Whether this SDK process can serve registered Tool Calls. `healthy` and `degraded` renew Tool availability; `unhealthy` does not."""

    healthy = "healthy"
    degraded = "degraded"
    unhealthy = "unhealthy"

    @staticmethod
    def __get_pydantic_json_schema__(core_schema: CoreSchema, handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        json_schema = handler.resolve_ref_schema(handler(core_schema))
        json_schema.update(
            {
                "description": "Whether this SDK process can serve registered Tool Calls. `healthy` and `degraded` renew Tool availability; `unhealthy` does not.",
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
            description="Agent Identity for this lifecycle request. It must match the identity bound to the SDK token."
        ),
    ]
    process_id: Annotated[
        Annotated[UUID, BeforeValidator(_validate_string_constraints_2)],
        Field(
            description="UUID version 7 generated once for this SDK process and reused across its lifecycle requests."
        ),
    ]
    status: Annotated[
        HeartbeatStatus,
        Field(
            description="Readiness reported for this SDK process. `healthy` and `degraded` renew Tool availability; `unhealthy` does not."
        ),
    ]
    time: Annotated[
        Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_3)],
        Field(description="SDK estimate of server time when this heartbeat was created."),
    ]
    config_hash: Annotated[
        Annotated[StrictStr, Field(pattern="^[a-f0-9]{64}$")],
        Field(description="SDK-computed SHA-256 digest of the Tool manifest this process most recently registered."),
    ]
    heartbeat_publication_id: Annotated[
        Annotated[UUID, BeforeValidator(_validate_string_constraints_2)],
        Field(description="UUID version 7 that identifies this heartbeat. Reuse it when retrying the same heartbeat."),
    ]


class AgentHeartbeatResponse(BaseModel):
    """Timestamps from an accepted heartbeat let the SDK estimate server-clock offset."""

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
        Field(description="Client timestamp echoed from the accepted heartbeat."),
    ]


class RegisteredTool(BaseModel):
    """Registration entry for a customer-hosted Tool associated with an Agent Identity. `tool` describes the callable Tool, and `timeout_seconds` limits how long its result can be accepted."""

    model_config = ConfigDict(extra="forbid", title="RegisteredTool", json_schema_extra=None)
    tool: Annotated[
        tool_module.Tool,
        Field(description="Model-facing Tool Definition, including its name, purpose, and parameter contract."),
    ]
    timeout_seconds: Annotated[
        Annotated[
            StrictFloat, Field(ge=-9007199254740991, le=9007199254740991, allow_inf_nan=False), Field(le=3600, gt=0)
        ],
        Field(
            description="Maximum time available for one registered customer Tool Call. The Platform derives one absolute result deadline shared by every attempt and retry. The SDK stops waiting at that deadline, but it cannot interrupt a synchronous callable that has already started."
        ),
    ]


class AgentRegistrationMessage(BaseModel):
    """Publishes the complete customer Tool manifest served by one SDK process for its Agent Identity."""

    model_config = ConfigDict(extra="forbid", title="AgentRegistrationMessage", json_schema_extra=None)
    agent_id: Annotated[
        agent_id_module.AgentId,
        Field(
            description="Agent Identity for this lifecycle request. It must match the identity bound to the SDK token."
        ),
    ]
    process_id: Annotated[
        Annotated[UUID, BeforeValidator(_validate_string_constraints_2)],
        Field(
            description="UUID version 7 generated once for this SDK process and reused across its lifecycle requests."
        ),
    ]
    tools: Annotated[
        Annotated[list[RegisteredTool], Field(strict=True, max_length=4096)],
        Field(description="Complete set of customer Tool registrations served by this SDK process."),
    ]
    config_hash: Annotated[
        Annotated[StrictStr, Field(pattern="^[a-f0-9]{64}$")],
        Field(description="SDK-computed SHA-256 digest of the complete Tool manifest in this registration."),
    ]
    manifest_publication_id: Annotated[
        Annotated[UUID, BeforeValidator(_validate_string_constraints_2)],
        Field(
            description="UUID version 7 that identifies this full-manifest publication. Reuse it when retrying the same publication."
        ),
    ]
    time: Annotated[
        Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_3)],
        Field(description="SDK estimate of server time when this registration was created."),
    ]
    sdk_version: Annotated[
        Annotated[StrictStr, Field(min_length=1, max_length=100)],
        Field(description="Version of the SDK that created this registration."),
    ]


class BridgeContentDeltaResponse(BaseModel):
    """SSE `content.delta` event carrying streamed preview text to append."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    type: Annotated[Literal["content.delta"], Field(description="Identifies this payload as a streamed content chunk.")]
    timestamp: Annotated[
        Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_3)],
        Field(description="Time when this event was emitted."),
    ]
    delta: Annotated[StrictStr, Field(description="Text to append to the current streamed preview.")]


class BridgeContentResetResponse(BaseModel):
    """Signals that previously streamed content must be discarded before rendering later chunks."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    type: Annotated[
        Literal["content.reset"], Field(description="Identifies this payload as a streamed content replacement.")
    ]
    timestamp: Annotated[
        Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_3)],
        Field(description="Time when the replacement was emitted."),
    ]
    generation: Annotated[
        Annotated[StrictInt, Field(ge=-9007199254740991, le=9007199254740991), Field(ge=1)],
        Field(description="Replacement output version. Content received before this event must be discarded."),
    ]


class BridgeTaskCompletedResponse(BaseModel):
    """SSE `task.completed` event carrying the completed Task Result."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    type: Annotated[Literal["task.completed"], Field(description="Identifies this payload as Task completion.")]
    timestamp: Annotated[
        Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_3)],
        Field(description="Time when this event was emitted."),
    ]
    result: Annotated[llm_result_module.LLMResult, Field(description="Completed Task Result.")]


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
            description="Opaque replay cursor for this event. To resume after it, send this exact value in the `Last-Event-ID` header; do not parse or construct it."
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
        Field(description="Time when the client parsed this event."),
    ]


class BridgeTaskContinueRequest(BaseModel):
    """Continues the accepted `parent_task_id` as a new Task with a client-selected child task identifier. The `type` field is `continue`."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    type: Annotated[Literal["continue"], Field(description="Identifies this request as Task continuation.")]
    parent_task_id: Annotated[
        task_id_module.TaskId,
        Field(
            description="Accepted parent Task whose conversation this request continues. The URL contains a different, client-selected continuation Task identifier."
        ),
    ]
    message: Annotated[
        Annotated[StrictStr, Field(max_length=100000, pattern="^[^\\u0000]*$")],
        Field(description="User message that continues the conversation."),
    ]
    response_format: Annotated[
        Union[response_format_module.ResponseFormat, SkipJsonSchema[None]],
        BeforeValidator(_reject_explicit_null),
        Field(description="Expected format of the continuation Task Result."),
    ] = Field(default_factory=lambda: None, validate_default=False, exclude_if=lambda value: value is None)
    deadline: Annotated[
        Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_3)],
        Field(
            description="Requested absolute UTC deadline for the continuation. It cannot extend the accepted parent Task's deadline or add another final-response grace period.",
            examples=["2025-08-30T15:00:00Z", "2025-08-30T15:00:00.000Z"],
        ),
    ]


class Attachment(BaseModel):
    model_config = ConfigDict(extra="forbid", title="Attachment", json_schema_extra=None)
    key: Annotated[
        StrictStr,
        Field(description="Client-generated correlation key that links this Task attachment to its upload receipt."),
    ]
    filename: Annotated[StrictStr, Field(description="Original filename.")]
    description: Annotated[
        Annotated[StrictStr, Field(max_length=500)], Field(description="Model-facing description of the attachment.")
    ]


class BridgeTaskCreateRequest(BaseModel):
    """Creates a new Task with a client-selected task identifier. The `type` field is `create`."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    type: Annotated[Literal["create"], Field(description="Identifies this request as Task creation.")]
    action_prompt: Annotated[
        action_prompt_module.ActionPrompt, Field(description="Instruction and contextual material for the Task.")
    ]
    attachments: Annotated[
        Union[Annotated[list[Attachment], Field(strict=True)], SkipJsonSchema[None]],
        BeforeValidator(_reject_explicit_null),
        Field(
            description="Documents associated with this Task. Upload each document before submitting the Task, then send the matching client-side key, filename, and description here.",
            json_schema_extra={"default": []},
        ),
    ] = Field(default_factory=lambda: None, validate_default=False, exclude_if=lambda value: value is None)
    routing_policy: Annotated[
        Union[routing_policy_module.RoutingPolicy, None],
        Field(description="Task-scoped Routing Policy of soft preferences.", json_schema_extra={"default": None}),
    ] = None
    response_format: Annotated[
        Union[response_format_module.ResponseFormat, None],
        Field(description="Expected format of a completed Task Result.", json_schema_extra={"default": None}),
    ] = None
    response_stream: Annotated[
        Union[StrictBool, SkipJsonSchema[None]],
        BeforeValidator(_reject_explicit_null),
        Field(
            description="Enable streamed preview chunks. Streaming can add slight latency to the final serialized Task Result. Use streamed content only for display; use the completed Task Result for processing.",
            json_schema_extra={"default": False},
        ),
    ] = Field(default_factory=lambda: None, validate_default=False, exclude_if=lambda value: value is None)
    tools: Annotated[
        Union[Annotated[list[tool_module.Tool], Field(strict=True)], None],
        Field(
            description="Optional model-facing Tool Definitions for this Task. Definitions alone do not authorize Tool dispatch or any operation that can produce an External Effect.",
            json_schema_extra={"default": None},
        ),
    ] = None
    deadline: Annotated[
        Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_3)],
        Field(
            description="Requested absolute UTC deadline for the Task. The Platform normally ends planning five minutes before this time to prepare the final response. Final response synthesis may use only already captured state and can continue for up to five minutes after this time; persistence and delivery may finish later.",
            examples=["2025-08-30T15:00:00Z", "2025-08-30T15:00:00.000Z"],
        ),
    ]


class BridgeToolResultError(BaseModel):
    """Failed result for one Tool Call."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    type: Annotated[Literal["error"], Field(description="Identifies this Tool Result as failed.")]
    tool_call_id: Annotated[
        tool_call_id_module.ToolCallId,
        Field(
            description="Identifier of the Tool Call that produced this result. Match it within the owning Task; it does not provide idempotency by itself."
        ),
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
        Field(
            description="Identifier of the Tool Call that produced this result. Match it within the owning Task; it does not provide idempotency by itself."
        ),
    ]
    output: Annotated[
        Union[Annotated[dict[StrictStr, json_value_module.JsonValue], Field(strict=True)], StrictStr],
        Field(description="Value returned by the customer-hosted Tool."),
    ]


class BridgeToolResultsRequest(BaseModel):
    """Submits Tool Results and resumes the Task's existing event stream. The `type` field is `tool_results`."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    type: Annotated[Literal["tool_results"], Field(description="Identifies this request as Tool Result submission.")]
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
