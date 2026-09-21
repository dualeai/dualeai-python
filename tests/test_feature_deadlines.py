"""Priority 9 tests for deadline/timeout handling feature.

Tests deadline parameter propagation and timeout handling with REAL SDK (minimal mocking).

CRITICAL RULES:
- Use minimal_mock_sdk for tests that exercise REAL SDK code paths
- Check HTTP requests through the typed mock transport helper
- All tests use @pytest.mark.unit and TestUnit* class naming
"""

from datetime import datetime, timedelta, timezone

import pytest

from dualeai import DualeAISDK
from dualeai.constants import TimingDefaults
from dualeai.models.bridge import BridgeTaskContinueRequest, BridgeTaskCreateRequest
from dualeai.models.skill_enum import SkillEnum
from dualeai.orchestrator import ask, continue_conversation
from tests.mocks.mock_http import require_mock_http_transport


@pytest.mark.unit
class TestUnitDeadlineHandling:
    """Test deadline/timeout functionality with REAL SDK (minimal mocking)."""

    @pytest.mark.parametrize(
        "deadline",
        [
            datetime(2025, 1, 15, 10, 15, tzinfo=timezone.utc),
            datetime(2020, 1, 1, tzinfo=timezone.utc),
            datetime(2126, 1, 1, tzinfo=timezone.utc),
            datetime(2025, 1, 15, 10, 5, 30, 123456, tzinfo=timezone.utc),
        ],
        ids=["normal", "past", "far-future", "microseconds"],
    )
    async def test_deadline_reaches_typed_create_request(
        self,
        minimal_mock_sdk: DualeAISDK,
        deadline: datetime,
    ) -> None:
        """Preserve every supported deadline shape at the transport boundary."""
        await ask(action="Task with deadline", skills=[SkillEnum.analysis], deadline=deadline, sdk=minimal_mock_sdk)

        [record] = require_mock_http_transport(minimal_mock_sdk).get_requests()
        request = record["request"]
        assert isinstance(request, BridgeTaskCreateRequest)
        assert request.deadline == deadline

    async def test_default_deadline_uses_platform_constant(self):
        """Test TimingDefaults.DEFAULT_TASK_TIMEOUT_SECONDS constant exists."""
        assert TimingDefaults.DEFAULT_TASK_TIMEOUT_SECONDS == 1800  # 30 minutes

    async def test_continue_task_with_deadline(self, minimal_mock_sdk: DualeAISDK):
        """Test continue_task accepts and propagates deadline parameter."""
        sdk = minimal_mock_sdk
        deadline = datetime(2025, 1, 15, 10, 20, 0, tzinfo=timezone.utc)
        parent = await ask(action="Start", request_id="original-task-123", sdk=sdk)
        transport = require_mock_http_transport(sdk)
        transport.inject_event(parent.task_id, transport.create_task_completed_event())
        await parent.task

        # Continue task with deadline
        await continue_conversation(
            response=parent,
            message="Continue the task",
            deadline=deadline,
        )

        # Verify HTTP request was made
        requests = require_mock_http_transport(sdk).get_requests()
        assert len(requests) >= 1

        # Verify deadline in request body (must be propagated, never silently dropped)
        continuation_request = requests[-1]["request"]
        assert isinstance(continuation_request, BridgeTaskContinueRequest)
        assert continuation_request.deadline == deadline

    async def test_multiple_tasks_independent_deadlines(self, minimal_mock_sdk: DualeAISDK):
        """Test multiple tasks can have independent deadlines using same SDK."""
        sdk = minimal_mock_sdk
        deadline1 = datetime.now(timezone.utc) + timedelta(minutes=5)
        deadline2 = datetime.now(timezone.utc) + timedelta(minutes=10)

        # Submit first task
        await ask(
            action="Task 1",
            skills=[],
            deadline=deadline1,
            sdk=sdk,
        )

        # Submit second task with different deadline
        await ask(
            action="Task 2",
            skills=[],
            deadline=deadline2,
            sdk=sdk,
        )

        records = require_mock_http_transport(sdk).get_requests()
        requests = [record["request"] for record in records]
        assert all(isinstance(request, BridgeTaskCreateRequest) for request in requests)
        assert [request.deadline for request in requests if isinstance(request, BridgeTaskCreateRequest)] == [
            deadline1,
            deadline2,
        ]

    async def test_none_deadline_accepted(self, minimal_mock_sdk: DualeAISDK):
        """Test ask() accepts None for deadline parameter."""
        sdk = minimal_mock_sdk

        await ask(
            action="Task without explicit deadline",
            skills=[],
            deadline=None,
            sdk=sdk,
        )

        # Verify HTTP request was made
        requests = require_mock_http_transport(sdk).get_requests()
        assert len(requests) >= 1

        request = requests[0]["request"]
        assert isinstance(request, BridgeTaskCreateRequest)
        assert requests[0]["task_id"] is not None
        assert request.deadline is not None
