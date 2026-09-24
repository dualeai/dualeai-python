"""Routing choices passed from the public ask() API to the Task transport."""

import pytest

from dualeai import DualeAISDK
from dualeai.models.bridge import BridgeTaskCreateRequest
from dualeai.models.routing_policy import RoutingPolicy
from dualeai.models.skill_enum import SkillEnum
from dualeai.orchestrator import ask
from tests.mocks.mock_http import require_mock_http_transport


@pytest.mark.unit
async def test_explicit_routing_policy_overrides_derived_skills(minimal_mock_sdk: DualeAISDK) -> None:
    policy = RoutingPolicy(
        priority_level=0,
        cost_sensitivity=None,
        required_skills=[SkillEnum.reasoning],
        preferred_skills=[SkillEnum.long_context],
    )

    await ask(
        action="Route task",
        skills=[SkillEnum.instruction_following],
        routing=policy,
        sdk=minimal_mock_sdk,
    )

    [call] = require_mock_http_transport(minimal_mock_sdk).get_requests()
    request = call["request"]
    assert isinstance(request, BridgeTaskCreateRequest)
    routing = request.routing_policy
    assert routing is not None
    assert routing.priority_level == 0
    assert routing.cost_sensitivity is None
    assert routing.required_skills == [SkillEnum.reasoning]
    assert routing.preferred_skills == [SkillEnum.long_context]


@pytest.mark.unit
async def test_skills_derive_only_required_routing_skills(minimal_mock_sdk: DualeAISDK) -> None:
    skills = [SkillEnum.instruction_following, SkillEnum.analysis]

    await ask(action="Route task", skills=skills, sdk=minimal_mock_sdk)

    [call] = require_mock_http_transport(minimal_mock_sdk).get_requests()
    request = call["request"]
    assert isinstance(request, BridgeTaskCreateRequest)
    routing = request.routing_policy
    assert routing is not None
    assert routing.required_skills == [SkillEnum.instruction_following, SkillEnum.analysis]
    assert routing.model_fields_set == {"required_skills"}
