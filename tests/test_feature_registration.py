"""Tests for agent registration feature (Priority 12).

Priority 12 (Score 5): Agent registration with REAL SDK.
Tests agent decorator registration, unique ID generation, config storage, and NATS publishing.

CRITICAL RULES:
- Use minimal_mock_sdk for ALL tests - NATS mocked at network boundary
- Test REAL SDK code behavior
- All tests use @pytest.mark.unit and TestUnit* class naming
"""

import asyncio
import types

import pytest

from dualeai import DualeAISDK
from dualeai.decorators import agent
from dualeai.messages import AgentConfig
from dualeai.models.skill_enum import SkillEnum


@pytest.mark.unit
class TestUnitAgentRegistration:
    """Test agent registration with REAL SDK code."""

    async def test_agent_decorator_registers_function(self, minimal_mock_sdk: DualeAISDK):
        """Test @agent decorator registers function with SDK."""
        sdk = minimal_mock_sdk

        # Apply REAL @agent decorator
        @agent(
            name="TestAgent",
            capabilities=[SkillEnum.general, SkillEnum.analysis],
            sdk=sdk,
        )
        async def test_agent_func(data: dict[str, object]) -> dict[str, object]:
            return {"processed": True, "data": data}

        assert test_agent_func  # Registered via decorator side-effect

        # Verify function is registered in SDK.agents
        assert len(sdk.agents) >= 1

        # Get the registered agent
        agent_ids = [aid for aid in sdk.agents if "test_agent_func" in str(aid)]
        assert len(agent_ids) == 1

        agent_id = agent_ids[0]
        func, config_obj = sdk.agents[agent_id]

        # Verify function is correct
        # asyncio.iscoroutinefunction confirms func is a real coroutine function with __name__
        assert asyncio.iscoroutinefunction(func)
        assert isinstance(func, types.FunctionType)
        assert func.__name__ == "test_agent_func"

        # Verify config is correct
        assert isinstance(config_obj, AgentConfig)
        assert config_obj.name == "TestAgent"
        assert config_obj.capabilities == [SkillEnum.general, SkillEnum.analysis]
        assert config_obj.function_name == "test_agent_func"

    async def test_agent_generates_unique_id(self, minimal_mock_sdk: DualeAISDK):
        """Test agent ID is unique and includes SDK agent_id + module + function name."""
        sdk = minimal_mock_sdk

        # Register first agent
        @agent(name="FirstAgent", sdk=sdk)
        async def first_agent() -> str:
            return "first"

        assert first_agent  # Registered via decorator side-effect

        # Register second agent
        @agent(name="SecondAgent", sdk=sdk)
        async def second_agent() -> str:
            return "second"

        assert second_agent  # Registered via decorator side-effect

        # Verify unique agent IDs
        agent_ids = list(sdk.agents.keys())

        # Should include function names
        assert any("first_agent" in str(aid) for aid in agent_ids)
        assert any("second_agent" in str(aid) for aid in agent_ids)

        # Should include module name
        assert all("test_feature_registration" in str(aid) for aid in agent_ids)

        # IDs for our test agents must be different
        first_ids = [aid for aid in agent_ids if "first_agent" in str(aid)]
        second_ids = [aid for aid in agent_ids if "second_agent" in str(aid)]
        assert first_ids[0] != second_ids[0]

    async def test_registered_agent_has_config(self, minimal_mock_sdk: DualeAISDK):
        """Test registered agent stores AgentConfig with all fields."""
        sdk = minimal_mock_sdk

        # Register agent with full config
        @agent(
            name="FullConfigAgent",
            org={"Engineering", "ML"},
            capabilities=[SkillEnum.code, SkillEnum.analysis],
            sdk=sdk,
        )
        async def full_config_agent(input_data: str) -> str:
            return f"processed: {input_data}"

        assert full_config_agent  # Registered via decorator side-effect

        # Find the agent we just registered in the SDK registry
        agent_ids = [aid for aid in sdk.agents if "full_config_agent" in str(aid)]
        assert len(agent_ids) == 1

        agent_id = agent_ids[0]
        func, agent_config = sdk.agents[agent_id]

        # Verify AgentConfig fields
        assert isinstance(agent_config, AgentConfig)
        assert agent_config.name == "FullConfigAgent"
        assert set(agent_config.org) == {"Engineering", "ML"}
        assert agent_config.capabilities == [SkillEnum.code, SkillEnum.analysis]
        assert agent_config.function_name == "full_config_agent"

        # Verify function is callable
        assert callable(func)
        assert asyncio.iscoroutinefunction(func)

    async def test_agent_decorator_stores_config(self, minimal_mock_sdk: DualeAISDK):
        """Test @agent decorator stores the full config on THIS SDK instance."""
        sdk = minimal_mock_sdk

        # Apply decorator
        @agent(
            name="MetadataAgent",
            org={"Finance"},
            capabilities=[SkillEnum.analysis],
            sdk=sdk,
        )
        async def metadata_agent() -> None:
            pass

        assert metadata_agent  # Registered via decorator side-effect

        # Registration on THIS instance proves the decorator bound to sdk
        agent_ids = [aid for aid in sdk.agents if "metadata_agent" in str(aid)]
        assert len(agent_ids) == 1
        _func, config = sdk.agents[agent_ids[0]]

        # Verify config contents
        assert isinstance(config, AgentConfig)
        assert config.name == "MetadataAgent"
        assert set(config.org) == {"Finance"}
        assert config.capabilities == [SkillEnum.analysis]

    async def test_multiple_agents_different_configs(self, minimal_mock_sdk: DualeAISDK):
        """Test registering multiple agents with different configurations."""
        sdk = minimal_mock_sdk

        # Register multiple agents with different configs
        @agent(name="Agent1", capabilities=[SkillEnum.analysis], sdk=sdk)
        async def agent1() -> str:
            return "agent1"

        assert agent1  # Registered via decorator side-effect

        @agent(name="Agent2", org={"Engineering"}, sdk=sdk)
        async def agent2() -> str:
            return "agent2"

        assert agent2  # Registered via decorator side-effect

        @agent(name="Agent3", capabilities=[SkillEnum.agentic], sdk=sdk)
        async def agent3() -> str:
            return "agent3"

        assert agent3  # Registered via decorator side-effect

        # Verify each config in the SDK registry
        configs = {config.name: config for _, config in sdk.agents.values()}
        assert "Agent1" in configs
        assert "Agent2" in configs
        assert "Agent3" in configs

        # Verify individual configs
        assert configs["Agent1"].capabilities == [SkillEnum.analysis]
        assert configs["Agent2"].org == ["Engineering"]
        assert configs["Agent3"].capabilities == [SkillEnum.agentic]

    async def test_agent_decorator_preserves_function_signature(self, minimal_mock_sdk: DualeAISDK):
        """Test @agent decorator preserves original function signature and metadata."""
        sdk = minimal_mock_sdk

        @agent(name="DocumentedAgent", sdk=sdk)
        async def documented_agent(param1: str, param2: int) -> dict[str, object]:
            """This is a documented agent function.

            Args:
                param1: First parameter
                param2: Second parameter

            Returns:
                Result dictionary
            """
            return {"param1": param1, "param2": param2}

        # Verify function name preserved
        assert documented_agent.__name__ == "documented_agent"

        # Verify docstring preserved
        assert documented_agent.__doc__ is not None
        assert "documented agent function" in documented_agent.__doc__

        # Verify signature preserved
        import inspect

        sig = inspect.signature(documented_agent)
        assert "param1" in sig.parameters
        assert "param2" in sig.parameters

        # Verify function still works
        result = await documented_agent(SkillEnum.general, 42)
        assert result == {"param1": SkillEnum.general, "param2": 42}
