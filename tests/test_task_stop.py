"""Task stop requests and independently supplied stopped terminal events."""

from datetime import datetime, timezone

import pytest

from dualeai import BridgeTaskStoppedResponse, DualeAISDK, TaskStoppedError
from dualeai.models.bridge import BridgeSSEEvent
from dualeai.models.task_stop import TaskStopRequest
from tests.mocks.mock_http import require_mock_http_transport


def _stopped_event(reason: str) -> BridgeSSEEvent:
    """One terminal stop event, shaped as the bridge sends it."""
    return BridgeSSEEvent(
        id="1:1",
        timestamp=datetime.now(timezone.utc),
        data=BridgeTaskStoppedResponse(
            type="task.stopped",
            timestamp=datetime.now(timezone.utc),
            reason=reason,
        ),
    )


@pytest.mark.unit
async def test_stop_task_posts_the_reason(minimal_mock_sdk: DualeAISDK) -> None:
    """The caller sends one field, and the platform acknowledges the request."""
    accepted = await minimal_mock_sdk.stop_task("task-stop-me-123456", "Wrong document supplied")

    assert accepted.task_id == "task-stop-me-123456"
    request = require_mock_http_transport(minimal_mock_sdk).get_requests()[-1]
    assert request["operation"] == "stop_task"
    assert request["task_id"] == "task-stop-me-123456"
    assert isinstance(request["request"], TaskStopRequest)
    assert request["request"].reason == "Wrong document supplied"


@pytest.mark.unit
async def test_stop_task_rejects_an_empty_reason(minimal_mock_sdk: DualeAISDK) -> None:
    """A stop without a reason would reach the user as an empty explanation."""
    with pytest.raises(ValueError, match="Stop reason cannot be empty"):
        await minimal_mock_sdk.stop_task("task-stop-me-123456", "   ")


@pytest.mark.unit
async def test_an_open_stream_ends_on_the_stop_and_raises_with_its_reason(
    minimal_mock_sdk: DualeAISDK,
) -> None:
    """A stopped task is a third terminal arm, not a transport failure."""
    from dualeai import ask

    task_id = "task-open-stream-1234"
    transport = require_mock_http_transport(minimal_mock_sdk)
    transport.inject_event(task_id, _stopped_event("Cost ceiling reached"))

    response = await ask("Start something long", request_id=task_id, sdk=minimal_mock_sdk)

    with pytest.raises(TaskStoppedError) as stopped:
        await response.model()
    assert stopped.value.reason == "Cost ceiling reached"


@pytest.mark.unit
async def test_response_stop_is_the_same_call_without_the_task_id(minimal_mock_sdk: DualeAISDK) -> None:
    """The shortcut saves the caller from repeating an identifier it already holds."""
    from dualeai import ask

    response = await ask("Start something long", request_id="task-shortcut-123456", sdk=minimal_mock_sdk)

    accepted = await response.stop(reason="Wrong document supplied")

    assert accepted.task_id == response.task_id
    request = require_mock_http_transport(minimal_mock_sdk).get_requests()[-1]
    assert request["task_id"] == response.task_id
    assert isinstance(request["request"], TaskStopRequest)
    assert request["request"].reason == "Wrong document supplied"


@pytest.mark.unit
async def test_response_stop_rejects_an_empty_reason(minimal_mock_sdk: DualeAISDK) -> None:
    """The shortcut enforces the same contract as the client method."""
    from dualeai import ask

    response = await ask("Start something long", request_id="task-shortcut-654321", sdk=minimal_mock_sdk)

    with pytest.raises(ValueError, match="Stop reason cannot be empty"):
        await response.stop(reason="")
