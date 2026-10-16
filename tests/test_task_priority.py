"""Task priority belongs to each submission, independently of its parent or result format."""

import pytest
from pydantic import BaseModel

from dualeai import DualeAISDK, PriorityClass, ask
from dualeai._wire import dump_wire_model
from dualeai.models.bridge import BridgeTaskContinueRequest, BridgeTaskCreateRequest
from tests.mocks.mock_http import require_mock_http_transport


class Summary(BaseModel):
    text: str


@pytest.mark.unit
@pytest.mark.parametrize("facade", ["ask", "submit_task"])
async def test_root_submission_defaults_to_standard(minimal_mock_sdk: DualeAISDK, facade: str) -> None:
    if facade == "ask":
        await ask("Summarize", sdk=minimal_mock_sdk)
    else:
        await minimal_mock_sdk.submit_task("Summarize", [])

    request = require_mock_http_transport(minimal_mock_sdk).get_requests()[0]["request"]
    assert isinstance(request, BridgeTaskCreateRequest)
    assert dump_wire_model(request)["priority"] == "standard"


@pytest.mark.unit
@pytest.mark.parametrize("facade", ["ask", "submit_task"])
@pytest.mark.parametrize("response_type", [None, Summary])
async def test_fast_priority_reaches_the_request_for_text_and_structured_results(
    minimal_mock_sdk: DualeAISDK, facade: str, response_type: type[Summary] | None
) -> None:
    if facade == "ask":
        await ask("Summarize", res=response_type, priority=PriorityClass.fast, sdk=minimal_mock_sdk)
    else:
        await minimal_mock_sdk.submit_task("Summarize", [], response_type=response_type, priority=PriorityClass.fast)

    request = require_mock_http_transport(minimal_mock_sdk).get_requests()[0]["request"]
    assert isinstance(request, BridgeTaskCreateRequest)
    body = dump_wire_model(request)
    assert body["priority"] == "fast"
    policy = body["routing_policy"]
    assert isinstance(policy, dict)
    assert "priority" not in policy
    assert (request.response_format is not None) is (response_type is not None)


@pytest.mark.unit
@pytest.mark.parametrize("parent_priority", [PriorityClass.standard, PriorityClass.fast])
async def test_continuation_defaults_to_standard_independently_of_parent_priority(
    minimal_mock_sdk: DualeAISDK, parent_priority: PriorityClass
) -> None:
    parent = await ask("Summarize", priority=parent_priority, sdk=minimal_mock_sdk)

    await parent.next("Shorten the summary")

    request = require_mock_http_transport(minimal_mock_sdk).get_requests()[-1]["request"]
    assert isinstance(request, BridgeTaskContinueRequest)
    assert request.parent_task_id == parent.task_id
    assert dump_wire_model(request)["priority"] == "standard"


@pytest.mark.unit
async def test_continuation_can_select_fast_after_a_standard_parent(
    minimal_mock_sdk: DualeAISDK,
) -> None:
    parent = await ask("Summarize", sdk=minimal_mock_sdk)

    child = await parent.next("Shorten the summary", priority=PriorityClass.fast)

    transport = require_mock_http_transport(minimal_mock_sdk)
    request = transport.get_requests()[-1]["request"]
    assert isinstance(request, BridgeTaskContinueRequest)
    assert dump_wire_model(request)["priority"] == "fast"
    transport.inject_event(child.task_id, transport.create_task_completed_event())
    await child.task
