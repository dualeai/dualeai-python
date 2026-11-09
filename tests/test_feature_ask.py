"""Tests for the ask() function.

Tests ask() orchestration with the real SDK and a transport boundary double.

Architecture:
- Uses minimal_mock_sdk fixture which creates REAL DualeAISDK
- Only HTTP transport is replaced (network boundary)
- REAL: submit_task, CloudEventsClient, circuit breaker, AgentResponse
"""

import asyncio
from uuid import UUID

import pytest
from pydantic import ValidationError

from dualeai import DualeAISDK
from dualeai.models.bridge import BridgeTaskCreateRequest
from dualeai.models.capability import Capability
from dualeai.orchestrator import ask
from dualeai.response import AgentResponse
from tests.mocks.mock_http import require_mock_http_transport


@pytest.mark.unit
class TestUnitAskFunction:
    """Test ``ask()`` with only the HTTP transport boundary replaced."""

    async def test_ask_returns_agent_response(self, minimal_mock_sdk: DualeAISDK):
        """Test ask() returns AgentResponse with valid task_id."""
        response = await ask(
            instruction="Extract invoice data",
            sdk=minimal_mock_sdk,
        )

        # Verify response type
        assert isinstance(response, AgentResponse)
        assert response.task_id is not None
        assert response.sdk is minimal_mock_sdk

        assert isinstance(response.task, asyncio.Task)
        assert not response.task.done()

        # Verify the transport received a Task call.
        assert len(require_mock_http_transport(minimal_mock_sdk).get_requests()) > 0

    async def test_ask_refuses_an_empty_instruction_unsent_and_sends_a_whitespace_one_unchanged(
        self, minimal_mock_sdk: DualeAISDK
    ):
        """ask() applies the public Task instruction rule and no stricter one.

        An empty instruction is refused before anything is sent; a whitespace-only instruction is non-empty text,
        which the rule admits.
        """
        transport = require_mock_http_transport(minimal_mock_sdk)
        with pytest.raises(ValidationError) as error:
            await ask(instruction="", sdk=minimal_mock_sdk)
        assert [item["loc"] for item in error.value.errors()] == [("instruction",)]
        assert transport.get_requests() == []

        await ask(instruction="   ", sdk=minimal_mock_sdk)

        task_request = transport.get_requests()[-1]["request"]
        assert isinstance(task_request, BridgeTaskCreateRequest)
        assert task_request.instruction == "   "

    async def test_ask_passes_the_instruction_to_the_task_transport_unchanged(self, minimal_mock_sdk: DualeAISDK):
        """The Task request passed to the transport carries the instruction unchanged."""
        instruction = "Process document with OCR"
        await ask(instruction=instruction, sdk=minimal_mock_sdk)

        [record] = require_mock_http_transport(minimal_mock_sdk).get_requests()
        task_request = record["request"]
        assert isinstance(task_request, BridgeTaskCreateRequest)
        assert task_request.instruction == instruction

    async def test_ask_includes_capabilities_in_request(self, minimal_mock_sdk: DualeAISDK):
        """The Task request passed to the transport carries the selected capabilities."""
        capabilities = [Capability.agentic, Capability.long_context]
        await ask(instruction="Extract and analyze", capabilities=capabilities, sdk=minimal_mock_sdk)

        requests = require_mock_http_transport(minimal_mock_sdk).get_requests()
        assert len(requests) > 0
        task_request = requests[0]["request"]
        assert isinstance(task_request, BridgeTaskCreateRequest)
        assert task_request.routing_policy is not None
        assert task_request.routing_policy.required_capabilities == [
            Capability.agentic,
            Capability.long_context,
        ]

    async def test_ask_with_streaming_flag(self, minimal_mock_sdk: DualeAISDK):
        """Test ask() with streaming=True sets up streaming."""
        response = await ask(
            instruction="Generate report",
            streaming=True,
            sdk=minimal_mock_sdk,
        )

        # Verify response has streaming enabled
        assert response.streaming is True

        # Verify the transport received a Task call.
        assert len(require_mock_http_transport(minimal_mock_sdk).get_requests()) > 0

    async def test_ask_exposes_task_runner(self, minimal_mock_sdk: DualeAISDK):
        """The public response exposes its in-flight asyncio task."""
        response = await ask(
            instruction="Create pending task",
            sdk=minimal_mock_sdk,
        )

        assert isinstance(response.task, asyncio.Task)
        assert not response.task.done()

    async def test_ask_with_multiple_capabilities(self, minimal_mock_sdk: DualeAISDK):
        """Test ask() with multiple capabilities includes all in payload."""
        capabilities = [
            Capability.agentic,
            Capability.long_context,
            Capability.problem_solving,
            Capability.math,
        ]

        await ask(
            instruction="Complex multi-capability task",
            capabilities=capabilities,
            sdk=minimal_mock_sdk,
        )

        requests = require_mock_http_transport(minimal_mock_sdk).get_requests()
        assert len(requests) > 0
        task_request = requests[0]["request"]
        assert isinstance(task_request, BridgeTaskCreateRequest)
        assert task_request.routing_policy is not None
        assert task_request.routing_policy.required_capabilities == [
            Capability.agentic,
            Capability.long_context,
            Capability.problem_solving,
            Capability.math,
        ]

    async def test_ask_task_id_uniqueness(self, minimal_mock_sdk: DualeAISDK):
        """Test ask() generates unique task IDs for each call."""
        # Create multiple tasks with the same SDK
        response1 = await ask(instruction="Task 1", sdk=minimal_mock_sdk)
        response2 = await ask(instruction="Task 2", sdk=minimal_mock_sdk)
        response3 = await ask(instruction="Task 3", sdk=minimal_mock_sdk)

        # Verify all task IDs are unique client-generated UUIDs.
        task_ids = {response1.task_id, response2.task_id, response3.task_id}
        assert len(task_ids) == 3

    async def test_ask_task_id_names_the_submitted_task(self, minimal_mock_sdk: DualeAISDK):
        """A caller-selected ``task_id`` names the root Task on the response and in the submitted body."""
        response = await ask(instruction="Summarize the report", task_id="task-root-0001", sdk=minimal_mock_sdk)

        record = require_mock_http_transport(minimal_mock_sdk).get_requests()[-1]
        assert isinstance(record["request"], BridgeTaskCreateRequest)
        assert record["request"].task_id == "task-root-0001"
        assert response.task_id == "task-root-0001"

    async def test_ask_without_task_id_names_the_task_by_a_new_uuid4(self, minimal_mock_sdk: DualeAISDK):
        """An omitted ``task_id`` names the root Task by a UUID4 that the SDK generates before submission."""
        response = await ask(instruction="Summarize the report", sdk=minimal_mock_sdk)

        task_request = require_mock_http_transport(minimal_mock_sdk).get_requests()[-1]["request"]
        assert isinstance(task_request, BridgeTaskCreateRequest)
        assert UUID(response.task_id).version == 4
        assert task_request.task_id == response.task_id

    async def test_ask_streaming_false_by_default(self, minimal_mock_sdk: DualeAISDK):
        """Test ask() defaults to streaming=False."""
        response = await ask(
            instruction="Non-streaming task",
            sdk=minimal_mock_sdk,
        )

        # Verify streaming is False
        assert response.streaming is False

        # Verify the transport received a Task call.
        assert len(require_mock_http_transport(minimal_mock_sdk).get_requests()) > 0
