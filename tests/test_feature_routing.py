"""Priority 8 tests for routing policy feature.

Tests routing policy configuration with REAL SDK (HTTP boundary mocking only).

Architecture:
- Uses minimal_mock_sdk fixture which creates REAL DualeAISDK
- Only HTTP transport is mocked (network boundary)
- REAL: submit_task, CloudEventsClient, circuit breaker, AgentResponse
"""

import pytest

from dualeai import DualeAISDK
from dualeai.models.routing_policy import RoutingPolicy
from dualeai.models.skill_enum import SkillEnum
from dualeai.orchestrator import ask
from tests.mocks.mock_http import require_mock_http_transport


@pytest.mark.unit
class TestUnitRoutingPolicy:
    """Test routing policy functionality with REAL SDK (minimal mocking)."""

    @pytest.mark.parametrize(
        ("routing_policy", "skills", "expected"),
        [
            pytest.param(
                RoutingPolicy(
                    target_accuracy=0.85,
                    target_permissiveness=0.3,
                    cost_sensitivity=0.4,
                    speed_preference=0.5,
                    priority_level=3,
                    required_skills=[SkillEnum.instruction_following, SkillEnum.analysis],
                    preferred_skills=[SkillEnum.long_context],
                ),
                [SkillEnum.instruction_following],
                {
                    "target_accuracy": 0.85,
                    "target_permissiveness": 0.3,
                    "cost_sensitivity": 0.4,
                    "speed_preference": 0.5,
                    "priority_level": 3,
                    "required_skills": ["instruction_following", "analysis"],
                    "preferred_skills": ["long_context"],
                },
                id="full",
            ),
            pytest.param(
                RoutingPolicy(
                    target_accuracy=0.0,
                    target_permissiveness=0.0,
                    cost_sensitivity=0.0,
                    speed_preference=0.0,
                    priority_level=-10,
                ),
                [],
                {
                    "target_accuracy": 0.0,
                    "target_permissiveness": 0.0,
                    "cost_sensitivity": 0.0,
                    "speed_preference": 0.0,
                    "priority_level": -10,
                },
                id="minimum",
            ),
            pytest.param(
                RoutingPolicy(
                    target_accuracy=1.0,
                    target_permissiveness=1.0,
                    cost_sensitivity=1.0,
                    speed_preference=1.0,
                    priority_level=10,
                ),
                [],
                {
                    "target_accuracy": 1.0,
                    "target_permissiveness": 1.0,
                    "cost_sensitivity": 1.0,
                    "speed_preference": 1.0,
                    "priority_level": 10,
                },
                id="maximum",
            ),
            pytest.param(
                RoutingPolicy(priority_level=7, required_skills=[SkillEnum.instruction_following]),
                [SkillEnum.instruction_following],
                {"priority_level": 7, "required_skills": ["instruction_following"]},
                id="partial",
            ),
            pytest.param(
                RoutingPolicy(cost_sensitivity=None),
                [],
                {"cost_sensitivity": None},
                id="explicit-null-cost-sensitivity",
            ),
            pytest.param(
                RoutingPolicy(
                    required_skills=[SkillEnum.instruction_following, SkillEnum.analysis, SkillEnum.reasoning],
                    preferred_skills=[SkillEnum.long_context, SkillEnum.general],
                ),
                [SkillEnum.instruction_following],
                {
                    "required_skills": ["instruction_following", "analysis", "reasoning"],
                    "preferred_skills": ["long_context", "general"],
                },
                id="multiple-skills",
            ),
            pytest.param(
                RoutingPolicy(required_skills=[SkillEnum.reasoning]),
                [SkillEnum.instruction_following],
                {"required_skills": ["reasoning"]},
                id="explicit-routing-overrides-skills",
            ),
            pytest.param(
                RoutingPolicy(required_skills=[], preferred_skills=[], priority_level=0),
                [],
                {"priority_level": 0, "required_skills": [], "preferred_skills": []},
                id="empty-skills",
            ),
        ],
    )
    async def test_routing_policy_serialized_in_request(
        self,
        minimal_mock_sdk: DualeAISDK,
        routing_policy: RoutingPolicy,
        skills: list[SkillEnum],
        expected: dict[str, object],
    ) -> None:
        """Serialize every supported routing-policy shape exactly."""
        await ask(action="Route task", skills=skills, routing=routing_policy, sdk=minimal_mock_sdk)

        [request] = require_mock_http_transport(minimal_mock_sdk).get_requests()
        assert request["body"]["routing_policy"] == expected
