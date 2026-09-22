"""Tests for cached activity execution.

The suite uses the real SDK with ``MockCacheBackend`` and an injected HTTP
transport where a network boundary is needed.
"""

from datetime import timedelta

import pytest

from dualeai import DualeAISDK
from dualeai.cache import MockCacheBackend
from dualeai.sdk import JsonValue
from tests.conftest import TestTenantIDs


@pytest.mark.unit
class TestUnitActivityCaching:
    """Test activity caching with REAL SDK execute_activity logic."""

    async def test_execute_activity_caches_result(self, minimal_mock_sdk: DualeAISDK):
        """Test execute_activity caches function result on first execution.

        Observable behaviour: a second invocation with identical args
        returns the cached value WITHOUT re-running the function.
        We assert on observable behaviour (call count, equal results)
        — not on the internal cache-key format, which would couple
        the test to ``execute_activity`` implementation details.
        """
        sdk = minimal_mock_sdk
        sdk.cache = MockCacheBackend(tenant_id=TestTenantIDs.DEFAULT)

        call_count = 0

        async def fetch_user_data(user_id: str) -> dict[str, JsonValue]:
            nonlocal call_count
            call_count += 1
            return {"user_id": user_id, "name": "Test User"}

        first = await sdk.execute_activity(
            fetch_user_data,
            cache_ttl=timedelta(minutes=5),
            max_retries=1,
            user_id="user-123",
        )
        assert first == {"user_id": "user-123", "name": "Test User"}
        assert call_count == 1

        # Second call with identical args: function not re-invoked.
        second = await sdk.execute_activity(
            fetch_user_data,
            cache_ttl=timedelta(minutes=5),
            max_retries=1,
            user_id="user-123",
        )
        assert second == first
        assert call_count == 1

    async def test_cache_hit_returns_cached_value(self, minimal_mock_sdk: DualeAISDK):
        """Test cache hit returns cached value without re-execution."""
        sdk = minimal_mock_sdk
        mock_cache = MockCacheBackend(tenant_id=TestTenantIDs.DEFAULT)
        sdk.cache = mock_cache

        # Define test activity
        execution_count = 0

        async def expensive_computation(value: int) -> int:
            nonlocal execution_count
            execution_count += 1
            return value * 2

        # First execution - populates cache
        result1 = await sdk.execute_activity(
            expensive_computation,
            cache_ttl=timedelta(minutes=10),
            max_retries=1,
            value=42,
        )
        assert result1 == 84
        assert execution_count == 1

        # Second execution - should hit cache
        result2 = await sdk.execute_activity(
            expensive_computation,
            cache_ttl=timedelta(minutes=10),
            max_retries=1,
            value=42,
        )
        assert result2 == 84
        assert execution_count == 1  # Should not increment

    async def test_cache_miss_executes_function(self, minimal_mock_sdk: DualeAISDK):
        """Different args produce independent cache entries.

        Observable behaviour: each unique-arg call returns its own
        result and does not collide with a prior cache entry.
        """
        sdk = minimal_mock_sdk
        sdk.cache = MockCacheBackend(tenant_id=TestTenantIDs.DEFAULT)

        call_log: list[str] = []

        async def process_data(input_val: str) -> str:
            call_log.append(input_val)
            return f"processed_{input_val}"

        result1 = await sdk.execute_activity(
            process_data,
            cache_ttl=timedelta(minutes=5),
            max_retries=1,
            input_val="data1",
        )
        result2 = await sdk.execute_activity(
            process_data,
            cache_ttl=timedelta(minutes=5),
            max_retries=1,
            input_val="data2",
        )
        assert result1 == "processed_data1"
        assert result2 == "processed_data2"
        assert call_log == ["data1", "data2"]

        # Repeating either call returns the cached value without re-running.
        cached1 = await sdk.execute_activity(
            process_data,
            cache_ttl=timedelta(minutes=5),
            max_retries=1,
            input_val="data1",
        )
        cached2 = await sdk.execute_activity(
            process_data,
            cache_ttl=timedelta(minutes=5),
            max_retries=1,
            input_val="data2",
        )
        assert cached1 == result1
        assert cached2 == result2
        assert call_log == ["data1", "data2"]  # No re-runs.

    async def test_cache_ttl_expires_correctly(self, minimal_mock_sdk: DualeAISDK):
        """Test cache entries expire after TTL."""
        sdk = minimal_mock_sdk
        mock_cache = MockCacheBackend(tenant_id=TestTenantIDs.DEFAULT)
        sdk.cache = mock_cache

        # Define test activity
        execution_count = 0

        async def time_sensitive_data() -> str:
            nonlocal execution_count
            execution_count += 1
            return f"data_v{execution_count}"

        # Execute with short TTL
        result1 = await sdk.execute_activity(
            time_sensitive_data,
            cache_ttl=timedelta(milliseconds=1),
            max_retries=1,
        )
        assert result1 == "data_v1"
        assert execution_count == 1

        # Wait for TTL expiration
        import asyncio

        await asyncio.sleep(0.01)

        # Execute cleanup to remove expired entries
        await mock_cache.cleanup_expired()

        # Second execution should miss cache (expired)
        result2 = await sdk.execute_activity(
            time_sensitive_data,
            cache_ttl=timedelta(minutes=10),
            max_retries=1,
        )
        assert result2 == "data_v2"
        assert execution_count == 2

    async def test_cache_distinguishes_identical_function_with_different_args(
        self, minimal_mock_sdk: DualeAISDK
    ) -> None:
        """Different arg combinations are cached independently.

        Observable behaviour: ``compute(x=10, y=20)`` and
        ``compute(x=10, y=21)`` each return their own result and
        re-runs of either return the cached value without
        re-invoking the function.
        """
        sdk = minimal_mock_sdk
        sdk.cache = MockCacheBackend(tenant_id=TestTenantIDs.DEFAULT)

        invocations: list[tuple[int, int]] = []

        async def compute(x: int, y: int) -> int:
            invocations.append((x, y))
            return x + y

        # Two distinct invocations populate two cache entries.
        first_a = await sdk.execute_activity(compute, cache_ttl=timedelta(minutes=5), max_retries=1, x=10, y=20)
        first_b = await sdk.execute_activity(compute, cache_ttl=timedelta(minutes=5), max_retries=1, x=10, y=21)
        assert first_a == 30
        assert first_b == 31
        assert invocations == [(10, 20), (10, 21)]

        # Re-running with the same args — function not re-invoked.
        second_a = await sdk.execute_activity(compute, cache_ttl=timedelta(minutes=5), max_retries=1, x=10, y=20)
        second_b = await sdk.execute_activity(compute, cache_ttl=timedelta(minutes=5), max_retries=1, x=10, y=21)
        assert second_a == first_a
        assert second_b == first_b
        assert invocations == [(10, 20), (10, 21)]

    async def test_separate_cache_backends_do_not_share_entries(self, config_factory):
        """Independently injected cache instances do not share entries."""
        from unittest.mock import AsyncMock

        from dualeai import DualeAISDK

        # Create two SDKs with different tenants
        sdk1 = DualeAISDK(config=config_factory(tenant_id=TestTenantIDs.DEFAULT), auto_start=False)
        sdk2 = DualeAISDK(config=config_factory(tenant_id=TestTenantIDs.ISOLATED), auto_start=False)

        # Avoid network work; this test focuses on explicitly injected caches.
        sdk1._events_client = AsyncMock()
        sdk2._events_client = AsyncMock()

        mock_cache1 = MockCacheBackend(tenant_id=TestTenantIDs.DEFAULT)
        mock_cache2 = MockCacheBackend(tenant_id=TestTenantIDs.ISOLATED)
        sdk1.cache = mock_cache1
        sdk2.cache = mock_cache2

        try:
            # Define test activity
            async def get_tenant_data(key: str) -> str:
                return f"data_for_{key}"

            # Execute for tenant 1
            result1 = await sdk1.execute_activity(
                get_tenant_data,
                cache_ttl=timedelta(minutes=10),
                max_retries=1,
                key="shared_key",
            )
            assert result1 == "data_for_shared_key"

            # Execute for tenant 2 with same key
            result2 = await sdk2.execute_activity(
                get_tenant_data,
                cache_ttl=timedelta(minutes=10),
                max_retries=1,
                key="shared_key",
            )
            assert result2 == "data_for_shared_key"

            # Caches are tenant-isolated: tenant1 cache only has its own
            # entry, tenant2 cache only has its own. Verifying this by
            # observing the SDK behaviour: tenant1's cache size grew by
            # exactly 1, ditto for tenant2.
            assert len(mock_cache1._data) == 1
            assert len(mock_cache2._data) == 1
            tenant1_keys = list(mock_cache1._data.keys())
            tenant2_keys = list(mock_cache2._data.keys())
            assert all(TestTenantIDs.DEFAULT in k for k in tenant1_keys)
            assert all(TestTenantIDs.ISOLATED in k for k in tenant2_keys)

        finally:
            await sdk1.cleanup()
            await sdk2.cleanup()

    async def test_execute_activity_with_retry_on_failure(self, minimal_mock_sdk: DualeAISDK):
        """Test execute_activity retries on failure before caching."""
        sdk = minimal_mock_sdk
        mock_cache = MockCacheBackend(tenant_id=TestTenantIDs.DEFAULT)
        sdk.cache = mock_cache

        # Define flaky activity that fails once then succeeds
        attempt_count = 0

        async def flaky_operation(value: str) -> str:
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count == 1:
                raise ValueError("Transient error")
            return f"success_{value}"

        # Execute with retries
        result = await sdk.execute_activity(
            flaky_operation,
            cache_ttl=timedelta(minutes=5),
            max_retries=3,
            value="test",
        )

        # Verify retry worked and result cached
        assert result == "success_test"
        assert attempt_count == 2  # Failed once, succeeded second time

        # The successful result is cached: a second invocation returns
        # the same value WITHOUT re-running the function.
        replay = await sdk.execute_activity(
            flaky_operation,
            cache_ttl=timedelta(minutes=5),
            max_retries=3,
            value="test",
        )
        assert replay == "success_test"
        assert attempt_count == 2  # No further invocations.

    async def test_execute_activity_without_cache_ttl(self, minimal_mock_sdk: DualeAISDK):
        """Test execute_activity without cache_ttl still executes."""
        sdk = minimal_mock_sdk
        mock_cache = MockCacheBackend(tenant_id=TestTenantIDs.DEFAULT)
        sdk.cache = mock_cache

        execution_count = 0

        async def uncached_operation(value: int) -> int:
            nonlocal execution_count
            execution_count += 1
            return value * 3

        # Execute without cache_ttl (None)
        result = await sdk.execute_activity(
            uncached_operation,
            cache_ttl=None,
            max_retries=1,
            value=7,
        )

        # Verify execution
        assert result == 21
        assert execution_count == 1

        # Even with cache_ttl=None, the result is cached (no expiry):
        # second call returns the same value without re-running.
        replay = await sdk.execute_activity(
            uncached_operation,
            cache_ttl=None,
            max_retries=1,
            value=7,
        )
        assert replay == 21
        assert execution_count == 1

    async def test_execute_activity_with_kwargs(self, minimal_mock_sdk: DualeAISDK):
        """Different kwarg values produce independent cache entries."""
        sdk = minimal_mock_sdk
        sdk.cache = MockCacheBackend(tenant_id=TestTenantIDs.DEFAULT)

        runs: list[tuple[int, int]] = []

        async def compute_with_kwargs(x: int, y: int = 5) -> int:
            runs.append((x, y))
            return x * y

        first = await sdk.execute_activity(compute_with_kwargs, cache_ttl=timedelta(minutes=5), max_retries=1, x=3, y=7)
        assert first == 21
        assert runs == [(3, 7)]

        # Repeat with same kwargs — cache hit, function not re-invoked.
        cached = await sdk.execute_activity(
            compute_with_kwargs, cache_ttl=timedelta(minutes=5), max_retries=1, x=3, y=7
        )
        assert cached == 21
        assert runs == [(3, 7)]

        # Different kwargs — fresh invocation.
        other = await sdk.execute_activity(compute_with_kwargs, cache_ttl=timedelta(minutes=5), max_retries=1, x=3, y=8)
        assert other == 24
        assert runs == [(3, 7), (3, 8)]

    async def test_execute_activity_sync_function_support(self, minimal_mock_sdk: DualeAISDK):
        """Sync functions are supported and cached identically to async."""
        sdk = minimal_mock_sdk
        sdk.cache = MockCacheBackend(tenant_id=TestTenantIDs.DEFAULT)

        runs: list[int] = []

        def sync_computation(value: int) -> int:
            runs.append(value)
            return value + 10

        first = await sdk.execute_activity(sync_computation, cache_ttl=timedelta(minutes=5), max_retries=1, value=5)
        assert first == 15
        assert runs == [5]

        # Cache hit on second call.
        replay = await sdk.execute_activity(sync_computation, cache_ttl=timedelta(minutes=5), max_retries=1, value=5)
        assert replay == 15
        assert runs == [5]
