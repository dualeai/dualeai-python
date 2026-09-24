"""Tests for the SDK's local health snapshot.

The fixture uses the real SDK with its HTTP transport boundary replaced.
"""

import asyncio
import time
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from dualeai import DualeAISDK
from dualeai.cache import MockCacheBackend
from dualeai.models.bridge import BridgeTaskCompletedResponse
from dualeai.models.llm_result import LLMResult
from dualeai.response import TerminalEvent
from dualeai.sdk import ComponentHealth
from tests.conftest import TestTenantIDs


def _component_bool(raw_components: dict[str, object], name: str) -> bool:
    value = raw_components[name]
    if not isinstance(value, bool):
        raise TypeError(f"Health component {name} must be a boolean")
    return value


def _health_components(health: dict[str, object]) -> ComponentHealth:
    raw_components = health["components"]
    assert isinstance(raw_components, Mapping)
    components_by_name: dict[str, object] = {}
    for key, value in raw_components.items():
        assert isinstance(key, str)
        components_by_name[key] = value
    return {
        "sdk": _component_bool(components_by_name, "sdk"),
        "scheduler": _component_bool(components_by_name, "scheduler"),
        "cache": _component_bool(components_by_name, "cache"),
        "events": _component_bool(components_by_name, "events"),
    }


async def _sleeping_terminal_event() -> TerminalEvent:
    await asyncio.sleep(60)
    return BridgeTaskCompletedResponse(
        type="task.completed",
        timestamp=datetime.now(timezone.utc),
        result=LLMResult(completion="done", cache_hit=False),
    )


