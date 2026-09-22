"""SDK hosted-Tool lifecycle and dispatch tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Annotated, TypeVar

import pytest
from pydantic import AliasChoices, BaseModel, Field, ValidationError

from dualeai import create_sdk
from dualeai.config import DualeAIConfig
from dualeai.constants import TimingDefaults
from dualeai.decorators import agent, tool
from dualeai.exceptions import DualeAIAuthError, DualeAIConnectionError
from dualeai.models.bridge import (
    AgentHeartbeatMessage,
    AgentHeartbeatResponse,
    AgentRegistrationMessage,
    Attachment,
    BridgeSSEEvent,
    BridgeTaskCompletedResponse,
    BridgeTaskContinueRequest,
    BridgeTaskCreateRequest,
    BridgeToolResultError,
    BridgeToolResultsRequest,
    BridgeToolResultSuccess,
    BridgeToolUseResponse,
    RegisteredTool,
)
from dualeai.models.skill_enum import SkillEnum
from dualeai.orchestrator import ask
from dualeai.sdk import DualeAISDK
from dualeai.tools.errors import TOOL_ERROR_MESSAGE_MAX_CHARS, format_registered_tool_error
from tests.mocks.mock_http import MockHTTPTransport

# Module-level so get_type_hints can resolve it under `from __future__ import annotations`.
_UnboundToolParam = TypeVar("_UnboundToolParam")


async def _model_config_parameter(model_config: str) -> str:
    return model_config


async def _model_fields_parameter(model_fields: str) -> str:
    return model_fields


class _SampleToolError(RuntimeError):
    """Module-level so its qualified name is stable for error-format assertions."""


class FixedJitterRandom:
    """Deterministic random source for lifecycle jitter tests."""

    def __init__(self, value: float) -> None:
        self.value = value

    def uniform(self, _lower: float, _upper: float) -> float:
        return self.value


class SecurityZone(BaseModel):
    """Structured nested parameter model for registered-tool schema tests."""

    name: str = Field(examples=["perimeter"])
    level: int


class GateCommand(BaseModel):
    """Structured registered-tool command model for schema tests."""

    gate_id: str
    zone: SecurityZone


class GateCommandResult(BaseModel):
    """Structured registered-tool result model for serialization tests."""

    gate_id: str
    state: str
    command_id: str


def test_create_sdk_coerces_constructor_arguments_with_pydantic() -> None:
    """create_sdk uses a Pydantic boundary for constructor-only settings."""
    sdk = create_sdk(
        token="dualeai_test_token",
        tenant_id="tenant-test",
        agent_id="agent_security_operations",
        max_jobs="7",
        job_timeout="11",
        auto_start="false",
    )

    assert sdk.max_jobs == 7
    assert sdk.job_timeout == 11
    assert sdk.auto_start is False
    assert sdk.config.tenant_id == "tenant-test"


@pytest.mark.unit
def test_registered_tool_requires_agent_id(config_factory, mock_http_transport: MockHTTPTransport) -> None:
    """A declared tool must not silently no-op lifecycle when agent_id is missing."""
    config: DualeAIConfig = config_factory()
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)

    with pytest.raises(RuntimeError, match="SDK tools require agent_id"):

        @tool(
            sdk=sdk,
            description="Close a named security gate.",
            timeout=timedelta(seconds=10),
        )
        async def close_security_gate(gate_id: str) -> dict[str, str]:
            return {"gate_id": gate_id}


@pytest.mark.unit
def test_registered_tool_rejects_reserved_platform_name(config_factory, mock_http_transport: MockHTTPTransport) -> None:
    """The SDK rejects platform-reserved tool names before manifest publication."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)

    with pytest.raises(ValueError, match="reserved by the platform"):

        @tool(
            sdk=sdk,
            description="Finish is reserved by the platform.",
            timeout=timedelta(seconds=10),
        )
        async def finish() -> dict[str, str]:
            return {"state": "done"}


