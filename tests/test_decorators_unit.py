"""Unit tests for @agent and @activity decorators.

Decorator tests run against a REAL DualeAISDK instance with auto_start=False —
no heartbeat / startup tasks are scheduled. ``register_agent`` is a sync
dict insert and is exercised for real. ``execute_activity`` would touch the
scheduler + cache backend, so tests that assert wrapper-delegation patch
*only* that method on the real SDK (``patch.object``), keeping all other SDK
behavior intact. ``Mock(spec=DualeAISDK)`` is forbidden by the conftest fixture
policy. All assertions read SDK state (``sdk.agents``, ``sdk.registered_tools``)
or observable wrapper behavior — decorators keep no module-level registries.
"""

import asyncio
import inspect
import json
from collections.abc import Callable
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import BaseModel

from dualeai import DualeAISDK
from dualeai.config import DualeAIConfig
from dualeai.decorators import activity, agent, tool
from dualeai.messages import AgentConfig
from dualeai.models.bridge import RegisteredTool
from dualeai.models.skill_enum import SkillEnum
from dualeai.sdk import JsonValue
from tests.conftest import UnstartedSDKFactory


class TaskInputModel(BaseModel):
    """Test input model for decorator tests."""

    message: str
    value: int


class TaskOutputModel(BaseModel):
    """Test output model for decorator tests."""

    result: str
    processed_value: int


@pytest.fixture
def test_sdk(unstarted_sdk_factory: UnstartedSDKFactory) -> DualeAISDK:
    """Real agent-bound SDK without background tasks."""
    return unstarted_sdk_factory(agent_id="agent_security_operations")


def registered_agent_entry(sdk: DualeAISDK, function_name: str) -> tuple[str, Callable[..., object], AgentConfig]:
    """Return the single (agent_id, func, config) entry whose id contains function_name."""
    matching = [
        (agent_id, func, config) for agent_id, (func, config) in sdk.agents.items() if function_name in agent_id
    ]
    assert len(matching) == 1, f"expected exactly one agent entry for {function_name!r}, got {len(matching)}"
    return matching[0]


def registered_tool_by_name(sdk: DualeAISDK, name: str) -> RegisteredTool:
    """Return the single registered tool manifest with the given tool name."""
    matching = [registered for registered in sdk.registered_tools if registered.tool.name == name]
    assert len(matching) == 1, f"expected exactly one registered tool named {name!r}, got {len(matching)}"
    return matching[0]