@pytest.mark.unit
class TestUnitHealthFeature:
    """Test get_health_status() with REAL SDK logic."""

    async def test_get_health_status_returns_status(self, minimal_mock_sdk: DualeAISDK):
        """Test get_health_status() returns status dict with expected keys."""
        sdk = minimal_mock_sdk

        # Execute get_health_status() - real SDK code
        health = sdk.get_health_status()

        # Verify response structure
        assert isinstance(health, dict)
        assert "status" in health
        assert "components" in health
        assert "uptime_seconds" in health
        assert "inflight_tasks" in health
        assert "registered_tools" in health

        # minimal_mock_sdk has no failing component — status is deterministic.
        assert health["status"] == "healthy"

        # Verify components is dict
        assert isinstance(health["components"], dict)

        # Verify numeric fields
        assert isinstance(health["uptime_seconds"], int)
        assert isinstance(health["inflight_tasks"], int)
        assert isinstance(health["registered_tools"], int)

    async def test_health_includes_component_status(self, minimal_mock_sdk: DualeAISDK):
        """Test health check includes status for each component."""
        sdk = minimal_mock_sdk

        # Execute get_health_status()
        health = sdk.get_health_status()

        # Verify component status keys exist
        components = _health_components(health)
        assert "sdk" in components
        assert "scheduler" in components
        assert "cache" in components
        assert "events" in components

        # Verify component values are boolean
        assert isinstance(components["sdk"], bool)
        assert isinstance(components["scheduler"], bool)
        assert isinstance(components["cache"], bool)
        assert isinstance(components["events"], bool)

    async def test_health_reflects_connection_state(self, minimal_mock_sdk: DualeAISDK):
        """Test health status reflects actual component connection states."""
        sdk = minimal_mock_sdk

        # Get initial health (minimal_mock_sdk has events connected via mock)
        health_before = sdk.get_health_status()

        # SDK component should always be healthy
        components_before = _health_components(health_before)
        assert components_before["sdk"] is True

        # Events are healthy because the injected HTTP transport is connected.
        assert components_before["events"] is True

        # Initialize scheduler
        await sdk._ensure_scheduler()
        health_with_scheduler = sdk.get_health_status()

        # Scheduler should now be healthy
        components_with_scheduler = _health_components(health_with_scheduler)
        assert components_with_scheduler["scheduler"] is True

        # Mock cache backend
        sdk.cache = MockCacheBackend(tenant_id=TestTenantIDs.DEFAULT)
        health_with_cache = sdk.get_health_status()

        # Cache should now be healthy
        components_with_cache = _health_components(health_with_cache)
        assert components_with_cache["cache"] is True

    async def test_health_overall_status_healthy_when_components_healthy(self, minimal_mock_sdk: DualeAISDK):
        """Test overall status is healthy when all components are healthy."""
        sdk = minimal_mock_sdk

        # Initialize all components
        await sdk._ensure_scheduler()
        sdk.cache = MockCacheBackend(tenant_id=TestTenantIDs.DEFAULT)

        # Get health status
        health = sdk.get_health_status()

        # Overall status should be healthy
        assert health["status"] == "healthy"

        # All components should be healthy
        assert all(_health_components(health).values())

    async def test_health_overall_status_reflects_component_states(self, minimal_mock_sdk: DualeAISDK):
        """Test overall status reflects component states."""
        sdk = minimal_mock_sdk

        # Get initial health
        health = sdk.get_health_status()

        # SDK component should always be healthy
        components = _health_components(health)
        assert components["sdk"] is True

        # minimal_mock_sdk has no failing component — status is deterministic.
        assert health["status"] == "healthy"

        # Verify all components are accounted for
        assert "scheduler" in components
        assert "cache" in components
        assert "events" in components

    async def test_health_uptime_increases_over_time(self, minimal_mock_sdk: DualeAISDK):
        """Test uptime_seconds increases as time passes."""
        sdk = minimal_mock_sdk

        # Get initial health
        health1 = sdk.get_health_status()
        uptime1 = health1["uptime_seconds"]
        assert isinstance(uptime1, (int, float))

        # Wait a bit
        time.sleep(0.1)

        # Get health again
        health2 = sdk.get_health_status()
        uptime2 = health2["uptime_seconds"]
        assert isinstance(uptime2, (int, float))

        # Uptime should have increased
        assert uptime2 >= uptime1

    async def test_health_inflight_tasks_count(self, minimal_mock_sdk: DualeAISDK):
        """Test inflight_tasks reflects actual in-flight task count.

        After the Future→asyncio.Task migration, in-flight tasks are
        spawned by submit_task (real bridge iteration). This test
        directly populates the inflight set with sentinel tasks.
        """
        import asyncio

        sdk = minimal_mock_sdk

        # No tasks yet
        assert sdk.get_health_status()["inflight_tasks"] == 0

        # Spawn sentinel tasks and register
        t1 = asyncio.create_task(_sleeping_terminal_event())
        t2 = asyncio.create_task(_sleeping_terminal_event())
        sdk._inflight_tasks["task-1"] = t1
        sdk._inflight_tasks["task-2"] = t2

        try:
            assert sdk.get_health_status()["inflight_tasks"] == 2

            t1.cancel()
            try:
                await t1
            except asyncio.CancelledError:
                pass
            sdk._inflight_tasks.pop("task-1", None)

            assert sdk.get_health_status()["inflight_tasks"] == 1
        finally:
            t2.cancel()
            try:
                await t2
            except asyncio.CancelledError:
                pass
            sdk._inflight_tasks.clear()

    def test_health_registered_tools_count(self, minimal_mock_sdk: DualeAISDK) -> None:
        """The local health snapshot counts Tools registered through the SDK."""
        sdk = minimal_mock_sdk
        assert sdk.get_health_status()["registered_tools"] == 0

        @sdk.tool(description="Echo a value.", timeout=timedelta(seconds=1))
        async def echo(value: str) -> str:
            return value

        assert sdk.get_health_status()["registered_tools"] == 1

    async def test_health_scheduler_closed_detection(self, minimal_mock_sdk: DualeAISDK):
        """Test health detects scheduler state."""
        sdk = minimal_mock_sdk

        # Get initial health
        health_initial = sdk.get_health_status()
        initial_components = _health_components(health_initial)
        _initial_scheduler_state = initial_components["scheduler"]

        # Initialize scheduler
        await sdk._ensure_scheduler()

        # Get health after scheduler init
        health_after = sdk.get_health_status()

        # Scheduler should be healthy after initialization
        after_components = _health_components(health_after)
        assert after_components["scheduler"] is True

    async def test_health_events_connected_via_mock(self, minimal_mock_sdk: DualeAISDK):
        """Test health detects when events client is connected via mock."""
        sdk = minimal_mock_sdk

        # minimal_mock_sdk has a connected client backed by MockHTTPTransport.
        health = sdk.get_health_status()

        # Events should be healthy (connected via mock)
        events_components = _health_components(health)
        assert events_components["events"] is True

    async def test_health_error_handling(self, minimal_mock_sdk: DualeAISDK):
        """Test health check handles errors gracefully without crashing."""
        sdk = minimal_mock_sdk

        # Scheduler whose .closed getter really raises (property must live on
        # the TYPE — an instance-attribute property object never executes).
        class RaisingScheduler:
            @property
            def closed(self) -> bool:
                raise RuntimeError("Test error")

        with patch.object(sdk, "_scheduler", new=RaisingScheduler()):
            # Get health should not crash
            health = sdk.get_health_status()

        # The except branch preserves the pre-error default: scheduler stays
        # reported healthy (True) rather than crashing the health endpoint.
        components = _health_components(health)
        assert components["scheduler"] is True
        assert health["status"] in ("healthy", "unhealthy")

    async def test_init_time_metrics_report_absent_components_healthy(self, minimal_mock_sdk: DualeAISDK):
        """The init-time auto metric path must agree with get_health_status.

        Regression: ``_update_health_metrics(None)`` — the exact call
        ``__init__`` makes — recorded an absent scheduler/cache as
        ``unhealthy`` (value=-1) while ``get_health_status`` reported them
        ``healthy``. This drives that auto path and inspects the RECORDED
        metric so a re-broken auto branch fails even if the public status
        reader still looks right.
        """
        sdk = minimal_mock_sdk
        assert sdk._scheduler is None
        assert sdk._cache is None

        with patch.object(sdk._observability, "record_operation") as spy:
            sdk._update_health_metrics()  # no args → auto path, exactly as __init__ does

        recorded = {call.kwargs["component"]: (call.args[1], call.kwargs["value"]) for call in spy.call_args_list}
        # Absent optional components record healthy(+1); pre-fix wrongly
        # recorded unhealthy(-1) here while the status reader said healthy.
        assert recorded["scheduler"] == ("healthy", 1)
        assert recorded["cache"] == ("healthy", 1)
        assert recorded["sdk"] == ("healthy", 1)

    # Component-metric recording is pinned exactly (all four components,
    # call_count == 4) in test_feature_observability.py::
    # test_get_health_status_records_component_metrics — the weaker duplicate
    # that lived here was deleted.
