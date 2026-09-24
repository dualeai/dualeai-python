"""Routing-policy skill values survive public wire serialization."""

import pytest

from dualeai._wire import dump_wire_model
from dualeai.models.routing_policy import RoutingPolicy
from dualeai.models.skill_enum import SkillEnum


@pytest.mark.unit
class TestSkillsProcessing:
    """Test skill values in generated RoutingPolicy models."""

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

    def test_routing_policy_wire_preserves_explicit_zero_null_and_empty_lists(self) -> None:
        policy = RoutingPolicy(
            priority_level=0,
            cost_sensitivity=None,
            required_skills=[],
            preferred_skills=[],
        )

        assert dump_wire_model(policy) == {
            "priority_level": 0,
            "cost_sensitivity": None,
            "required_skills": [],
            "preferred_skills": [],
        }