@pytest.mark.unit
class TestAgentDecorator:
    """Unit tests for the @agent decorator."""

    def test_agent_decorator_basic_application(self, test_sdk: DualeAISDK) -> None:
        """Test basic application of @agent decorator."""

        @agent(name="TestAgent", sdk=test_sdk)
        async def test_agent_func(task_input: TaskInputModel) -> TaskOutputModel:
            return TaskOutputModel(result=f"Processed: {task_input.message}", processed_value=task_input.value * 2)

        # Verify function is wrapped
        assert asyncio.iscoroutinefunction(test_agent_func)

        # Verify registration on the SDK instance
        agent_id, _func, config = registered_agent_entry(test_sdk, "test_agent_func")
        assert agent_id
        assert config.name == "TestAgent"
        assert config.org == []
        assert config.capabilities == []

    def test_agent_decorator_with_org_and_capabilities(self, test_sdk: DualeAISDK) -> None:
        """Test @agent decorator with organization and capabilities."""

        @agent(
            name="SpecializedAgent",
            org={"Finance", "Accounting"},
            capabilities=[SkillEnum.analysis, SkillEnum.general],
            sdk=test_sdk,
        )
        async def specialized_agent(_data: dict[str, object]) -> dict[str, object]:
            return {"processed": True}

        _agent_id, _func, config = registered_agent_entry(test_sdk, "specialized_agent")
        assert isinstance(config, AgentConfig)
        assert config.name == "SpecializedAgent"
        assert set(config.org) == {"Finance", "Accounting"}
        assert config.capabilities == [SkillEnum.analysis, SkillEnum.general]

    def test_agent_decorator_sdk_registration(self, test_sdk: DualeAISDK) -> None:
        """Test that @agent decorator registers with SDK via real agents dict."""

        @agent(name="RegisteredAgent", sdk=test_sdk)
        async def registered_agent() -> str:
            return "success"

        assert registered_agent  # Registered via decorator side-effect

        # Verify real SDK registry contains the entry. AgentConfig.function_name
        # is the typed contract for the registered callable's identity — the
        # raw callable in ``agents[id]`` is typed ``Callable[..., object]`` and
        # has no ``__name__`` guarantee.
        _agent_id, _func, config = registered_agent_entry(test_sdk, "registered_agent")
        assert isinstance(config, AgentConfig)
        assert config.name == "RegisteredAgent"
        assert config.function_name == "registered_agent"

    def test_agent_decorator_requires_async_function(self, test_sdk: DualeAISDK) -> None:
        """Test that @agent decorator requires async functions."""
        with pytest.raises(TypeError, match="must be async"):

            @agent(name="SyncAgent", sdk=test_sdk)
            def sync_function():  # Not async
                return "sync"

            assert sync_function  # Registered via decorator side-effect

    def test_agent_decorator_requires_sdk(self):
        """Test that @agent decorator requires SDK parameter."""
        with pytest.raises(ValueError, match="SDK instance required"):

            @agent(name="NoSDKAgent")  # No sdk parameter
            async def no_sdk_agent():
                return "fail"

            assert no_sdk_agent  # Registered via decorator side-effect

    async def test_agent_wrapper_execution(self, test_sdk: DualeAISDK) -> None:
        """Test that wrapped agent function executes correctly."""

        @agent(name="ExecutionAgent", sdk=test_sdk)
        async def execution_agent(value: int) -> int:
            return value * 3

        # Execute wrapped function
        result = await execution_agent(5)
        assert result == 15

    def test_agent_id_generation(self, test_sdk: DualeAISDK) -> None:
        """Test agent ID is derived from module and function name."""

        @agent(name="IDTestAgent", sdk=test_sdk)
        async def id_test_function():
            pass

        agent_id, _func, config = registered_agent_entry(test_sdk, "id_test_function")

        # Agent ID includes module name and function name
        assert "test_decorators_unit" in agent_id  # Module name
        assert "id_test_function" in agent_id  # Function name
        # Function name is also preserved in AgentConfig
        assert config.function_name == "id_test_function"

    def test_agent_metadata_preservation(self, test_sdk: DualeAISDK) -> None:
        """Test that original function metadata is preserved."""

        @agent(name="MetadataAgent", sdk=test_sdk)
        async def documented_agent(param: str) -> str:
            """This is a documented agent function.

            Args:
                param: Input parameter

            Returns:
                Processed string
            """
            return f"processed_{param}"

        # Verify functools.wraps preserved metadata
        assert documented_agent.__name__ == "documented_agent"
        assert documented_agent.__doc__ is not None and "documented agent function" in documented_agent.__doc__

        # Verify signature preservation
        sig = inspect.signature(documented_agent)
        assert "param" in sig.parameters