@pytest.mark.unit
def test_registered_tool_requires_description_and_positive_timeout(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """A registered tool must expose model-facing description and a positive timeout."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)

    with pytest.raises(ValueError, match="Tool description is required"):

        @tool(
            sdk=sdk,
            description=" ",
            timeout=timedelta(seconds=10),
        )
        async def blank_description_tool(gate_id: str) -> dict[str, str]:
            return {"gate_id": gate_id}

    with pytest.raises(ValueError, match="Tool timeout must be positive"):

        @tool(
            sdk=sdk,
            description="Close a named security gate.",
            timeout=timedelta(seconds=0),
        )
        async def zero_timeout_tool(gate_id: str) -> dict[str, str]:
            return {"gate_id": gate_id}


@pytest.mark.unit
async def test_start_registers_manifest_and_first_heartbeat(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """start() publishes the generated manifest and one heartbeat."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)

    @tool(
        sdk=sdk,
        description="Close a named security gate and return the final gate state.",
        timeout=timedelta(seconds=10),
    )
    async def close_security_gate(gate_id: str, reason: str) -> dict[str, str]:
        return {"gate_id": gate_id, "state": "closed", "reason": reason}

    try:
        await sdk.start()

        requests = mock_http_transport.get_requests()
        registration = next(request for request in requests if request["path"] == "/v1/agent/registration")
        heartbeat = next(request for request in requests if request["path"] == "/v1/agent/heartbeat")

        registration_body = AgentRegistrationMessage.model_validate(registration["body"])
        heartbeat_body = AgentHeartbeatMessage.model_validate(heartbeat["body"])
        assert registration_body.agent_id == "agent_security_operations"
        assert registration_body.tools[0].tool.name == "close_security_gate"
        assert registration_body.tools[0].tool.description == (
            "Close a named security gate and return the final gate state."
        )
        assert registration_body.tools[0].timeout_seconds == 10.0
        assert len(registration_body.config_hash) == 64
        assert heartbeat_body.agent_id == registration_body.agent_id
        assert heartbeat_body.process_id == registration_body.process_id
        assert heartbeat_body.config_hash == registration_body.config_hash
        assert sdk.heartbeat_task is not None
    finally:
        await sdk.cleanup()


@pytest.mark.unit
def test_registered_tools_returns_detached_models(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """Callers cannot mutate the manifest or desynchronize it from its validator."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)

    @tool(
        sdk=sdk,
        description="Close a named security gate.",
        timeout=timedelta(seconds=10),
    )
    async def close_security_gate(gate_id: str) -> dict[str, str]:
        return {"gate_id": gate_id}

    initial_hash = sdk._config_hash()
    published = sdk.registered_tools
    published[0].tool.description = "Mutated outside the SDK"
    published[0].tool.parameters.properties["gate_id"]["type"] = "integer"

    current = sdk.registered_tools[0]
    assert current.tool.description == "Close a named security gate."
    assert current.tool.parameters.properties["gate_id"]["type"] == "string"
    assert sdk._config_hash() == initial_hash


@pytest.mark.unit
async def test_lifecycle_manifest_excludes_legacy_agent_capabilities(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """Legacy @agent metadata must not leak into the hosted-tool lifecycle manifest."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)

    @tool(
        sdk=sdk,
        description="Close a named gate and return the final state.",
        timeout=timedelta(seconds=10),
    )
    async def close_security_gate(gate_id: str) -> dict[str, str]:
        return {"gate_id": gate_id, "state": "closed"}

    tools_only_hash = sdk._config_hash()

    @agent(
        name="LegacySecurityAgent",
        org={"Security"},
        capabilities=[SkillEnum.analysis, SkillEnum.reasoning],
        sdk=sdk,
    )
    async def legacy_security_agent() -> None:
        return None

    registration = sdk._lifecycle._registration_message()
    heartbeat = sdk._lifecycle._heartbeat_message(local_sent_at=datetime.now(timezone.utc))
    body = registration.model_dump(mode="json", by_alias=True)

    assert sdk.agents
    assert registration.config_hash == tools_only_hash
    assert heartbeat.config_hash == tools_only_hash
    assert set(body) == {
        "agent_id",
        "process_id",
        "tools",
        "config_hash",
        "manifest_publication_id",
        "time",
        "sdk_version",
    }
    assert body["tools"][0]["tool"]["name"] == "close_security_gate"
    forbidden_payload = json.dumps(body, sort_keys=True)
    for forbidden in ("LegacySecurityAgent", "Security", "skills", "capabilities", "agent_type"):
        assert forbidden not in forbidden_payload


@pytest.mark.unit
async def test_heartbeat_registers_changed_manifest_before_new_hash(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """A tool added after start is registered before its hash appears in a heartbeat."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)

    await sdk.start()

    @tool(
        sdk=sdk,
        description="Open a named security gate and return the final gate state.",
        timeout=timedelta(seconds=10),
    )
    async def open_security_gate(gate_id: str) -> dict[str, str]:
        return {"gate_id": gate_id, "state": "open"}

    await sdk._lifecycle._send_heartbeat_once()
    requests = mock_http_transport.get_requests()
    lifecycle_paths = [request["path"] for request in requests if str(request["path"]).startswith("/v1/agent/")]
    assert lifecycle_paths == [
        "/v1/agent/registration",
        "/v1/agent/heartbeat",
        "/v1/agent/registration",
        "/v1/agent/heartbeat",
    ]
    refreshed_registration = AgentRegistrationMessage.model_validate(requests[-2]["body"])
    refreshed_heartbeat = AgentHeartbeatMessage.model_validate(requests[-1]["body"])
    assert refreshed_registration.tools[0].tool.name == "open_security_gate"
    assert refreshed_heartbeat.config_hash == refreshed_registration.config_hash

    await sdk.cleanup()


@pytest.mark.unit
async def test_heartbeat_uses_the_manifest_snapshot_sent_by_its_registration(
    config_factory,
    mock_http_transport: MockHTTPTransport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concurrent registration cannot make one heartbeat advertise a different hash."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)

    @tool(
        sdk=sdk,
        description="Close a named security gate.",
        timeout=timedelta(seconds=10),
    )
    async def close_security_gate(gate_id: str) -> dict[str, str]:
        return {"gate_id": gate_id}

    original_register = mock_http_transport.register_agent_manifest
    registration_started = asyncio.Event()
    release_registration = asyncio.Event()

    async def slow_register(request: AgentRegistrationMessage) -> None:
        registration_started.set()
        await release_registration.wait()
        await original_register(request)

    monkeypatch.setattr(mock_http_transport, "register_agent_manifest", slow_register)
    first_heartbeat = asyncio.create_task(sdk._lifecycle._send_heartbeat_once())
    await asyncio.wait_for(registration_started.wait(), timeout=1)

    @tool(
        sdk=sdk,
        description="Open a named security gate.",
        timeout=timedelta(seconds=10),
    )
    async def open_security_gate(gate_id: str) -> dict[str, str]:
        return {"gate_id": gate_id}

    release_registration.set()
    await first_heartbeat

    first_requests = mock_http_transport.get_requests()
    first_registration = AgentRegistrationMessage.model_validate(
        next(item for item in first_requests if item["path"] == "/v1/agent/registration")["body"]
    )
    first_beat = AgentHeartbeatMessage.model_validate(
        next(item for item in first_requests if item["path"] == "/v1/agent/heartbeat")["body"]
    )
    assert [item.tool.name for item in first_registration.tools] == ["close_security_gate"]
    assert first_beat.config_hash == first_registration.config_hash

    await sdk._lifecycle._send_heartbeat_once()
    all_requests = mock_http_transport.get_requests()
    registrations = [item for item in all_requests if item["path"] == "/v1/agent/registration"]
    heartbeats = [item for item in all_requests if item["path"] == "/v1/agent/heartbeat"]
    latest_registration = AgentRegistrationMessage.model_validate(registrations[-1]["body"])
    latest_beat = AgentHeartbeatMessage.model_validate(heartbeats[-1]["body"])
    assert [item.tool.name for item in latest_registration.tools] == [
        "close_security_gate",
        "open_security_gate",
    ]
    assert latest_beat.config_hash == latest_registration.config_hash
    await sdk.cleanup()


@pytest.mark.unit
async def test_heartbeat_refreshes_manifest_after_anti_entropy_interval(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """A running lifecycle periodically re-sends the manifest even when it is unchanged."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    await sdk.start()
    sdk._lifecycle._next_registration_refresh_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    await sdk._lifecycle._send_heartbeat_once()

    requests = mock_http_transport.get_requests()
    lifecycle_paths = [request["path"] for request in requests if str(request["path"]).startswith("/v1/agent/")]
    assert lifecycle_paths == [
        "/v1/agent/registration",
        "/v1/agent/heartbeat",
        "/v1/agent/registration",
        "/v1/agent/heartbeat",
    ]

    await sdk.cleanup()


@pytest.mark.unit
async def test_heartbeat_sleep_jitter_stays_within_bounds(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """Heartbeat sleep adds bounded jitter to avoid synchronized fleets."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)

    # `FixedJitterRandom` is a test double standing in for the real collaborator.
    sdk._lifecycle._lifecycle_jitter_random = FixedJitterRandom(-TimingDefaults.HEARTBEAT_JITTER_SECONDS)  # ty: ignore[invalid-assignment]
    assert sdk._lifecycle._heartbeat_sleep_seconds() == 45.0

    # `FixedJitterRandom` is a test double standing in for the real collaborator.
    sdk._lifecycle._lifecycle_jitter_random = FixedJitterRandom(TimingDefaults.HEARTBEAT_JITTER_SECONDS)  # ty: ignore[invalid-assignment]
    assert sdk._lifecycle._heartbeat_sleep_seconds() == 75.0


@pytest.mark.unit
async def test_registration_refresh_jitter_stays_within_bounds(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """Anti-entropy registration refresh uses a bounded jittered deadline."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    now = datetime(2026, 7, 6, 10, 0, tzinfo=timezone.utc)

    # `FixedJitterRandom` is a test double standing in for the real collaborator.
    sdk._lifecycle._lifecycle_jitter_random = FixedJitterRandom(-TimingDefaults.REGISTRATION_REFRESH_JITTER_SECONDS)  # ty: ignore[invalid-assignment]
    sdk._lifecycle._schedule_next_registration_refresh(now=now)
    assert sdk._lifecycle._next_registration_refresh_at == now + timedelta(minutes=9)

    # `FixedJitterRandom` is a test double standing in for the real collaborator.
    sdk._lifecycle._lifecycle_jitter_random = FixedJitterRandom(TimingDefaults.REGISTRATION_REFRESH_JITTER_SECONDS)  # ty: ignore[invalid-assignment]
    sdk._lifecycle._schedule_next_registration_refresh(now=now)
    assert sdk._lifecycle._next_registration_refresh_at == now + timedelta(minutes=11)


@pytest.mark.unit
async def test_registered_tool_parameter_schema_preserves_nested_definitions(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """Nested model parameters keep $ref/$defs — the platform inlines them per provider."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)

    @tool(
        sdk=sdk,
        description="Close a security gate using a structured command payload.",
        timeout=timedelta(seconds=10),
    )
    async def close_structured_gate(command: GateCommand) -> dict[str, str]:
        return {"gate_id": command.gate_id, "zone": command.zone.name}

    parameters = sdk.registered_tools[0].tool.parameters
    # The nested-model parameter references a shared definition rather than being inlined.
    assert parameters.properties["command"] == {"$ref": "#/$defs/GateCommand"}
    field_defs = parameters.field_defs
    assert field_defs is not None
    assert set(field_defs) == {"GateCommand", "SecurityZone"}
    # $ref/$defs survive on the wire (the llm-service inlines them); GateCommand.zone
    # references SecurityZone, whose level:int and authored example are preserved.
    defs_json = json.dumps(field_defs, sort_keys=True)
    assert '"$ref": "#/$defs/SecurityZone"' in defs_json
    assert '"level"' in defs_json
    assert '"integer"' in defs_json
    assert '"examples": ["perimeter"]' in defs_json


@pytest.mark.unit
async def test_registered_tool_parameter_schema_preserves_user_field_named_title(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """A parameter named title is preserved alongside Pydantic's field-title annotation."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)

    @tool(
        sdk=sdk,
        description="Create a titled security incident note.",
        timeout=timedelta(seconds=10),
    )
    async def create_incident_note(title: str) -> dict[str, str]:
        return {"title": title}

    properties = sdk.registered_tools[0].tool.parameters.properties
    # The parameter named "title" survives as a property; Pydantic also emits a
    # field-title annotation, which the platform tolerates (no local stripping).
    assert properties["title"] == {"title": "Title", "type": "string"}


@pytest.mark.unit
async def test_registered_tool_enforces_its_advertised_annotated_contract(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """The public schema and tool-use execution enforce the same constraints."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    await sdk._ensure_events_client()
    calls: list[int] = []

    @tool(sdk=sdk, description="Set a gate threshold.", timeout=timedelta(seconds=10))
    async def set_threshold(value: Annotated[int, Field(gt=0, le=10)]) -> dict[str, int]:
        calls.append(value)
        return {"value": value}

    value_schema = sdk.registered_tools[0].tool.parameters.properties["value"]
    assert value_schema["type"] == "integer"
    assert value_schema["exclusiveMinimum"] == 0
    assert value_schema["maximum"] == 10

    # Registration freezes the executable contract. Later annotation mutation
    # must not change the schema or validation used for tool calls.
    set_threshold.__annotations__["value"] = str

    cases = [
        ("lower-bound", 1, BridgeToolResultSuccess),
        ("upper-bound", 10, BridgeToolResultSuccess),
        ("below-bound", 0, BridgeToolResultError),
        ("above-bound", 11, BridgeToolResultError),
        ("boolean", True, BridgeToolResultError),
    ]
    for case_name, value, result_type in cases:
        task_id = f"task-annotated-{case_name}"
        tool_use = BridgeToolUseResponse(
            type="tool.use",
            timestamp=datetime.now(timezone.utc),
            tool_call_id=f"call-annotated-{case_name}",
            name="set_threshold",
            input={"value": value},
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=10),
        )
        mock_http_transport.inject_event(task_id, mock_http_transport.create_task_completed_event())

        await sdk._execute_tool_use(task_id, tool_use)

        result = _submitted_tool_result(mock_http_transport, task_id)
        assert isinstance(result, result_type)

    assert calls == [1, 10]


@pytest.mark.unit
async def test_registered_tool_rejects_unbound_typevar_parameter(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """An unbound TypeVar parameter fails at decoration, not as a silent empty schema."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)

    with pytest.raises(TypeError, match="unbound TypeVar"):

        @tool(sdk=sdk, description="Echo an unconstrained value.", timeout=timedelta(seconds=10))
        async def echo_value(value: _UnboundToolParam) -> dict[str, str]:  # type: ignore[valid-type]
            return {"value": str(value)}


@pytest.mark.unit
async def test_registered_tool_rejects_multi_path_validation_alias(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """A callable must expose one unambiguous JSON property per parameter."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)

    with pytest.raises(TypeError, match="unsupported multi-path validation alias"):

        @tool(sdk=sdk, description="Close a named gate.", timeout=timedelta(seconds=10))
        async def close_gate(
            gate_id: Annotated[str, Field(validation_alias=AliasChoices("gateId", "gate_id"))],
        ) -> dict[str, str]:
            return {"gate_id": gate_id}


@pytest.mark.unit
@pytest.mark.parametrize("func", [_model_config_parameter, _model_fields_parameter])
async def test_registered_tool_rejects_model_attribute_parameter_name(
    func,
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """A parameter named like a Pydantic model attribute fails loudly, not as a broken tool.

    create_model silently drops a field named e.g. model_config, which would advertise an
    empty schema while the runtime still requires the argument.
    """
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)

    with pytest.raises(TypeError, match="collide with a Pydantic model attribute"):
        tool(sdk=sdk, description="Configure a gate.", timeout=timedelta(seconds=10))(func)


@pytest.mark.unit
def test_registered_tool_error_is_module_class_prefixed_and_bounded() -> None:
    """Tool errors render as ``<module>.<class>: <message>``, length-bounded.

    The wire contract uses a stable module.class prefix plus a truncated
    free-form message, without an error enum or retryable flag.
    """
    # Known builtin: literal expected value, not derived from the implementation.
    assert format_registered_tool_error(ValueError("boom")) == "builtins.ValueError: boom"

    # Custom exception: class name and message are joined onto the module path.
    assert format_registered_tool_error(_SampleToolError("bad gate")).endswith("._SampleToolError: bad gate")

    # Oversized rendering is truncated to the schema maxLength with an ellipsis
    # marker; the whole wire message stays within the declared bound.
    bounded = format_registered_tool_error(ValueError("x" * 5000))
    assert bounded.startswith("builtins.ValueError: ")
    assert bounded.endswith("…")
    assert len(bounded) == TOOL_ERROR_MESSAGE_MAX_CHARS

    # Empty message: just the qualified name, no dangling colon.
    assert format_registered_tool_error(ValueError()) == "builtins.ValueError"


@pytest.mark.unit
async def test_registered_tool_parameter_schema_emits_parameter_default(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """A published parameter default also applies when execution omits the input."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    await sdk._ensure_events_client()

    @tool(
        sdk=sdk,
        description="Report a security gate state with a default retry budget.",
        timeout=timedelta(seconds=10),
    )
    async def report_state(retries: int = 5) -> dict[str, int]:
        return {"retries": retries}

    parameters = sdk.registered_tools[0].tool.parameters
    assert "retries" not in parameters.required
    assert parameters.properties["retries"]["default"] == 5

    task_id = "task-default-argument"
    tool_use = BridgeToolUseResponse(
        type="tool.use",
        timestamp=datetime.now(timezone.utc),
        tool_call_id="call-default-argument",
        name="report_state",
        input={},
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=10),
    )
    mock_http_transport.inject_event(task_id, mock_http_transport.create_task_completed_event())

    await sdk._execute_tool_use(task_id, tool_use)

    result = _submitted_tool_result(mock_http_transport, task_id)
    assert isinstance(result, BridgeToolResultSuccess)
    assert result.output == {"retries": 5}


@pytest.mark.unit
def test_registered_tool_rejects_non_finite_default(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """A callable default must be valid both in Python and on the JSON wire."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)

    with pytest.raises(TypeError, match="non-finite default"):

        @tool(
            sdk=sdk,
            description="Report a ratio with a sentinel default.",
            timeout=timedelta(seconds=10),
        )
        async def report_ratio(ratio: float = float("nan")) -> dict[str, float]:
            return {"ratio": ratio}


def _submitted_tool_result(
    mock_http_transport: MockHTTPTransport, task_id: str
) -> BridgeToolResultSuccess | BridgeToolResultError:
    record = mock_http_transport.get_requests_for_task(task_id)[0]
    request = record["request"]
    assert isinstance(request, BridgeToolResultsRequest)
    return request.tool_results[0]


@pytest.mark.unit
async def test_registered_tool_retries_until_attempts_exhausted(
    config_factory,
    mock_http_transport: MockHTTPTransport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """retries=N runs the callable N+1 times before submitting the failure."""
    monkeypatch.setattr(TimingDefaults, "TOOL_RETRY_BACKOFF_MULTIPLIER_SECONDS", 0.0)
    monkeypatch.setattr(TimingDefaults, "TOOL_RETRY_BACKOFF_MAX_SECONDS", 0.0)
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    await sdk._ensure_events_client()
    calls = 0

    @tool(sdk=sdk, description="Always fails.", timeout=timedelta(seconds=10), retries=2)
    async def flaky_tool(gate_id: str) -> dict[str, str]:
        nonlocal calls
        calls += 1
        raise RuntimeError("gate controller offline")

    tool_use = mock_http_transport.create_tool_use_event(
        tool_call_id="call_retry_exhausted",
        name="flaky_tool",
        tool_input={"gate_id": "north"},
    ).data
    assert isinstance(tool_use, BridgeToolUseResponse)

    await sdk._execute_tool_use("task_retry_exhausted", tool_use)

    assert calls == 3  # initial attempt + 2 retries
    result = _submitted_tool_result(mock_http_transport, "task_retry_exhausted")
    assert isinstance(result, BridgeToolResultError)
    assert result.message == "builtins.RuntimeError: gate controller offline"


@pytest.mark.unit
async def test_registered_tool_does_not_retry_by_default(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """The default retries=0 runs the callable exactly once on failure."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    await sdk._ensure_events_client()
    calls = 0

    @tool(sdk=sdk, description="Fails once.", timeout=timedelta(seconds=10))
    async def once_tool(gate_id: str) -> dict[str, str]:
        nonlocal calls
        calls += 1
        raise RuntimeError("boom")

    tool_use = mock_http_transport.create_tool_use_event(
        tool_call_id="call_no_retry",
        name="once_tool",
        tool_input={"gate_id": "north"},
    ).data
    assert isinstance(tool_use, BridgeToolUseResponse)

    await sdk._execute_tool_use("task_no_retry", tool_use)

    assert calls == 1
    result = _submitted_tool_result(mock_http_transport, "task_no_retry")
    assert isinstance(result, BridgeToolResultError)


@pytest.mark.unit
@pytest.mark.parametrize(
    "failures_before_success",
    [
        pytest.param(1, id="recovers-before-final-attempt"),
        pytest.param(2, id="recovers-on-final-attempt"),
    ],
)
async def test_registered_tool_retry_succeeds_within_budget(
    config_factory,
    mock_http_transport: MockHTTPTransport,
    monkeypatch: pytest.MonkeyPatch,
    failures_before_success: int,
) -> None:
    """A transient failure that clears anywhere in the retry budget succeeds."""
    monkeypatch.setattr(TimingDefaults, "TOOL_RETRY_BACKOFF_MULTIPLIER_SECONDS", 0.0)
    monkeypatch.setattr(TimingDefaults, "TOOL_RETRY_BACKOFF_MAX_SECONDS", 0.0)
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    await sdk._ensure_events_client()
    calls = 0

    @tool(sdk=sdk, description="Fails then recovers.", timeout=timedelta(seconds=10), retries=2)
    async def recovering_tool(gate_id: str) -> dict[str, str]:
        nonlocal calls
        calls += 1
        if calls <= failures_before_success:
            raise RuntimeError("transient blip")
        return {"gate_id": gate_id}

    task_id = f"task_recovers_after_{failures_before_success}"
    tool_use = mock_http_transport.create_tool_use_event(
        tool_call_id=f"call_recovers_after_{failures_before_success}",
        name="recovering_tool",
        tool_input={"gate_id": "north"},
    ).data
    assert isinstance(tool_use, BridgeToolUseResponse)

    await sdk._execute_tool_use(task_id, tool_use)

    assert calls == failures_before_success + 1
    result = _submitted_tool_result(mock_http_transport, task_id)
    assert isinstance(result, BridgeToolResultSuccess)
    assert result.output == {"gate_id": "north"}


@pytest.mark.unit
async def test_tool_use_not_re_executed_when_result_submission_fails(
    config_factory,
    mock_http_transport: MockHTTPTransport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A result-submission failure must not re-run the callable on redelivery.

    The callable's result is cached before submission, so a redelivered tool.use
    resubmits the cached result instead of re-executing side-effecting code.
    """
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    calls = 0

    @tool(sdk=sdk, description="Count executions of a gate close.", timeout=timedelta(seconds=10))
    async def counting_gate(gate_id: str) -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {"gate_id": gate_id, "state": "closed"}

    submit_attempts = 0
    first_submission_finished = asyncio.Event()
    second_submission_finished = asyncio.Event()
    original_submit = mock_http_transport.submit_tool_results

    async def observe_submissions(
        task_id: str,
        request: BridgeToolResultsRequest,
        *,
        last_event_id: str | None = None,
    ) -> AsyncIterator[BridgeSSEEvent]:
        nonlocal submit_attempts
        submit_attempts += 1
        try:
            async for event in original_submit(task_id, request, last_event_id=last_event_id):
                yield event
        finally:
            if submit_attempts == 1:
                first_submission_finished.set()
            else:
                second_submission_finished.set()

    monkeypatch.setattr(mock_http_transport, "submit_tool_results", observe_submissions)
    mock_http_transport.fail_next_tool_results(DualeAIConnectionError("submit failed"))

    tool_event = mock_http_transport.create_tool_use_event(
        tool_call_id="call_dedup",
        name="counting_gate",
        tool_input={"gate_id": "north"},
    )
    mock_http_transport.inject_event("task_dedup", tool_event)

    response = await ask(action="Close the gate", request_id="task_dedup", sdk=sdk)

    # First delivery: callable runs, result cached, submission fails.
    await asyncio.wait_for(first_submission_finished.wait(), timeout=1)
    assert calls == 1

    # Redelivery: the cached result is resubmitted; the callable is NOT re-run.
    mock_http_transport.inject_events(
        "task_dedup",
        [tool_event, mock_http_transport.create_task_completed_event()],
    )
    await response.model()
    await asyncio.wait_for(second_submission_finished.wait(), timeout=1)

    assert calls == 1
    assert submit_attempts == 2


@pytest.mark.unit
async def test_concurrent_tool_executions_are_bounded(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """A burst of tool.use events runs at most max_jobs callables concurrently."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False, max_jobs=2)
    concurrent = 0
    peak = 0
    total_ran = 0
    completed = 0
    release = asyncio.Event()
    limit_reached = asyncio.Event()
    all_completed = asyncio.Event()

    @tool(sdk=sdk, description="Block until released.", timeout=timedelta(seconds=30))
    async def blocking_tool(gate_id: str) -> dict[str, str]:
        nonlocal completed, concurrent, peak, total_ran
        total_ran += 1
        concurrent += 1
        peak = max(peak, concurrent)
        if concurrent == 2:
            limit_reached.set()
        try:
            await release.wait()
            return {"gate_id": gate_id}
        finally:
            concurrent -= 1
            completed += 1
            if completed == 5:
                all_completed.set()

    events = []
    for index in range(5):
        events.append(
            mock_http_transport.create_tool_use_event(
                tool_call_id=f"call_{index}",
                name="blocking_tool",
                tool_input={"gate_id": f"gate_{index}"},
            )
        )
    events.append(mock_http_transport.create_task_completed_event())
    mock_http_transport.inject_events("task_burst", events)

    response = await ask(action="Run the tool burst", request_id="task_burst", sdk=sdk)
    await asyncio.wait_for(limit_reached.wait(), timeout=1)

    assert peak == 2  # bounded at max_jobs (RED: peak == 5 without the semaphore)

    release.set()
    await response.model()
    await asyncio.wait_for(all_completed.wait(), timeout=1)

    assert concurrent == 0
    assert total_ran == 5  # every queued tool.use eventually executed, none dropped


@pytest.mark.unit
async def test_registered_tool_returns_pydantic_result_as_mapping(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """Pydantic tool return values are converted to bridge-compatible mappings."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)

    @tool(
        sdk=sdk,
        description="Close a security gate using a structured command payload.",
        timeout=timedelta(seconds=10),
    )
    async def close_structured_gate(command: GateCommand) -> GateCommandResult:
        return GateCommandResult(gate_id=command.gate_id, state="closed", command_id=f"cmd-{command.zone.name}")

    result = await sdk._run_registered_tool(
        "task_registered_tool",
        BridgeToolUseResponse(
            type="tool.use",
            timestamp=datetime.now(timezone.utc),
            tool_call_id="call-structured-result",
            name="close_structured_gate",
            input={"command": {"gate_id": "north", "zone": {"name": "perimeter", "level": 2}}},
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=10),
        ),
    )

    assert result == {"gate_id": "north", "state": "closed", "command_id": "cmd-perimeter"}


@pytest.mark.unit
async def test_registered_tool_request_deadline_caps_long_tool_timeout(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """A near request deadline fires before a far decorator timeout.

    Observable-outcome twin of
    test_tool_exceeding_timeout_yields_error_and_cancels_callable (which covers
    the decorator-timeout-first arm): a never-returning tool with a 60s
    decorator timeout but a ~0.05s request deadline must be cancelled and report
    an error result — proving the earliest limit governs, not the mechanism.
    """
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    await sdk._ensure_events_client()

    cancelled = {"seen": False}

    @tool(sdk=sdk, description="Slow tool with a far decorator timeout.", timeout=timedelta(seconds=60))
    async def deadline_bound_tool(gate_id: str) -> dict[str, str]:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled["seen"] = True
            raise
        return {"gate_id": gate_id}

    tool_use = BridgeToolUseResponse(
        type="tool.use",
        timestamp=datetime.now(timezone.utc),
        tool_call_id="call-deadline-bound",
        name="deadline_bound_tool",
        input={"gate_id": "g1"},
        # Near deadline — must cap the 60s decorator timeout.
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=0.05),
    )
    mock_http_transport.inject_event("task_deadline_bound", mock_http_transport.create_task_completed_event())

    await sdk._execute_tool_use("task_deadline_bound", tool_use)

    result = _submitted_tool_result(mock_http_transport, "task_deadline_bound")
    assert isinstance(result, BridgeToolResultError)
    assert cancelled["seen"] is True  # cancelled at the deadline, not after 60s


@pytest.mark.unit
async def test_registered_tool_config_hash_uses_canonical_escaped_json(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """SDK config_hash uses stable ASCII escapes for non-ASCII manifests."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)

    @tool(
        sdk=sdk,
        description="Fermer une grille de sécurité.",
        timeout=timedelta(seconds=10),
    )
    async def close_gate(gate_id: str) -> dict[str, str]:
        return {"gate_id": gate_id}

    # Frozen golden for a non-ASCII manifest. It pins ensure_ascii encoding so the
    # same manifest keeps one digest across SDK processes and versions.
    non_ascii_golden = "24aaaefd01809222bd43b0c78f1d802af6ad0c100326269ddac9383c643b7232"
    assert sdk._config_hash() == non_ascii_golden
    assert sdk._config_hash() == sdk._config_hash()  # stable across repeated calls


@pytest.mark.unit
async def test_config_hash_includes_and_canonicalizes_parameters(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """Parameter schemas are part of config_hash and canonicalized stably.

    The same manifest has the same SDK hash. A changed parameter schema has a
    different hash.
    """

    def two_param_hash() -> str:
        sdk = DualeAISDK(
            config=config_factory(agent_id="agent_security_operations"),
            transport=mock_http_transport,
            auto_start=False,
        )

        @tool(sdk=sdk, description="Set gate.", timeout=timedelta(seconds=1))
        async def set_gate(width: int, height: int) -> dict[str, int]:
            return {"width": width, "height": height}

        return sdk._config_hash()

    def one_param_hash() -> str:
        sdk = DualeAISDK(
            config=config_factory(agent_id="agent_security_operations"),
            transport=mock_http_transport,
            auto_start=False,
        )

        @tool(sdk=sdk, description="Set gate.", timeout=timedelta(seconds=1))
        async def set_gate(width: int) -> dict[str, int]:
            return {"width": width}

        return sdk._config_hash()

    # Stable canonicalization: identical manifest → identical hash.
    assert two_param_hash() == two_param_hash()
    # Parameters participate in the hash: a changed schema → a changed hash.
    assert two_param_hash() != one_param_hash()


@pytest.mark.unit
async def test_registered_tool_config_hash_golden_detects_schema_gen_drift(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """Golden config_hash for a fixed manifest — change-detector for schema-gen drift.

    This is a characterization/drift guard, not a spec assertion: the pinned hash
    is derived from the current canonical output on purpose. Its job is to FAIL
    when a dependency or compiler change alters the generated tool-parameter
    JSON Schema.
    A failure here means the wire config_hash shifted for identical tool code;
    investigate the Pydantic or schema-generation change before re-pinning it.
    """
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)

    @tool(
        sdk=sdk,
        description="Close a security gate by identifier.",
        timeout=timedelta(seconds=10),
    )
    async def close_gate(gate_id: str) -> dict[str, str]:
        return {"gate_id": gate_id}

    # Re-pinned when generated tool schemas began preserving $ref/$defs and field
    # titles.
    golden = "3692635cfc9984ac26236f3b4e74d2110d6630ca38e64db6bea2239d2887afd6"
    assert sdk._config_hash() == golden


@pytest.mark.unit
async def test_registered_tool_config_hash_is_a_one_sided_canonicalization_golden(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """Freeze the SDK hash for a no-parameter manifest.

    This guards the SDK's canonicalization only. It does not prove agreement with
    a server-side recomputation.
    """
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)

    @tool(sdk=sdk, description="Close a named security gate.", timeout=timedelta(seconds=10))
    async def close_gate() -> dict[str, str]:
        return {}

    assert sdk._config_hash() == "065c719fed99b179e89d484df76bc68922d5c6206f1a2799fb96bf5023989ee0"


@pytest.mark.unit
def test_registered_tool_config_hash_normalizes_negative_zero(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """Equal JSON numeric defaults have one cross-tier config hash."""

    def config_hash(default: float) -> str:
        sdk = DualeAISDK(
            config=config_factory(agent_id="agent_security_operations"),
            transport=mock_http_transport,
            auto_start=False,
        )

        @tool(
            sdk=sdk,
            description="Configure a security gate.",
            timeout=timedelta(seconds=10),
        )
        async def configure_gate(value: float = default) -> dict[str, float]:
            return {"value": value}

        return sdk._config_hash()

    assert config_hash(-0.0) == config_hash(0.0)


@pytest.mark.unit
async def test_start_is_idempotent_when_called_concurrently(
    config_factory,
    mock_http_transport: MockHTTPTransport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent start paths publish lifecycle messages once."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    original_register = mock_http_transport.register_agent_manifest
    registration_started = asyncio.Event()
    release_registration = asyncio.Event()

    async def slow_register(request: AgentRegistrationMessage) -> None:
        registration_started.set()
        await release_registration.wait()
        await original_register(request)

    monkeypatch.setattr(mock_http_transport, "register_agent_manifest", slow_register)

    first_start = asyncio.create_task(sdk.start())
    try:
        await asyncio.wait_for(registration_started.wait(), timeout=1)
        second_start = asyncio.create_task(sdk.start())
        release_registration.set()
        await asyncio.gather(first_start, second_start)

        lifecycle_paths = [
            request["path"]
            for request in mock_http_transport.get_requests()
            if str(request["path"]).startswith("/v1/agent/")
        ]
        assert lifecycle_paths == [
            "/v1/agent/registration",
            "/v1/agent/heartbeat",
        ]
    finally:
        await sdk.cleanup()


@pytest.mark.unit
async def test_zero_heartbeat_interval_stays_zero(
    config_factory,
    mock_http_transport: MockHTTPTransport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tests can still force immediate heartbeats without negative sleep."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    monkeypatch.setattr(TimingDefaults, "HEARTBEAT_INTERVAL_SECONDS", 0.0)
    # `FixedJitterRandom` is a test double standing in for the real collaborator.
    sdk._lifecycle._lifecycle_jitter_random = FixedJitterRandom(TimingDefaults.HEARTBEAT_JITTER_SECONDS)  # ty: ignore[invalid-assignment]

    assert sdk._lifecycle._heartbeat_sleep_seconds() == 0.0


@pytest.mark.unit
async def test_serve_runs_until_stop_event_and_deregisters(
    config_factory,
    mock_http_transport: MockHTTPTransport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """serve() starts hosted-agent lifecycle and cleans up on explicit stop."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    stop_event = asyncio.Event()
    heartbeat_sent = asyncio.Event()
    original_send_heartbeat = mock_http_transport.send_agent_heartbeat

    async def send_heartbeat_and_signal(request: AgentHeartbeatMessage) -> AgentHeartbeatResponse:
        response = await original_send_heartbeat(request)
        heartbeat_sent.set()
        return response

    monkeypatch.setattr(mock_http_transport, "send_agent_heartbeat", send_heartbeat_and_signal)

    serve_task = asyncio.create_task(sdk.serve(stop_event=stop_event))
    try:
        await asyncio.wait_for(heartbeat_sent.wait(), timeout=1)
        stop_event.set()
        await asyncio.wait_for(serve_task, timeout=1)
    finally:
        stop_event.set()
        if not serve_task.done():
            serve_task.cancel()
            with suppress(asyncio.CancelledError):
                await serve_task

    lifecycle_paths = [
        request["path"]
        for request in mock_http_transport.get_requests()
        if str(request["path"]).startswith("/v1/agent/")
    ]
    assert lifecycle_paths == [
        "/v1/agent/registration",
        "/v1/agent/heartbeat",
        "/v1/agent/deregistration",
    ]


def _heartbeat_ok() -> AgentHeartbeatResponse:
    now = datetime.now(timezone.utc)
    return AgentHeartbeatResponse(server_received_at=now, server_sent_at=now, client_sent_at=now)


@pytest.mark.unit
async def test_serve_stops_after_two_consecutive_heartbeat_auth_failures(
    config_factory,
    mock_http_transport: MockHTTPTransport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """serve() tolerates one rejected heartbeat, then stops on a second."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    calls = 0

    async def auth_reject_after_start(_snapshot: object = None) -> AgentHeartbeatResponse:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise DualeAIAuthError("token revoked")
        return _heartbeat_ok()

    monkeypatch.setattr(TimingDefaults, "HEARTBEAT_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(sdk._lifecycle, "_send_heartbeat_once", auth_reject_after_start)

    with pytest.raises(DualeAIAuthError, match="token revoked"):
        await sdk.serve()
    # Startup beat (1) ok; beat 2 auth-rejected (tolerated once); beat 3 auth-rejected → stop.
    assert calls == 3


@pytest.mark.unit
async def test_serve_survives_single_heartbeat_auth_blip(
    config_factory,
    mock_http_transport: MockHTTPTransport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """serve() continues when one rejected heartbeat is followed by recovery."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    stop = asyncio.Event()
    calls = 0

    async def auth_blip_then_recover(_snapshot: object = None) -> AgentHeartbeatResponse:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise DualeAIAuthError("transient authz blip")
        if calls >= 3:
            stop.set()  # recovered — end serve cleanly
        return _heartbeat_ok()

    monkeypatch.setattr(TimingDefaults, "HEARTBEAT_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(sdk._lifecycle, "_send_heartbeat_once", auth_blip_then_recover)

    # Must return normally — a single auth blip is tolerated, not fatal.
    await sdk.serve(stop_event=stop)
    assert calls >= 3


@pytest.mark.unit
async def test_serve_survives_transient_heartbeat_failure(
    config_factory,
    mock_http_transport: MockHTTPTransport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single transient (non-auth) heartbeat failure does not tear down serve()."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    stop = asyncio.Event()
    calls = 0

    async def flaky_then_recover(_snapshot: object = None) -> AgentHeartbeatResponse:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise DualeAIConnectionError("transient blip")
        if calls >= 3:
            stop.set()  # recovered — end serve cleanly
        return _heartbeat_ok()

    monkeypatch.setattr(TimingDefaults, "HEARTBEAT_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(sdk._lifecycle, "_send_heartbeat_once", flaky_then_recover)

    # Must return normally — the transient blip is tolerated, not fatal.
    await sdk.serve(stop_event=stop)
    assert calls >= 3


@pytest.mark.unit
async def test_serve_stops_after_consecutive_heartbeat_failures(
    config_factory,
    mock_http_transport: MockHTTPTransport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sustained heartbeat failure stops serve() after the bounded tolerance."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    calls = 0

    async def always_fail_after_start(_snapshot: object = None) -> AgentHeartbeatResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _heartbeat_ok()
        raise DualeAIConnectionError("bridge down")

    monkeypatch.setattr(TimingDefaults, "HEARTBEAT_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(sdk._lifecycle, "_send_heartbeat_once", always_fail_after_start)

    with pytest.raises(DualeAIConnectionError, match="bridge down"):
        await sdk.serve()
    # Startup beat (1) + MAX_CONSECUTIVE_HEARTBEAT_FAILURES failing loop beats.
    assert calls == 1 + TimingDefaults.MAX_CONSECUTIVE_HEARTBEAT_FAILURES


@pytest.mark.unit
async def test_serve_resets_failure_counter_after_recovery(
    config_factory,
    mock_http_transport: MockHTTPTransport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful beat resets the transient-failure counter.

    Intermittent blips separated by recoveries must never accumulate to the
    bound — each pair of failures (< MAX) is cleared by the following success.
    Without the reset, the second failure run would reach MAX and stop serve().
    """
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    stop = asyncio.Event()
    beats = 0
    # Startup ok, then two fail/fail-then-recover runs. With MAX=3 and the reset,
    # no run reaches the bound. Without the reset, beat 5 would be the 3rd
    # cumulative failure and stop serve().
    outcomes = iter(["ok", "fail", "fail", "ok", "fail", "fail", "ok"])

    async def patterned(_snapshot: object = None) -> AgentHeartbeatResponse:
        nonlocal beats
        beats += 1
        outcome = next(outcomes, "stop")
        if outcome == "stop":
            stop.set()
            return _heartbeat_ok()
        if outcome == "fail":
            raise DualeAIConnectionError("blip")
        return _heartbeat_ok()

    monkeypatch.setattr(TimingDefaults, "HEARTBEAT_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(sdk._lifecycle, "_send_heartbeat_once", patterned)

    # Must return normally — recoveries reset the counter, so the bound is never hit.
    await sdk.serve(stop_event=stop)
    assert beats >= 7


@pytest.mark.unit
async def test_heartbeat_clock_offset_uses_server_and_local_midpoints(
    config_factory,
) -> None:
    """Clock offset estimation removes request latency from the heartbeat sample."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, auto_start=False)
    local_sent_at = datetime(2026, 7, 6, 10, 0, 0, tzinfo=timezone.utc)
    local_received_at = datetime(2026, 7, 6, 10, 0, 2, tzinfo=timezone.utc)
    response = AgentHeartbeatResponse(
        server_received_at=datetime(2026, 7, 6, 10, 1, 1, tzinfo=timezone.utc),
        server_sent_at=datetime(2026, 7, 6, 10, 1, 3, tzinfo=timezone.utc),
        client_sent_at=local_sent_at,
    )

    sdk._lifecycle._update_clock_offset(
        local_sent_at=local_sent_at,
        local_received_at=local_received_at,
        response=response,
    )

    assert sdk._estimated_server_time(local_sent_at) == datetime(2026, 7, 6, 10, 1, 1, tzinfo=timezone.utc)


@pytest.mark.unit
async def test_heartbeat_clock_offset_ignores_slow_samples(
    config_factory,
) -> None:
    """Slow heartbeat round trips do not replace the previous clock-offset estimate."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, auto_start=False)
    sdk._lifecycle._clock_offset = timedelta(minutes=1)
    local_sent_at = datetime(2026, 7, 6, 10, 0, 0, tzinfo=timezone.utc)
    local_received_at = datetime(2026, 7, 6, 10, 0, 6, tzinfo=timezone.utc)
    response = AgentHeartbeatResponse(
        server_received_at=datetime(2026, 7, 6, 10, 5, 0, tzinfo=timezone.utc),
        server_sent_at=datetime(2026, 7, 6, 10, 5, 1, tzinfo=timezone.utc),
        client_sent_at=local_sent_at,
    )

    sdk._lifecycle._update_clock_offset(
        local_sent_at=local_sent_at,
        local_received_at=local_received_at,
        response=response,
    )

    assert sdk._estimated_server_time(local_sent_at) == datetime(2026, 7, 6, 10, 1, 0, tzinfo=timezone.utc)


@pytest.mark.unit
async def test_cleanup_deregisters_started_lifecycle(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """cleanup() sends a best-effort deregistration after lifecycle start."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)

    await sdk.start()
    await sdk.cleanup()

    requests = mock_http_transport.get_requests()
    deregistration = next(request for request in requests if request["path"] == "/v1/agent/deregistration")
    assert deregistration["body"]["agent_id"] == "agent_security_operations"
    assert deregistration["body"]["reason"] == "shutdown"


@pytest.mark.unit
async def test_cleanup_allows_lifecycle_restart(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """cleanup() resets lifecycle startup state so the same SDK can start again."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)

    await sdk.start()
    await sdk.cleanup()
    await sdk.start()
    await sdk.cleanup()

    lifecycle_paths = [
        request["path"]
        for request in mock_http_transport.get_requests()
        if str(request["path"]).startswith("/v1/agent/")
    ]
    assert lifecycle_paths == [
        "/v1/agent/registration",
        "/v1/agent/heartbeat",
        "/v1/agent/deregistration",
        "/v1/agent/registration",
        "/v1/agent/heartbeat",
        "/v1/agent/deregistration",
    ]


@pytest.mark.unit
async def test_startup_heartbeat_failure_deregisters_published_registration(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """A failed first heartbeat still emits best-effort deregistration after registration."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    mock_http_transport.fail_next_heartbeat(RuntimeError("heartbeat forbidden"))

    with pytest.raises(RuntimeError, match="heartbeat forbidden"):
        await sdk.serve()

    lifecycle_paths = [
        request["path"]
        for request in mock_http_transport.get_requests()
        if str(request["path"]).startswith("/v1/agent/")
    ]
    assert lifecycle_paths == [
        "/v1/agent/registration",
        "/v1/agent/heartbeat",
        "/v1/agent/deregistration",
    ]
    deregistration = mock_http_transport.get_requests()[-1]
    assert deregistration["body"]["reason"] == "startup_failed"


@pytest.mark.unit
async def test_submit_tool_results_passes_generated_request_to_transport(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """submit_tool_results() passes the generated request through its transport port."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    await sdk._ensure_events_client()
    mock_http_transport.inject_event("task_123", mock_http_transport.create_task_completed_event())

    terminal = await sdk.submit_tool_results(
        "task_123",
        [
            BridgeToolResultSuccess(
                type="success",
                tool_call_id="call_123",
                output={"state": "closed"},
            )
        ],
    )

    assert isinstance(terminal, BridgeTaskCompletedResponse)
    record = mock_http_transport.get_requests_for_task("task_123")[0]
    request = record["request"]
    assert isinstance(request, BridgeToolResultsRequest)
    assert record["task_id"] == "task_123"
    assert request.type == "tool_results"
    assert request.tool_results[0].tool_call_id == "call_123"


@pytest.mark.unit
async def test_tool_result_model_rejects_non_finite_json_before_submission(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """The generated wire model rejects non-finite JSON before any request is sent."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    await sdk._ensure_events_client()

    with pytest.raises(ValidationError, match="finite number"):
        BridgeToolResultSuccess(
            type="success",
            tool_call_id="call_non_finite_manual",
            output={"score": float("nan")},
        )

    assert mock_http_transport.get_requests_for_task("task_non_finite_manual") == []


@pytest.mark.unit
async def test_tool_use_executes_registered_tool_and_posts_success_result(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """A bridge tool.use event runs the local callable and posts tool_results."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    await sdk._ensure_events_client()

    @tool(
        sdk=sdk,
        description="Close a named security gate and return the final gate state.",
        timeout=timedelta(seconds=10),
    )
    async def close_security_gate(gate_id: str, reason: str) -> dict[str, str]:
        return {"gate_id": gate_id, "state": "closed", "reason": reason}

    tool_use = mock_http_transport.create_tool_use_event(
        tool_call_id="call_456",
        name="close_security_gate",
        tool_input={"gate_id": "north", "reason": "drill"},
    ).data
    assert isinstance(tool_use, BridgeToolUseResponse)
    mock_http_transport.inject_event("task_456", mock_http_transport.create_task_completed_event())

    await sdk._execute_tool_use("task_456", tool_use)

    result = _submitted_tool_result(mock_http_transport, "task_456")
    assert result.type == "success"
    assert isinstance(result, BridgeToolResultSuccess)
    assert result.tool_call_id == "call_456"
    assert result.output == {"gate_id": "north", "state": "closed", "reason": "drill"}


@pytest.mark.unit
async def test_tool_use_posts_error_for_invalid_input_without_calling_tool(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """Invalid bridge input is reported as an error result before customer code runs."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    await sdk._ensure_events_client()
    calls = 0

    @tool(
        sdk=sdk,
        description="Close a named security gate and return the final gate state.",
        timeout=timedelta(seconds=10),
    )
    async def close_security_gate(gate_id: str) -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {"gate_id": gate_id, "state": "closed"}

    tool_use = BridgeToolUseResponse(
        type="tool.use",
        timestamp=datetime.now(timezone.utc),
        tool_call_id="call-invalid-input",
        name="close_security_gate",
        input={},
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=10),
    )
    mock_http_transport.inject_event("task_invalid_input", mock_http_transport.create_task_completed_event())

    await sdk._execute_tool_use("task_invalid_input", tool_use)

    assert calls == 0
    result = _submitted_tool_result(mock_http_transport, "task_invalid_input")
    assert result.type == "error"
    assert isinstance(result, BridgeToolResultError)
    assert result.tool_call_id == "call-invalid-input"
    assert "Missing required tool input parameter: gate_id" in result.message


@pytest.mark.unit
@pytest.mark.parametrize(
    ("invalid_output", "expected_type_name"),
    [
        pytest.param(object(), "object", id="non-serializable-object"),
        pytest.param(
            {"gate_id": "north", "score": float("nan")},
            "dict",
            id="non-finite-mapping",
        ),
    ],
)
async def test_tool_use_posts_error_for_invalid_output(
    config_factory,
    mock_http_transport: MockHTTPTransport,
    invalid_output: object,
    expected_type_name: str,
) -> None:
    """Invalid customer output is converted into a typed generated error result."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    await sdk._ensure_events_client()

    @tool(
        sdk=sdk,
        description="Close a named security gate and return the final gate state.",
        timeout=timedelta(seconds=10),
    )
    async def close_security_gate(gate_id: str) -> object:
        return invalid_output

    task_id = f"task_invalid_output_{expected_type_name}"
    tool_call_id = f"call-invalid-output-{expected_type_name}"
    tool_use = BridgeToolUseResponse(
        type="tool.use",
        timestamp=datetime.now(timezone.utc),
        tool_call_id=tool_call_id,
        name="close_security_gate",
        input={"gate_id": "north"},
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=10),
    )
    mock_http_transport.inject_event(task_id, mock_http_transport.create_task_completed_event())

    await sdk._execute_tool_use(task_id, tool_use)

    result = _submitted_tool_result(mock_http_transport, task_id)
    assert result.type == "error"
    assert isinstance(result, BridgeToolResultError)
    assert result.tool_call_id == tool_call_id
    assert result.message == (
        f"builtins.TypeError: Tool returned {expected_type_name}; a tool result must be JSON-serializable "
        '(a str, a mapping, a Pydantic model, or a value wrappable as {"result": ...}).'
    )


@pytest.mark.unit
async def test_tool_input_rejects_int_bool_confusion(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """int<->bool arguments are rejected rather than silently coerced."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    await sdk._ensure_events_client()

    @tool(sdk=sdk, description="Toggle.", timeout=timedelta(seconds=10))
    async def toggle(active: bool, count: int) -> dict[str, object]:
        return {"active": active, "count": count}

    # active (bool) receives an int 1; a non-strict provider could send this.
    tool_use = BridgeToolUseResponse(
        type="tool.use",
        timestamp=datetime.now(timezone.utc),
        tool_call_id="call-confuse",
        name="toggle",
        input={"active": 1, "count": 2},
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=10),
    )
    mock_http_transport.inject_event("task_confuse", mock_http_transport.create_task_completed_event())

    await sdk._execute_tool_use("task_confuse", tool_use)

    result = _submitted_tool_result(mock_http_transport, "task_confuse")
    assert result.type == "error"
    assert isinstance(result, BridgeToolResultError)
    assert "'active'" in result.message


@pytest.mark.unit
@pytest.mark.parametrize(
    ("transform_failure", "forbidden_fragments"),
    [
        pytest.param("returns-non-string", ("p4ss",), id="non-string-return"),
        pytest.param("raises", ("p4ss", "redactor bug"), id="raises"),
    ],
)
async def test_tool_error_transform_failure_fails_closed_generic(
    config_factory,
    mock_http_transport: MockHTTPTransport,
    transform_failure: str,
    forbidden_fragments: tuple[str, ...],
) -> None:
    """A broken error transform fails closed without leaking either failure."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    await sdk._ensure_events_client()

    def bad_transform(exc: Exception) -> str:
        if transform_failure == "returns-non-string":
            return [str(exc)]  # ty: ignore[invalid-return-type]
        raise ValueError("redactor bug")

    @tool(sdk=sdk, description="Boom.", timeout=timedelta(seconds=10), error_transform=bad_transform)
    async def boom(gate_id: str) -> dict[str, str]:
        raise RuntimeError(f"postgres://user:p4ss@db/{gate_id}")

    tool_use = BridgeToolUseResponse(
        type="tool.use",
        timestamp=datetime.now(timezone.utc),
        tool_call_id=f"call-{transform_failure}",
        name="boom",
        input={"gate_id": "north"},
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=10),
    )
    task_id = f"task_{transform_failure}"
    mock_http_transport.inject_event(task_id, mock_http_transport.create_task_completed_event())

    await sdk._execute_tool_use(task_id, tool_use)

    result = _submitted_tool_result(mock_http_transport, task_id)
    assert result.type == "error"
    assert isinstance(result, BridgeToolResultError)
    assert result.message == "boom: tool execution failed"
    assert all(fragment not in result.message for fragment in forbidden_fragments)


@pytest.mark.unit
async def test_tool_input_rejects_int_bool_confusion_through_optional(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """The int<->bool guard applies through Optional/union types."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    await sdk._ensure_events_client()

    @tool(sdk=sdk, description="Toggle.", timeout=timedelta(seconds=10))
    async def toggle(active: bool | None) -> dict[str, object]:
        return {"active": active}

    tool_use = BridgeToolUseResponse(
        type="tool.use",
        timestamp=datetime.now(timezone.utc),
        tool_call_id="call-opt",
        name="toggle",
        input={"active": 1},  # int for Optional[bool] must be rejected, not coerced to True
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=10),
    )
    mock_http_transport.inject_event("task_opt", mock_http_transport.create_task_completed_event())

    await sdk._execute_tool_use("task_opt", tool_use)

    result = _submitted_tool_result(mock_http_transport, "task_opt")
    assert result.type == "error"
    assert isinstance(result, BridgeToolResultError)
    assert "'active'" in result.message


@pytest.mark.unit
async def test_tool_error_transform_redacts_model_facing_message(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """A per-@tool error_transform controls the model-facing error text."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    await sdk._ensure_events_client()

    @tool(
        sdk=sdk,
        description="Boom.",
        timeout=timedelta(seconds=10),
        error_transform=lambda _exc: "gate service unavailable",
    )
    async def boom(gate_id: str) -> dict[str, str]:
        raise RuntimeError(f"postgres://user:p4ss@db/{gate_id}")

    tool_use = BridgeToolUseResponse(
        type="tool.use",
        timestamp=datetime.now(timezone.utc),
        tool_call_id="call-redact",
        name="boom",
        input={"gate_id": "north"},
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=10),
    )
    mock_http_transport.inject_event("task_redact", mock_http_transport.create_task_completed_event())

    await sdk._execute_tool_use("task_redact", tool_use)

    result = _submitted_tool_result(mock_http_transport, "task_redact")
    assert result.type == "error"
    assert isinstance(result, BridgeToolResultError)
    # The raw secret never reaches the model; only the transform output does.
    assert result.message == "gate service unavailable"
    assert "p4ss" not in result.message


@pytest.mark.unit
async def test_tool_use_error_names_invalid_input_parameter(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """A bad scalar input names the offending parameter."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    await sdk._ensure_events_client()

    @tool(sdk=sdk, description="Set gate levels.", timeout=timedelta(seconds=10))
    async def set_gate_levels(width: int, height: int) -> dict[str, int]:
        return {"width": width, "height": height}

    tool_use = BridgeToolUseResponse(
        type="tool.use",
        timestamp=datetime.now(timezone.utc),
        tool_call_id="call-bad-input",
        name="set_gate_levels",
        input={"width": "not-an-int", "height": 5},
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=10),
    )
    mock_http_transport.inject_event("task_bad_input", mock_http_transport.create_task_completed_event())

    await sdk._execute_tool_use("task_bad_input", tool_use)

    result = _submitted_tool_result(mock_http_transport, "task_bad_input")
    assert result.type == "error"
    assert isinstance(result, BridgeToolResultError)
    assert result.tool_call_id == "call-bad-input"
    # Names the specific offending parameter ('width'), not just the anonymous type,
    # so the model knows which argument to fix on its next call.
    assert "'width'" in result.message


@pytest.mark.unit
async def test_tool_can_read_its_call_context(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """current_tool_context() exposes call id, attempt, Task, and deadline."""
    from dualeai import current_tool_context

    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    await sdk._ensure_events_client()

    seen: dict[str, object] = {}

    @tool(sdk=sdk, description="Report context.", timeout=timedelta(seconds=10))
    async def report_context() -> dict[str, str]:
        ctx = current_tool_context()
        assert ctx is not None
        seen["tool_call_id"] = ctx.tool_call_id
        seen["attempt"] = ctx.attempt
        seen["task_id"] = ctx.task_id
        seen["remaining_positive"] = ctx.remaining_seconds() > 0
        return {"ok": "yes"}

    tool_use = BridgeToolUseResponse(
        type="tool.use",
        timestamp=datetime.now(timezone.utc),
        tool_call_id="call-ctx",
        name="report_context",
        input={},
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=10),
    )
    mock_http_transport.inject_event("task_ctx", mock_http_transport.create_task_completed_event())

    await sdk._execute_tool_use("task_ctx", tool_use)

    assert seen["tool_call_id"] == "call-ctx"
    assert seen["attempt"] == 1
    assert seen["task_id"] == "task_ctx"
    assert seen["remaining_positive"] is True
    # Context does not leak outside the call.
    assert current_tool_context() is None


@pytest.mark.unit
async def test_sync_tool_can_read_its_call_context(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """A SYNC @tool runs in a worker thread; current_tool_context() must still work.

    loop.run_in_executor does NOT propagate contextvars into the worker thread, so
    without copy_context()+ctx.run the context reads None inside a sync tool and its
    idempotency key is lost. Red before that fix, green after.
    """
    from dualeai import current_tool_context

    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    await sdk._ensure_events_client()

    seen: dict[str, object] = {}

    @tool(sdk=sdk, description="Report context from a sync tool.", timeout=timedelta(seconds=10))
    def report_context_sync(gate_id: str) -> dict[str, str]:
        ctx = current_tool_context()
        assert ctx is not None  # None before the copy_context() fix
        seen["tool_call_id"] = ctx.tool_call_id
        seen["attempt"] = ctx.attempt
        return {"gate_id": gate_id}

    tool_use = BridgeToolUseResponse(
        type="tool.use",
        timestamp=datetime.now(timezone.utc),
        tool_call_id="call-ctx-sync",
        name="report_context_sync",
        input={"gate_id": "g1"},
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=10),
    )
    mock_http_transport.inject_event("task_ctx_sync", mock_http_transport.create_task_completed_event())

    await sdk._execute_tool_use("task_ctx_sync", tool_use)

    assert seen["tool_call_id"] == "call-ctx-sync"
    assert seen["attempt"] == 1


@pytest.mark.unit
async def test_run_task_create_body_serializes_tool_schema_with_wire_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The task-create body must be dumped by_alias so the alias-only tool schema
    field goes out as ``additionalProperties`` (JSON Schema), not the snake-case
    field name a bridge Parameters(extra=forbid) would reject."""
    from dualeai.events.http_transport import HTTPTransport
    from dualeai.models.tool import Parameters as WireParameters
    from dualeai.models.tool import Tool as WireTool

    transport = HTTPTransport(endpoint="https://api.test.duale.ai", token="dualeai_test_token_padded_1234")
    captured: dict[str, object] = {}

    async def _spy(*, method: str, path: str, json_data: object, task_id: str, last_event_id: str | None = None):
        captured["json"] = json_data
        for _ in ():  # empty — makes _spy an async generator (like the real method) yielding nothing
            yield

    monkeypatch.setattr(transport, "_stream_request", _spy)

    tool = WireTool(
        name="reserve",
        description="Reserve units.",
        parameters=WireParameters.model_validate(
            {
                "type": "object",
                "properties": {"sku": {"type": "string"}},
                "required": ["sku"],
                "additionalProperties": False,
            }
        ),
    )
    request = BridgeTaskCreateRequest(
        type="create",
        action_prompt="do it",
        deadline=datetime.now(timezone.utc) + timedelta(seconds=30),
        tools=[tool],
    )
    async for _ in transport.run_task("task-alias", request):
        pass

    import json

    serialized = json.dumps(captured["json"])
    assert '"additionalProperties"' in serialized  # by_alias form (correct)
    assert '"additional_properties"' not in serialized  # snake form the bridge would reject


@pytest.mark.unit
async def test_run_task_create_body_carries_policy_format_stream_attachments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real transport serializes every field of a typed create request."""
    from dualeai.events.http_transport import HTTPTransport
    from dualeai.models.response_format import JsonSchemaResponseFormat
    from dualeai.models.routing_policy import RoutingPolicy
    from dualeai.models.skill_enum import SkillEnum

    transport = HTTPTransport(endpoint="https://api.test.duale.ai", token="dualeai_test_token_padded_1234")
    captured: dict[str, object] = {}

    async def _spy(*, method: str, path: str, json_data: object, task_id: str, last_event_id: str | None = None):
        captured["json"] = json_data
        for _ in ():  # empty async generator, like the real method
            yield

    monkeypatch.setattr(transport, "_stream_request", _spy)

    request = BridgeTaskCreateRequest(
        type="create",
        action_prompt="do it",
        deadline=datetime.now(timezone.utc) + timedelta(seconds=30),
        routing_policy=RoutingPolicy(target_accuracy=0.9, required_skills=[SkillEnum.analysis]),
        response_format=JsonSchemaResponseFormat(json_schema={"type": "object"}),
        response_stream=True,
        attachments=[
            Attachment(
                key="attachment-key",
                filename="report.pdf",
                description="Quarterly report",
            )
        ],
    )
    async for _ in transport.run_task("task-wire-fields", request):
        pass

    body_raw = captured["json"]
    assert isinstance(body_raw, dict)
    body: dict[str, object] = {str(key): value for key, value in body_raw.items()}
    assert body["type"] == "create"
    assert body["response_stream"] is True
    assert body["routing_policy"] == {"target_accuracy": 0.9, "required_skills": ["analysis"]}
    assert body["response_format"] == {"json_schema": {"type": "object"}}
    assert body["attachments"] == [
        {"key": "attachment-key", "filename": "report.pdf", "description": "Quarterly report"}
    ]


@pytest.mark.unit
def test_config_hash_frozen_golden() -> None:
    """Freeze the exact SDK hash for a fixed registered-tool manifest."""
    from dualeai.events.client import CloudEventsClient
    from dualeai.lifecycle import LifecycleManager
    from dualeai.models.bridge import RegisteredTool
    from dualeai.models.tool import Parameters as WireParameters
    from dualeai.models.tool import Tool as WireTool

    async def _no_client() -> CloudEventsClient:
        raise AssertionError("config_hash must not need the events client")

    def _mk(name: str) -> RegisteredTool:
        return RegisteredTool(
            tool=WireTool(
                name=name,
                description=f"Run {name}.",
                parameters=WireParameters.model_validate(
                    {
                        "type": "object",
                        "properties": {"x": {"type": "string"}},
                        "required": ["x"],
                        "additionalProperties": False,
                    }
                ),
            ),
            timeout_seconds=10.0,
        )

    manifest = [_mk("alpha"), _mk("beta")]
    lifecycle = LifecycleManager(
        ensure_events_client=_no_client,
        manifest=lambda: manifest,
        agent_id="agent_x",
        auto_start=False,
    )
    assert lifecycle.config_hash() == "52782349f25a444e5a47f98c2cf1536ace538e7f2a50d9748f5119f1f4ccae41"

    # Order-invariance: a reversed manifest must hash identically because the SDK
    # treats tool registration order as irrelevant.
    reversed_lifecycle = LifecycleManager(
        ensure_events_client=_no_client,
        manifest=lambda: [_mk("beta"), _mk("alpha")],
        agent_id="agent_x",
        auto_start=False,
    )
    assert reversed_lifecycle.config_hash() == "52782349f25a444e5a47f98c2cf1536ace538e7f2a50d9748f5119f1f4ccae41"


@pytest.mark.unit
async def test_tool_exceeding_timeout_yields_error_and_cancels_callable(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """A tool that runs past its per-call timeout is cancelled and reports an error
    result, not a success. The deadline is far future so the TOOL timeout fires."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    await sdk._ensure_events_client()

    cancelled = {"seen": False}

    @tool(sdk=sdk, description="Slow tool.", timeout=timedelta(seconds=0.05))
    async def slow_tool(gate_id: str) -> dict[str, str]:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled["seen"] = True
            raise
        return {"gate_id": gate_id}

    tool_use = BridgeToolUseResponse(
        type="tool.use",
        timestamp=datetime.now(timezone.utc),
        tool_call_id="call-slow",
        name="slow_tool",
        input={"gate_id": "g1"},
        # Far-future deadline so the 0.05s tool timeout (not the deadline) fires.
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )
    mock_http_transport.inject_event("task_slow", mock_http_transport.create_task_completed_event())

    await sdk._execute_tool_use("task_slow", tool_use)

    result = _submitted_tool_result(mock_http_transport, "task_slow")
    assert isinstance(result, BridgeToolResultError)
    assert cancelled["seen"] is True  # wait_for cancelled the callable at its await


@pytest.mark.unit
async def test_tool_scalar_return_is_wrapped_as_result(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """A non-mapping JSON return wraps as {"result": value}."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    await sdk._ensure_events_client()

    @tool(sdk=sdk, description="Count gates.", timeout=timedelta(seconds=10))
    async def count_gates() -> int:
        return 3

    tool_use = BridgeToolUseResponse(
        type="tool.use",
        timestamp=datetime.now(timezone.utc),
        tool_call_id="call-scalar",
        name="count_gates",
        input={},
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=10),
    )
    mock_http_transport.inject_event("task_scalar", mock_http_transport.create_task_completed_event())

    await sdk._execute_tool_use("task_scalar", tool_use)

    result = _submitted_tool_result(mock_http_transport, "task_scalar")
    assert result.type == "success"
    assert isinstance(result, BridgeToolResultSuccess)
    assert result.output == {"result": 3}


@pytest.mark.unit
async def test_sync_tool_executes_via_thread_offload(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """A plain synchronous @tool runs in a worker thread."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    await sdk._ensure_events_client()

    @tool(sdk=sdk, description="Sync gate state.", timeout=timedelta(seconds=10))
    def gate_state(gate_id: str) -> dict[str, str]:
        return {"gate_id": gate_id, "state": "closed"}

    tool_use = BridgeToolUseResponse(
        type="tool.use",
        timestamp=datetime.now(timezone.utc),
        tool_call_id="call-sync",
        name="gate_state",
        input={"gate_id": "north"},
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=10),
    )
    mock_http_transport.inject_event("task_sync", mock_http_transport.create_task_completed_event())

    await sdk._execute_tool_use("task_sync", tool_use)

    result = _submitted_tool_result(mock_http_transport, "task_sync")
    assert result.type == "success"
    assert isinstance(result, BridgeToolResultSuccess)
    assert result.output == {"gate_id": "north", "state": "closed"}


@pytest.mark.unit
@pytest.mark.parametrize(
    "retries",
    [
        pytest.param(0, id="without-retries"),
        pytest.param(2, id="with-retries"),
    ],
)
async def test_expired_deadline_fast_fails_without_calling_tool(
    config_factory,
    mock_http_transport: MockHTTPTransport,
    retries: int,
) -> None:
    """An expired request deadline prevents invocation regardless of retry budget."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    await sdk._ensure_events_client()
    calls = 0

    @tool(
        sdk=sdk,
        description="Never runs.",
        timeout=timedelta(seconds=10),
        retries=retries,
    )
    async def close_security_gate(gate_id: str) -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {"gate_id": gate_id, "state": "closed"}

    task_id = f"task_expired_deadline_{retries}"
    tool_call_id = f"call-expired-deadline-{retries}"
    tool_use = BridgeToolUseResponse(
        type="tool.use",
        timestamp=datetime.now(timezone.utc),
        tool_call_id=tool_call_id,
        name="close_security_gate",
        input={"gate_id": "north"},
        deadline_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    mock_http_transport.inject_event(task_id, mock_http_transport.create_task_completed_event())

    await sdk._execute_tool_use(task_id, tool_use)

    assert calls == 0
    result = _submitted_tool_result(mock_http_transport, task_id)
    assert result.type == "error"
    assert isinstance(result, BridgeToolResultError)
    assert result.tool_call_id == tool_call_id
    assert "deadline already expired" in result.message


@pytest.mark.unit
async def test_tool_use_deadline_uses_server_clock_offset(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """Deadline enforcement uses the heartbeat-derived server clock estimate."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    await sdk._ensure_events_client()
    sdk._lifecycle._clock_offset = timedelta(minutes=1)
    calls = 0

    @tool(
        sdk=sdk,
        description="Close a named security gate and return the final gate state.",
        timeout=timedelta(seconds=10),
    )
    async def close_security_gate(gate_id: str) -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {"gate_id": gate_id, "state": "closed"}

    tool_use = BridgeToolUseResponse(
        type="tool.use",
        timestamp=datetime.now(timezone.utc),
        tool_call_id="call-server-expired-deadline",
        name="close_security_gate",
        input={"gate_id": "north"},
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )
    mock_http_transport.inject_event("task_server_expired_deadline", mock_http_transport.create_task_completed_event())

    await sdk._execute_tool_use("task_server_expired_deadline", tool_use)

    assert calls == 0
    result = _submitted_tool_result(mock_http_transport, "task_server_expired_deadline")
    assert result.type == "error"
    assert isinstance(result, BridgeToolResultError)
    assert "deadline already expired" in result.message


@pytest.mark.unit
async def test_registered_tool_use_with_null_deadline_posts_error_without_calling_tool(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """A registered tool.use with a null deadline becomes a typed error result."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    await sdk._ensure_events_client()
    calls = 0

    @tool(
        sdk=sdk,
        description="Close a named security gate and return the final gate state.",
        timeout=timedelta(seconds=10),
    )
    async def close_security_gate(gate_id: str) -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {"gate_id": gate_id, "state": "closed"}

    tool_use = BridgeToolUseResponse(
        type="tool.use",
        timestamp=datetime.now(timezone.utc),
        tool_call_id="call-missing-deadline",
        name="close_security_gate",
        input={"gate_id": "north"},
        deadline_at=None,
    )
    mock_http_transport.inject_event("task_missing_deadline", mock_http_transport.create_task_completed_event())

    await sdk._execute_tool_use("task_missing_deadline", tool_use)

    assert calls == 0
    result = _submitted_tool_result(mock_http_transport, "task_missing_deadline")
    assert result.type == "error"
    assert isinstance(result, BridgeToolResultError)
    assert result.tool_call_id == "call-missing-deadline"
    assert "has no deadline_at" in result.message


@pytest.mark.unit
async def test_duplicate_tool_use_delivery_does_not_reexecute_customer_tool(
    config_factory,
    mock_http_transport: MockHTTPTransport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay resubmits the cached result without repeating the customer side effect."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    calls = 0
    submit_attempts = 0
    both_submissions_finished = asyncio.Event()
    original_submit = mock_http_transport.submit_tool_results

    async def observe_submission(
        task_id: str,
        request: BridgeToolResultsRequest,
        *,
        last_event_id: str | None = None,
    ) -> AsyncIterator[BridgeSSEEvent]:
        nonlocal submit_attempts
        submit_attempts += 1
        try:
            async for event in original_submit(task_id, request, last_event_id=last_event_id):
                yield event
        finally:
            if submit_attempts == 2:
                both_submissions_finished.set()

    monkeypatch.setattr(mock_http_transport, "submit_tool_results", observe_submission)

    @tool(
        sdk=sdk,
        description="Close a named security gate and return the final gate state.",
        timeout=timedelta(seconds=10),
    )
    async def close_security_gate(gate_id: str, reason: str) -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {"gate_id": gate_id, "state": "closed", "reason": reason}

    tool_event = mock_http_transport.create_tool_use_event(
        tool_call_id="call_replayed",
        name="close_security_gate",
        tool_input={"gate_id": "north", "reason": "drill"},
    )
    mock_http_transport.inject_events(
        "task_replay",
        [tool_event, tool_event, mock_http_transport.create_task_completed_event()],
    )

    response = await ask(action="Close the security gate", request_id="task_replay", sdk=sdk)
    await response.model()
    await asyncio.wait_for(both_submissions_finished.wait(), timeout=1)

    assert calls == 1
    assert submit_attempts == 2


@pytest.mark.unit
async def test_per_task_tool_use_is_not_auto_executed_as_registered_tool(
    config_factory,
    mock_http_transport: MockHTTPTransport,
) -> None:
    """Per-task tool.use events remain observable but are not executed by registered-tool serving."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)

    tool_event = mock_http_transport.create_tool_use_event(
        tool_call_id="call_per_task",
        name="per_task_lookup",
        tool_input={"query": "status"},
    )
    assert isinstance(tool_event.data, BridgeToolUseResponse)
    mock_http_transport.inject_event("task_per_task", tool_event)
    mock_http_transport.inject_event("task_per_task", mock_http_transport.create_task_completed_event())

    response = await ask(
        action="Use the per-task lookup",
        streaming=True,
        request_id="task_per_task",
        sdk=sdk,
    )
    await response.model()

    assert len(mock_http_transport.get_requests_for_task("task_per_task")) == 1


@pytest.mark.unit
async def test_tool_results_submission_resumes_after_triggering_tool_use_event(
    config_factory,
    mock_http_transport: MockHTTPTransport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """tool_results POST resumes after the tool.use event that triggered it."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    calls = 0
    submission_finished = asyncio.Event()
    original_submit = mock_http_transport.submit_tool_results

    async def observe_submission(
        task_id: str,
        request: BridgeToolResultsRequest,
        *,
        last_event_id: str | None = None,
    ) -> AsyncIterator[BridgeSSEEvent]:
        try:
            async for event in original_submit(task_id, request, last_event_id=last_event_id):
                yield event
        finally:
            submission_finished.set()

    monkeypatch.setattr(mock_http_transport, "submit_tool_results", observe_submission)

    @tool(
        sdk=sdk,
        description="Close a named security gate and return the final gate state.",
        timeout=timedelta(seconds=10),
    )
    async def close_security_gate(gate_id: str, reason: str) -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {"gate_id": gate_id, "state": "closed", "reason": reason}

    tool_event = mock_http_transport.create_tool_use_event(
        tool_call_id="call_replayed_after_result",
        name="close_security_gate",
        tool_input={"gate_id": "north", "reason": "drill"},
    )
    assert isinstance(tool_event.data, BridgeToolUseResponse)
    mock_http_transport.inject_tool_result_event(
        "task_replay_after_result",
        mock_http_transport.create_task_completed_event(),
    )
    mock_http_transport.inject_event("task_replay_after_result", tool_event)
    mock_http_transport.inject_event(
        "task_replay_after_result",
        mock_http_transport.create_task_completed_event(),
    )

    response = await ask(
        action="Close the security gate",
        request_id="task_replay_after_result",
        sdk=sdk,
    )
    await response.model()
    await asyncio.wait_for(submission_finished.wait(), timeout=1)

    requests = mock_http_transport.get_requests_for_task("task_replay_after_result")
    assert calls == 1
    assert len(requests) == 2
    tool_result_request = requests[1]
    assert tool_result_request["last_event_id"] == tool_event.id
    body = tool_result_request["request"]
    assert isinstance(body, BridgeToolResultsRequest)
    assert body.tool_results[0].tool_call_id == "call_replayed_after_result"


@pytest.mark.unit
async def test_continuation_tool_use_and_results_stay_keyed_to_public_child(
    config_factory,
    mock_http_transport: MockHTTPTransport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A continuation's tool callback posts to its child ID, never its parent."""
    config: DualeAIConfig = config_factory(agent_id="agent_security_operations")
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)
    submission_finished = asyncio.Event()
    original_submit = mock_http_transport.submit_tool_results

    async def observe_submission(
        task_id: str,
        request: BridgeToolResultsRequest,
        *,
        last_event_id: str | None = None,
    ) -> AsyncIterator[BridgeSSEEvent]:
        try:
            async for event in original_submit(task_id, request, last_event_id=last_event_id):
                yield event
        finally:
            submission_finished.set()

    monkeypatch.setattr(mock_http_transport, "submit_tool_results", observe_submission)

    @tool(
        sdk=sdk,
        description="Close a named security gate and return the final gate state.",
        timeout=timedelta(seconds=10),
    )
    async def close_security_gate(gate_id: str) -> dict[str, str]:
        return {"gate_id": gate_id, "state": "closed"}

    parent_task_id = "parent-public-task"
    parent = await ask(action="Start", request_id=parent_task_id, sdk=sdk)
    mock_http_transport.inject_event(parent_task_id, mock_http_transport.create_task_completed_event())
    await parent.model()
    response = await parent.next(message="Close the gate", deadline=datetime.now(timezone.utc) + timedelta(minutes=1))
    child_task_id = response.task_id
    tool_event = mock_http_transport.create_tool_use_event(
        tool_call_id="call_continuation_child",
        name="close_security_gate",
        tool_input={"gate_id": "north"},
    )
    mock_http_transport.inject_events(
        child_task_id,
        [tool_event, mock_http_transport.create_task_completed_event()],
    )

    await response.model()
    await asyncio.wait_for(submission_finished.wait(), timeout=1)

    child_requests = mock_http_transport.get_requests_for_task(child_task_id)
    assert len(child_requests) == 2
    continuation_request = child_requests[0]["request"]
    assert isinstance(continuation_request, BridgeTaskContinueRequest)
    assert continuation_request.parent_task_id == parent_task_id
    assert child_requests[1]["last_event_id"] == tool_event.id
    result_body = child_requests[1]["request"]
    assert isinstance(result_body, BridgeToolResultsRequest)
    assert result_body.tool_results[0].tool_call_id == "call_continuation_child"
    parent_requests = mock_http_transport.get_requests_for_task(parent_task_id)
    assert len(parent_requests) == 1
    assert all(not isinstance(record["request"], BridgeToolResultsRequest) for record in parent_requests)
