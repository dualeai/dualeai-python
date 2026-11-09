"""Public SDK continuation contract and boundary tests."""

from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError

from dualeai import DualeAISDK
from dualeai.models.bridge import BridgeTaskContinueRequest
from dualeai.models.response_format import JsonSchemaResponseFormat, PredefinedResponseFormat
from dualeai.orchestrator import ask
from dualeai.response import AgentResponse
from tests.mocks.mock_http import require_mock_http_transport

DEADLINE = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)


class Answer(BaseModel):
    """Typed continuation result used to verify schema derivation."""

    value: str


async def _completed_parent(sdk: DualeAISDK, task_id: str = "parent-task-123") -> AgentResponse[object]:
    """Submit and complete one public parent through the real SDK path."""
    response = await ask("Start the task", task_id=task_id, sdk=sdk)
    transport = require_mock_http_transport(sdk)
    transport.inject_event(response.task_id, transport.create_task_completed_event())
    await response.task
    return response


@pytest.mark.unit
async def test_a_response_has_no_operator_that_continues_it(minimal_mock_sdk: DualeAISDK) -> None:
    parent = await _completed_parent(minimal_mock_sdk)
    with pytest.raises(TypeError):
        _ = parent | parent  # ty: ignore[unsupported-operator] - the refused operator is the contract


@pytest.mark.unit
async def test_next_builds_child_keyed_request(minimal_mock_sdk: DualeAISDK) -> None:
    parent = await _completed_parent(minimal_mock_sdk, "parent-task_123")

    response = await parent.next(
        instruction="Continue with é漢🙂",
        deadline=DEADLINE,
    )

    continuation = require_mock_http_transport(minimal_mock_sdk).get_requests()[-1]["request"]
    assert isinstance(continuation, BridgeTaskContinueRequest)
    assert UUID(response.task_id).version == 4
    assert continuation.task_id == response.task_id
    assert continuation.parent_task_id == "parent-task_123"
    assert continuation.instruction == "Continue with é漢🙂"
    assert continuation.deadline == DEADLINE
    assert continuation.response_format is None


@pytest.mark.unit
async def test_next_task_id_names_the_continuation_task(minimal_mock_sdk: DualeAISDK) -> None:
    """A caller-selected ``task_id`` names the continuation Task; the parent keeps its own task ID."""
    parent = await _completed_parent(minimal_mock_sdk, "parent-task-123")

    child = await parent.next(instruction="Continue", deadline=DEADLINE, task_id="task-child-0002")

    continuation = require_mock_http_transport(minimal_mock_sdk).get_requests()[-1]["request"]
    assert isinstance(continuation, BridgeTaskContinueRequest)
    assert continuation.task_id == "task-child-0002"
    assert child.task_id == "task-child-0002"
    assert continuation.parent_task_id == "parent-task-123"
    assert parent.task_id == "parent-task-123"


@pytest.mark.unit
async def test_next_sends_explicit_response_format(minimal_mock_sdk: DualeAISDK) -> None:
    parent = await _completed_parent(minimal_mock_sdk)

    await parent.next(
        instruction="Return JSON",
        deadline=DEADLINE,
        response_format=PredefinedResponseFormat.json,
    )

    request = require_mock_http_transport(minimal_mock_sdk).get_requests()[-1]["request"]
    assert isinstance(request, BridgeTaskContinueRequest)
    assert request.response_format is PredefinedResponseFormat.json


@pytest.mark.unit
async def test_next_derives_response_format_from_response_type(minimal_mock_sdk: DualeAISDK) -> None:
    parent = await _completed_parent(minimal_mock_sdk)

    await parent.next(
        instruction="Return typed JSON",
        deadline=DEADLINE,
        res=Answer,
    )

    request = require_mock_http_transport(minimal_mock_sdk).get_requests()[-1]["request"]
    assert isinstance(request, BridgeTaskContinueRequest)
    assert isinstance(request.response_format, JsonSchemaResponseFormat)
    assert request.response_format.json_schema["required"] == ["value"]


@pytest.mark.unit
async def test_next_refuses_an_empty_instruction_before_sending_a_child(minimal_mock_sdk: DualeAISDK) -> None:
    """A continuation follows the root Task's instruction rule: an empty instruction is refused, and nothing is sent."""
    parent = await _completed_parent(minimal_mock_sdk)
    transport = require_mock_http_transport(minimal_mock_sdk)
    requests_before = len(transport.get_requests())

    with pytest.raises(ValidationError) as error:
        await parent.next(instruction="", deadline=DEADLINE)

    assert [item["loc"] for item in error.value.errors()] == [("instruction",)]
    assert len(transport.get_requests()) == requests_before


@pytest.mark.unit
async def test_next_rejects_naive_deadline_without_child_request(minimal_mock_sdk: DualeAISDK) -> None:
    parent = await _completed_parent(minimal_mock_sdk)
    transport = require_mock_http_transport(minimal_mock_sdk)
    requests_before = len(transport.get_requests())

    with pytest.raises(ValueError, match="deadline must be timezone-aware"):
        await parent.next(
            instruction="Continue",
            deadline=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc).replace(tzinfo=None),
        )

    assert len(transport.get_requests()) == requests_before