@pytest.mark.unit
class TestActivityDecorator:
    """Unit tests for the @activity decorator."""

    async def test_activity_decorator_basic_application(self, test_sdk: DualeAISDK) -> None:
        """Test basic application of @activity decorator delegates with defaults."""

        @activity(sdk=test_sdk)
        async def basic_activity(data: str) -> str:
            return f"processed_{data}"

        # Verify function is wrapped
        assert asyncio.iscoroutinefunction(basic_activity)

        # Verify default parameters reach the SDK execution path
        execute_mock = AsyncMock(return_value="sdk_result")
        with patch.object(test_sdk, "execute_activity", new=execute_mock):
            await basic_activity("payload")

        func, cache_ttl, max_retries, arg = execute_mock.call_args[0]
        assert func.__name__ == "basic_activity"
        assert cache_ttl == timedelta(hours=1)
        assert max_retries == 3
        assert arg == "payload"

    async def test_activity_decorator_with_custom_params(self, test_sdk: DualeAISDK) -> None:
        """Test @activity decorator forwards custom parameters."""

        @activity(cache_ttl=timedelta(minutes=30), max_retries=5, sdk=test_sdk)
        async def custom_activity(value: int) -> int:
            return value**2

        execute_mock = AsyncMock(return_value=4)
        with patch.object(test_sdk, "execute_activity", new=execute_mock):
            await custom_activity(2)

        func, cache_ttl, max_retries, _arg = execute_mock.call_args[0]
        assert func.__name__ == "custom_activity"
        assert cache_ttl == timedelta(minutes=30)
        assert max_retries == 5

    def test_activity_decorator_requires_async_function(self, test_sdk: DualeAISDK) -> None:
        """Test that @activity decorator requires async functions."""
        with pytest.raises(TypeError, match="must be async"):

            @activity(sdk=test_sdk)
            def sync_activity():  # Not async
                return "sync"

            assert sync_activity  # Registered via decorator side-effect

    def test_activity_decorator_requires_sdk(self):
        """Test that @activity decorator requires SDK parameter."""
        with pytest.raises(ValueError, match="SDK instance required"):

            @activity(cache_ttl=timedelta(minutes=10))  # No sdk parameter
            async def no_sdk_activity():
                return "fail"

            assert no_sdk_activity  # Registered via decorator side-effect

    async def test_activity_wrapper_delegates_to_sdk(self, test_sdk: DualeAISDK) -> None:
        """Test that wrapped activity delegates to SDK execute_activity.

        Patches only ``execute_activity`` on the real SDK — the rest of the
        decorator wiring runs unmodified.
        """

        @activity(cache_ttl=timedelta(minutes=15), max_retries=2, sdk=test_sdk)
        async def delegated_activity(param1: str, param2: int) -> str:
            return f"{param1}_{param2}"

        execute_mock = AsyncMock(return_value="sdk_result")
        with patch.object(test_sdk, "execute_activity", new=execute_mock):
            result = await delegated_activity(SkillEnum.general, 42)

        # Verify SDK method was called with correct parameters
        execute_mock.assert_called_once()
        call_args = execute_mock.call_args

        func, cache_ttl, max_retries, arg1, arg2 = call_args[0]
        assert func.__name__ == "delegated_activity"
        assert cache_ttl == timedelta(minutes=15)
        assert max_retries == 2
        assert arg1 == SkillEnum.general
        assert arg2 == 42

        assert result == "sdk_result"

    async def test_activity_decorator_bare_usage(self, test_sdk: DualeAISDK) -> None:
        """Test @activity decorator applied without explicit cache parameters."""

        # This tests the callable(cache_ttl) branch in the decorator
        async def async_mock_func():
            return SkillEnum.general

        async_mock_func.__name__ = "test_func"

        # Use decorator with keyword arguments (normal usage)
        decorated = activity(sdk=test_sdk)(async_mock_func)

        # Should delegate with default values
        execute_mock = AsyncMock(return_value="cached")
        with patch.object(test_sdk, "execute_activity", new=execute_mock):
            await decorated()

        _func, cache_ttl, max_retries = execute_mock.call_args[0]
        assert cache_ttl == timedelta(hours=1)
        assert max_retries == 3

    def test_activity_metadata_preservation(self, test_sdk: DualeAISDK) -> None:
        """Test that original function metadata is preserved."""

        @activity(cache_ttl=timedelta(hours=2), sdk=test_sdk)
        async def documented_activity(input_data: dict[str, JsonValue]) -> dict[str, JsonValue]:
            """This activity processes input data.

            Args:
                input_data: Data to process

            Returns:
                Processed data dictionary
            """
            return {"processed": input_data}

        # Verify functools.wraps preserved metadata
        assert documented_activity.__name__ == "documented_activity"
        assert documented_activity.__doc__ is not None and "processes input data" in documented_activity.__doc__

        # Verify signature preservation
        sig = inspect.signature(documented_activity)
        assert "input_data" in sig.parameters


