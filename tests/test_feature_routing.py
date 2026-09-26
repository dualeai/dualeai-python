"""Routing choices passed from the public ask() API to the Task transport."""

import pytest

from dualeai import DualeAISDK
from dualeai.models.bridge import BridgeTaskCreateRequest
from dualeai.models.capability import Capability
from dualeai.models.routing_policy import RoutingPolicy
from dualeai.orchestrator import ask
from tests.mocks.mock_http import require_mock_http_transport


@pytest.mark.unit
async def test_explicit_routing_policy_overrides_derived_capabilities(minimal_mock_sdk: DualeAISDK) -> None:
    policy = RoutingPolicy(
        priority_level=0,
        cost_sensitivity=None,
        required_capabilities=[Capability.reasoning],
        preferred_capabilities=[Capability.long_context],
    )

    await ask(
        action="Route task",
        capabilities=[Capability.instruction_following],
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
    assert routing.required_capabilities == [Capability.reasoning]
    assert routing.preferred_capabilities == [Capability.long_context]


@pytest.mark.unit
async def test_capabilities_derive_only_required_routing_capabilities(minimal_mock_sdk: DualeAISDK) -> None:
    capabilities = [Capability.instruction_following, Capability.analysis]

    await ask(action="Route task", capabilities=capabilities, sdk=minimal_mock_sdk)

    [call] = require_mock_http_transport(minimal_mock_sdk).get_requests()
    request = call["request"]
    assert isinstance(request, BridgeTaskCreateRequest)
    routing = request.routing_policy
    assert routing is not None
    assert routing.required_capabilities == [Capability.instruction_following, Capability.analysis]
    assert routing.model_fields_set == {"required_capabilities"}
