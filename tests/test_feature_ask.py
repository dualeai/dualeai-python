"""Priority 1 tests for ask() function.

Tests ask() orchestration with the real SDK and a transport boundary double.

Architecture:
- Uses minimal_mock_sdk fixture which creates REAL DualeAISDK
- Only HTTP transport is replaced (network boundary)
- REAL: submit_task, CloudEventsClient, circuit breaker, AgentResponse
"""

import asyncio

import pytest

from dualeai import DualeAISDK
from dualeai.models.skill_enum import SkillEnum
from dualeai.orchestrator import ask
from dualeai.response import AgentResponse
from tests.mocks.mock_http import require_mock_http_transport


@pytest.mark.unit
class TestUnitAskFunction:
    """Test ``ask()`` with only the HTTP transport boundary replaced."""

    async def test_ask_returns_agent_response(self, minimal_mock_sdk: DualeAISDK):
        """Test ask() returns AgentResponse with valid task_id."""
        response = await ask(
            action="Extract invoice data",
            sdk=minimal_mock_sdk,
        )

        # Verify response type
        assert isinstance(response, AgentResponse)
        assert response.task_id is not None
        assert response.sdk is minimal_mock_sdk

        assert isinstance(response.task, asyncio.Task)
        assert not response.task.done()

        # Verify HTTP request was made (network boundary)
        assert len(require_mock_http_transport(minimal_mock_sdk).get_requests()) > 0

    async def test_ask_validates_empty_action(self, minimal_mock_sdk: DualeAISDK):
        """Test ask() rejects empty action parameter."""
        # Empty action should raise ValueError
        with pytest.raises(ValueError, match="Action cannot be empty"):
            await ask(action="", sdk=minimal_mock_sdk)

        # Whitespace-only action should raise ValueError
        with pytest.raises(ValueError, match="Action cannot be empty"):
            await ask(action="   ", sdk=minimal_mock_sdk)

        # Verify HTTP request was not made (validation happened first)
        assert len(require_mock_http_transport(minimal_mock_sdk).get_requests()) == 0

    async def test_ask_sends_action_prompt_in_http_request(self, minimal_mock_sdk: DualeAISDK):
        """The Task request body carries the action as ``action_prompt``."""
        action = "Process document with OCR"
        await ask(action=action, sdk=minimal_mock_sdk)

        # Verify HTTP request was made
        requests = require_mock_http_transport(minimal_mock_sdk).get_requests()
        assert len(requests) > 0

        # action_prompt is the exact action text in the wire body
        assert requests[0]["body"]["action_prompt"] == action

    async def test_ask_includes_skills_in_request(self, minimal_mock_sdk: DualeAISDK):
        """Skills are serialized in the HTTP Task request's routing policy."""
        skills = [SkillEnum.instruction_following, SkillEnum.analysis]
        await ask(action="Extract and analyze", skills=skills, sdk=minimal_mock_sdk)

        # Verify HTTP request was made
        requests = require_mock_http_transport(minimal_mock_sdk).get_requests()
        assert len(requests) > 0

        # Skills land in routing_policy.required_skills as exact wire values
        # (no target_accuracy/priority_level/etc — ask() builds a bare
        # RoutingPolicy(required_skills=skills), so exclude_unset drops the rest).
        body = requests[0]["body"]
        assert body["routing_policy"] == {
            "required_skills": ["instruction_following", "analysis"],
        }

    async def test_ask_with_streaming_flag(self, minimal_mock_sdk: DualeAISDK):
        """Test ask() with streaming=True sets up streaming."""
        response = await ask(
            action="Generate report",
            streaming=True,
            sdk=minimal_mock_sdk,
        )

        # Verify response has streaming enabled
        assert response.streaming is True

        # Verify HTTP request was made
        assert len(require_mock_http_transport(minimal_mock_sdk).get_requests()) > 0

    async def test_ask_exposes_task_runner(self, minimal_mock_sdk: DualeAISDK):
        """The public response exposes its in-flight asyncio task."""
        response = await ask(
            action="Create pending task",
            sdk=minimal_mock_sdk,
        )

        assert isinstance(response.task, asyncio.Task)
        assert not response.task.done()

    async def test_ask_with_multiple_skills(self, minimal_mock_sdk: DualeAISDK):
        """Test ask() with multiple skills includes all in payload."""
        skills = [
            SkillEnum.instruction_following,
            SkillEnum.analysis,
            SkillEnum.reasoning,
            SkillEnum.general,
        ]

        await ask(
            action="Complex multi-skill task",
            skills=skills,
            sdk=minimal_mock_sdk,
        )

        # Verify HTTP request was made with the exact skill list in routing_policy
        requests = require_mock_http_transport(minimal_mock_sdk).get_requests()
        assert len(requests) > 0
        assert requests[0]["body"]["routing_policy"] == {
            "required_skills": [
                "instruction_following",
                "analysis",
                "reasoning",
                "general",
            ],
        }

    async def test_ask_task_id_uniqueness(self, minimal_mock_sdk: DualeAISDK):
        """Test ask() generates unique task IDs for each call."""
        # Create multiple tasks with the same SDK
        response1 = await ask(action="Task 1", sdk=minimal_mock_sdk)
        response2 = await ask(action="Task 2", sdk=minimal_mock_sdk)
        response3 = await ask(action="Task 3", sdk=minimal_mock_sdk)

        # Verify all task IDs are unique client-generated UUIDs.
        task_ids = {response1.task_id, response2.task_id, response3.task_id}
        assert len(task_ids) == 3

    async def test_ask_streaming_false_by_default(self, minimal_mock_sdk: DualeAISDK):
        """Test ask() defaults to streaming=False."""
        response = await ask(
            action="Non-streaming task",
            sdk=minimal_mock_sdk,
        )

        # Verify streaming is False
        assert response.streaming is False

        # Verify HTTP request was made
        assert len(require_mock_http_transport(minimal_mock_sdk).get_requests()) > 0