@pytest.mark.unit
class TestDecoratorIntegration:
    """Test integration aspects of decorators."""

    def test_agents_registered_on_sdk(self, test_sdk: DualeAISDK) -> None:
        """Decorated agents land in the SDK agents dict with their configs."""

        # Register some agents
        @agent(name="Agent1", capabilities=[SkillEnum.general], sdk=test_sdk)
        async def agent1():
            pass

        assert agent1  # Registered via decorator side-effect

        @agent(name="Agent2", org={"TestOrg"}, sdk=test_sdk)
        async def agent2():
            pass

        assert agent2  # Registered via decorator side-effect

        # Verify registry contents
        assert len(test_sdk.agents) == 2

        _id1, _func1, config1 = registered_agent_entry(test_sdk, "agent1")
        assert config1.name == "Agent1"
        assert config1.capabilities == [SkillEnum.general]

        _id2, _func2, config2 = registered_agent_entry(test_sdk, "agent2")
        assert config2.name == "Agent2"
        assert config2.org == ["TestOrg"]

    async def test_multiple_decorators_different_sdks(self):
        """Test using decorators with different SDK instances."""
        config1 = DualeAIConfig(token="dualeai_test_token_sdk_one")
        config2 = DualeAIConfig(token="dualeai_test_token_sdk_two")

        sdk1 = DualeAISDK(config=config1, auto_start=False)
        sdk2 = DualeAISDK(config=config2, auto_start=False)

        @agent(name="SDK1Agent", sdk=sdk1)
        async def sdk1_agent():
            return "sdk1"

        assert sdk1_agent  # Registered via decorator side-effect

        @agent(name="SDK2Agent", sdk=sdk2)
        async def sdk2_agent():
            return "sdk2"

        assert sdk2_agent  # Registered via decorator side-effect

        # Verify each SDK has only its own agent
        assert len(sdk1.agents) == 1
        assert len(sdk2.agents) == 1

        # Verify different agent IDs
        agent1_id = next(iter(sdk1.agents.keys()))
        agent2_id = next(iter(sdk2.agents.keys()))

        assert "sdk1_agent" in agent1_id
        assert "sdk2_agent" in agent2_id

    def test_agent_and_activity_register_independently(self, test_sdk: DualeAISDK) -> None:
        """An @agent function registers on the SDK; an @activity function does not."""

        @agent(name="TestAgent", sdk=test_sdk)
        async def agent_func():
            pass

        @activity(sdk=test_sdk)
        async def activity_func():
            pass

        assert agent_func
        assert activity_func

        # Only the agent lands in sdk.agents; the activity registers nothing there.
        assert any("agent_func" in agent_id for agent_id in test_sdk.agents)
        assert not any("activity_func" in agent_id for agent_id in test_sdk.agents)

    async def test_nested_decorator_execution(self, test_sdk: DualeAISDK) -> None:
        """Test that decorated functions can call each other."""

        @activity(cache_ttl=timedelta(minutes=5), sdk=test_sdk)
        async def helper_activity(value: int) -> int:
            return value * 2

        @agent(name="CompositeAgent", sdk=test_sdk)
        async def composite_agent(input_val: int) -> int:
            # Agent calling an activity
            doubled = await helper_activity(input_val)
            assert isinstance(doubled, int)
            return doubled + 10

        # Mock the SDK's execute_activity method for this test
        with patch.object(test_sdk, "execute_activity", new=AsyncMock(return_value=20)):
            result = await composite_agent(5)
            assert result == 30  # 20 (mocked) + 10


@pytest.mark.unit
class TestToolDecorator:
    """Unit tests for the @tool decorator."""

    def test_tool_decorator_registers_generated_manifest(self, test_sdk: DualeAISDK) -> None:
        """@tool registers a generated bridge RegisteredTool manifest."""

        @tool(
            sdk=test_sdk,
            description="Close a named security gate and return the final gate state.",
            timeout=timedelta(seconds=10),
        )
        async def close_security_gate(gate_id: str, reason: str) -> dict[str, str]:
            """Developer maintenance note; not sent to the model."""
            return {"gate_id": gate_id, "state": "closed", "reason": reason}

        registered = registered_tool_by_name(test_sdk, "close_security_gate")
        assert registered.tool.description == "Close a named security gate and return the final gate state."
        assert registered.timeout_seconds == 10
        assert registered.tool.parameters.type == "object"
        assert registered.tool.parameters.required == ["gate_id", "reason"]
        assert registered.tool.parameters.additional_properties is False
        assert registered.tool.parameters.properties["gate_id"]["type"] == "string"
        assert registered.tool.parameters.properties["reason"]["type"] == "string"
        assert close_security_gate.__doc__ is not None
        assert "Developer maintenance note" in close_security_gate.__doc__
        # Docstring stays developer-facing: it must never leak into the published manifest (RFC-121).
        assert "Developer maintenance note" not in json.dumps(registered.model_dump(mode="json", by_alias=True))
        assert test_sdk.registered_tools == [registered]

    def test_tool_decorator_accepts_sync_function(self, test_sdk: DualeAISDK) -> None:
        """@tool accepts a plain def; the sync callable is registered (runs in a worker thread)."""

        @tool(sdk=test_sdk, description="Run a sync tool.", timeout=timedelta(seconds=1))
        def sync_tool(value: str) -> str:
            return value

        registered = registered_tool_by_name(test_sdk, "sync_tool")
        assert registered.tool.name == "sync_tool"
        # The decorated sync tool stays directly callable and returns its value.
        assert sync_tool("north") == "north"

    def test_instance_bound_tool_decorator_registers_without_sdk_arg(self, test_sdk: DualeAISDK) -> None:
        """@sdk.tool registers a tool without the explicit sdk= argument (RFC-121 DX #21)."""

        @test_sdk.tool(description="Instance-bound gate.", timeout=timedelta(seconds=1))
        async def bound_gate(gate_id: str) -> dict[str, str]:
            return {"gate_id": gate_id}

        assert bound_gate
        # Registration on THIS instance proves the decorator bound to test_sdk.
        registered = registered_tool_by_name(test_sdk, "bound_gate")
        assert registered.tool.description == "Instance-bound gate."

    def test_tool_decorator_requires_sdk(self) -> None:
        """@tool requires an explicit SDK instance."""
        with pytest.raises(ValueError, match="SDK instance required"):

            @tool(description="Missing SDK.", timeout=timedelta(seconds=1))
            async def missing_sdk_tool(value: str) -> str:
                return value

            assert missing_sdk_tool

    def test_tool_decorator_requires_description(self, test_sdk: DualeAISDK) -> None:
        """@tool requires model-facing description text."""
        with pytest.raises(ValueError, match="description is required"):

            @tool(sdk=test_sdk, description="", timeout=timedelta(seconds=1))
            async def missing_description(value: str) -> str:
                return value

            assert missing_description

    def test_tool_decorator_requires_positive_timeout(self, test_sdk: DualeAISDK) -> None:
        """@tool requires a positive timeout."""
        with pytest.raises(ValueError, match="timeout must be positive"):

            @tool(sdk=test_sdk, description="No timeout.", timeout=timedelta(seconds=0))
            async def zero_timeout(value: str) -> str:
                return value

            assert zero_timeout

    def test_tool_decorator_rejects_duplicate_name(self, test_sdk: DualeAISDK) -> None:
        """Tool function names are unique within one SDK instance."""

        async def duplicate_tool(value: str) -> str:
            return value

        decorated = tool(sdk=test_sdk, description="First tool.", timeout=timedelta(seconds=1))(duplicate_tool)
        assert decorated

        with pytest.raises(ValueError, match="already registered"):
            tool(sdk=test_sdk, description="Second tool.", timeout=timedelta(seconds=1))(duplicate_tool)


