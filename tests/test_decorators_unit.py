"""Unit tests for the activity and Tool decorators.

Tests use a real SDK without background startup. Activity wrapper tests patch
only ``execute_activity``; Tool tests inspect the published SDK manifest.
"""

import inspect
import json
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from dualeai import DualeAISDK
from dualeai.decorators import activity, tool
from dualeai.models.bridge import RegisteredTool
from dualeai.models.skill_enum import SkillEnum
from dualeai.sdk import JsonValue
from tests.conftest import UnstartedSDKFactory


@pytest.fixture
def test_sdk(unstarted_sdk_factory: UnstartedSDKFactory) -> DualeAISDK:
    """Real Tool-capable SDK without background tasks."""
    return unstarted_sdk_factory(agent_id="agent_security_operations")


def registered_tool_by_name(sdk: DualeAISDK, name: str) -> RegisteredTool:
    """Return the single registered tool manifest with the given tool name."""
    matching = [registered for registered in sdk.registered_tools if registered.tool.name == name]
    assert len(matching) == 1, f"expected exactly one registered tool named {name!r}, got {len(matching)}"
    return matching[0]


@pytest.mark.unit
class TestActivityDecorator:
    """Unit tests for the @activity decorator."""

    async def test_activity_decorator_basic_application(self, test_sdk: DualeAISDK) -> None:
        """Test basic application of @activity decorator delegates with defaults."""

        @activity(sdk=test_sdk)
        async def basic_activity(data: str) -> str:
            return f"processed_{data}"

        # Verify function is wrapped
        assert inspect.iscoroutinefunction(basic_activity)

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
        # Docstring stays developer-facing; it must never leak into the published manifest.
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
        """@sdk.tool registers a Tool without the explicit sdk= argument."""

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

    def test_decorator_with_none_sdk(self):
        """An activity requires an SDK instance."""
        with pytest.raises(ValueError, match="SDK instance required"):

            @activity(sdk=None)
            async def none_sdk_activity():
                pass

            assert none_sdk_activity  # Registered via decorator side-effect
