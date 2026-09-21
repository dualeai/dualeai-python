"""Tests for skills processing to prevent .value attribute errors.

This module contains regression tests for the bugs we encountered with
skills processing in decorators and SDK methods. These tests ensure that
both SkillEnum and string skills are handled correctly without AttributeError.

Uses a REAL ``DualeAISDK`` (auto_start=False) — ``Mock(spec=DualeAISDK)`` is
forbidden by the conftest fixture policy.
"""

import pytest

from dualeai import DualeAISDK
from dualeai._wire import dump_wire_model
from dualeai.decorators import agent
from dualeai.messages import AgentConfig
from dualeai.models.routing_policy import RoutingPolicy
from dualeai.models.skill_enum import SkillEnum
from tests.conftest import UnstartedSDKFactory


@pytest.fixture
def test_sdk(unstarted_sdk_factory: UnstartedSDKFactory) -> DualeAISDK:
    """Real SDK, no background tasks (auto_start=False)."""
    return unstarted_sdk_factory()


@pytest.mark.unit
class TestSkillsProcessing:
    """Test skills processing in decorators and SDK methods."""

    @pytest.mark.parametrize(
        ("capabilities", "expected_capabilities"),
        [
            pytest.param([SkillEnum.code, SkillEnum.analysis], [SkillEnum.code, SkillEnum.analysis], id="two"),
            pytest.param(
                [SkillEnum.agentic, SkillEnum.reasoning],
                [SkillEnum.agentic, SkillEnum.reasoning],
                id="specialized",
            ),
            pytest.param(
                [SkillEnum.code, SkillEnum.agentic, SkillEnum.analysis, SkillEnum.instruction_following],
                [SkillEnum.code, SkillEnum.agentic, SkillEnum.analysis, SkillEnum.instruction_following],
                id="four",
            ),
            pytest.param([], [], id="empty"),
            pytest.param(None, [], id="omitted"),
        ],
    )
    def test_agent_decorator_preserves_capabilities(
        self,
        test_sdk: DualeAISDK,
        capabilities: list[SkillEnum] | None,
        expected_capabilities: list[SkillEnum],
    ) -> None:
        """The decorator preserves every supported capabilities shape."""

        @agent(name="CapabilitiesAgent", capabilities=capabilities, sdk=test_sdk)
        async def capabilities_agent() -> str:
            return "success"

        assert capabilities_agent  # Registered via decorator side-effect

        matching = [entry for agent_id, entry in test_sdk.agents.items() if "capabilities_agent" in agent_id]
        assert len(matching) == 1
        _func, config = matching[0]
        assert isinstance(config, AgentConfig)
        assert config.capabilities == expected_capabilities

    @pytest.mark.parametrize(
        "skills",
        [
            pytest.param([SkillEnum.code], id="one"),
            pytest.param([SkillEnum.code, SkillEnum.analysis], id="two"),
            pytest.param(list(SkillEnum), id="every-enum-value"),
        ],
    )
    def test_routing_policy_skills_round_trip(self, skills: list[SkillEnum]) -> None:
        """Routing policies preserve valid skill sets on the public wire."""
        policy = RoutingPolicy(target_accuracy=0.9, required_skills=skills)
        serialized = dump_wire_model(policy)
        assert serialized == {
            "target_accuracy": 0.9,
            "required_skills": [skill.value for skill in skills],
        }

        deserialized = RoutingPolicy.model_validate(serialized)
        assert deserialized.target_accuracy == 0.9
        assert deserialized.required_skills == skills
