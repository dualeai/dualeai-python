"""Routing-policy capability values survive public wire serialization."""

import pytest

from dualeai._wire import dump_wire_model
from dualeai.models.capability import Capability
from dualeai.models.routing_policy import RoutingPolicy


@pytest.mark.unit
class TestCapabilitiesProcessing:
    """Test capability values in generated RoutingPolicy models."""

    @pytest.mark.parametrize(
        "capabilities",
        [
            pytest.param([Capability.code], id="one"),
            pytest.param([Capability.code, Capability.analysis], id="two"),
            pytest.param(list(Capability), id="every-enum-value"),
        ],
    )
    def test_routing_policy_capabilities_round_trip(self, capabilities: list[Capability]) -> None:
        """Routing policies preserve valid capability sets on the public wire."""
        policy = RoutingPolicy(target_accuracy=0.9, required_capabilities=capabilities)
        serialized = dump_wire_model(policy)
        assert serialized == {
            "target_accuracy": 0.9,
            "required_capabilities": [capability.value for capability in capabilities],
        }

        deserialized = RoutingPolicy.model_validate(serialized)
        assert deserialized.target_accuracy == 0.9
        assert deserialized.required_capabilities == capabilities

    def test_routing_policy_wire_preserves_explicit_zero_null_and_empty_lists(self) -> None:
        policy = RoutingPolicy(
            priority_level=0,
            cost_sensitivity=None,
            required_capabilities=[],
            preferred_capabilities=[],
        )

        assert dump_wire_model(policy) == {
            "priority_level": 0,
            "cost_sensitivity": None,
            "required_capabilities": [],
            "preferred_capabilities": [],
        }