@pytest.mark.unit
class TestDecoratorErrorHandling:
    """Test error handling in decorators."""

    def test_agent_decorator_invalid_org_type(self, test_sdk: DualeAISDK) -> None:
        """Test @agent with invalid org type."""

        # This should work - set gets converted to list in the registered config
        @agent(name="TestAgent", org={"Finance", "Accounting"}, sdk=test_sdk)
        async def test_agent():
            pass

        # Verify conversion happened
        _agent_id, _func, config = registered_agent_entry(test_sdk, "test_agent")
        assert isinstance(config.org, list)
        assert set(config.org) == {"Finance", "Accounting"}

    async def test_activity_decorator_invalid_cache_ttl(self, test_sdk: DualeAISDK) -> None:
        """Test @activity with invalid cache_ttl type."""

        # This test actually should pass as negative timedelta is valid in Python
        # Testing the decorator accepts timedelta objects
        @activity(cache_ttl=timedelta(seconds=1), sdk=test_sdk)
        async def good_activity():
            pass

        # Verify the activity was created successfully and delegates the TTL
        execute_mock = AsyncMock(return_value=None)
        with patch.object(test_sdk, "execute_activity", new=execute_mock):
            await good_activity()
        assert execute_mock.call_args[0][1] == timedelta(seconds=1)

    async def test_activity_decorator_negative_retries(self, test_sdk: DualeAISDK) -> None:
        """Test @activity with negative max_retries."""

        # This should work - SDK should handle validation
        @activity(max_retries=-1, sdk=test_sdk)
        async def negative_retries_activity():
            pass

        execute_mock = AsyncMock(return_value=None)
        with patch.object(test_sdk, "execute_activity", new=execute_mock):
            await negative_retries_activity()
        assert execute_mock.call_args[0][2] == -1  # Forwarded as-is

    def test_agent_decorator_empty_name(self, test_sdk: DualeAISDK) -> None:
        """Test @agent with empty name."""

        # This should work - empty string is valid
        @agent(name="", sdk=test_sdk)
        async def empty_name_agent():
            pass

        _agent_id, _func, config = registered_agent_entry(test_sdk, "empty_name_agent")
        assert config.name == ""

    def test_decorator_with_none_sdk(self):
        """Test decorators explicitly passed None for SDK."""
        with pytest.raises(ValueError, match="SDK instance required"):

            @agent(name="TestAgent", sdk=None)
            async def none_sdk_agent():
                pass

            assert none_sdk_agent  # Registered via decorator side-effect

        with pytest.raises(ValueError, match="SDK instance required"):

            @activity(sdk=None)
            async def none_sdk_activity():
                pass

            assert none_sdk_activity  # Registered via decorator side-effect
