"""Enhanced parameterized cache tests for comprehensive backend coverage.

This module provides additional test coverage for cache functionality using
parameterized tests across all backend implementations.
"""

import asyncio

import pytest

from dualeai.cache import CacheableValue
from dualeai.sdk import JsonValue
from tests.conftest import CacheFactory, TestTenantIDs


@pytest.mark.unit
class TestCacheDataTypeSupport:
    """Test cache support for various data types across all backends."""

    @pytest.mark.parametrize(
        "data_value",
        [
            "simple_string",
            {"nested": {"dict": "value", "number": 42}},
            ["list", "with", "multiple", "items"],
            42,
            3.14159,
            True,
            False,
            None,
            {"mixed": [1, "two", {"three": 3}, None]},
        ],
    )
    async def test_data_type_storage_retrieval(self, cache_factory: CacheFactory, data_value: CacheableValue):
        """Test storage and retrieval of various data types."""
        cache = await cache_factory()
        key = f"data_type_test_{type(data_value).__name__}"

        await cache.set(key, data_value)
        result = await cache.get(key)

        assert result == data_value

    @pytest.mark.parametrize(
        "data_size",
        [
            1,  # Very small
            1024,  # 1KB
            10240,  # 10KB
            102400,  # 100KB
        ],
    )
    async def test_large_data_storage(self, cache_factory: CacheFactory, data_size: int):
        """Test storage of various data sizes."""
        cache = await cache_factory()

        # Create test data of specified size
        large_data: dict[str, JsonValue] = {"content": "x" * data_size, "size": data_size}
        key = f"large_data_{data_size}"

        await cache.set(key, large_data)
        result = await cache.get(key)

        assert result == large_data
        content = large_data["content"]
        assert isinstance(content, str)
        assert len(content) == data_size

    @pytest.mark.parametrize(
        "unicode_content",
        [
            "Hello, 世界!",
            "Café résumé naïve 中文 العربية",
            "🚀 🌟 💫 ⭐ 🌙",  # Emojis
            "Ω ≈ ∑ ∞ ∂ ∆ ∇",  # Mathematical symbols
        ],
    )
    async def test_unicode_support(self, cache_factory: CacheFactory, unicode_content: str):
        """Test Unicode content storage and retrieval."""
        cache = await cache_factory()
        key = "unicode_test"

        await cache.set(key, unicode_content)
        result = await cache.get(key)

        assert result == unicode_content


@pytest.mark.unit
class TestCacheConcurrencyScenarios:
    """Test cache behavior under concurrent access patterns."""

    @pytest.mark.parametrize("num_concurrent", [5, 10, 20])
    async def test_concurrent_writes_different_keys(self, cache_factory: CacheFactory, num_concurrent: int):
        """Test concurrent writes to different keys."""
        cache = await cache_factory()

        async def write_task(task_id: int):
            key = f"concurrent_key_{task_id}"
            value: CacheableValue = {"task_id": task_id, "data": f"task_data_{task_id}"}
            await cache.set(key, value)
            return key, value

        # Execute concurrent writes
        tasks = [write_task(i) for i in range(num_concurrent)]
        results = await asyncio.gather(*tasks)

        # Verify all writes succeeded
        for key, expected_value in results:
            stored_value = await cache.get(key)
            assert stored_value == expected_value

    @pytest.mark.parametrize("num_operations", [10, 25, 50])
    async def test_concurrent_mixed_operations(self, cache_factory: CacheFactory, num_operations: int):
        """Test concurrent mix of read/write/delete operations."""
        cache = await cache_factory()

        # Pre-populate some data
        for i in range(5):
            await cache.set(f"base_key_{i}", f"base_value_{i}")

        operations_completed = []

        async def mixed_operation(op_id: int):
            op_type = op_id % 3

            if op_type == 0:  # Write
                key = f"mixed_key_{op_id}"
                value = f"mixed_value_{op_id}"
                await cache.set(key, value)
                operations_completed.append(("write", key, value))

            elif op_type == 1:  # Read
                key = f"base_key_{op_id % 5}"
                value = await cache.get(key)
                operations_completed.append(("read", key, value))

            else:  # Delete (if exists)
                key = f"base_key_{op_id % 5}"
                await cache.delete(key)
                operations_completed.append(("delete", key, None))

        # Execute concurrent operations
        tasks = [mixed_operation(i) for i in range(num_operations)]
        await asyncio.gather(*tasks)

        assert len(operations_completed) == num_operations

        # Reads raced with deletes on base_key_*: each must have observed the
        # seeded value or a deleted miss — never garbage.
        for op, key, value in operations_completed:
            if op == "read":
                index = key.removeprefix("base_key_")
                assert value in (f"base_value_{index}", None)

        # Post-state: written keys hold their exact values; deleted keys are gone.
        for op, key, value in operations_completed:
            if op == "write":
                assert await cache.get(key) == value
            elif op == "delete":
                assert await cache.get(key) is None

    async def test_concurrent_same_key_updates(self, cache_factory: CacheFactory):
        """Test concurrent updates to the same key."""
        cache = await cache_factory()
        key = "concurrent_updates"

        update_results = []

        async def update_task(task_id: int):
            value = f"updated_by_task_{task_id}"
            await cache.set(key, value)
            # Read back to verify
            result = await cache.get(key)
            update_results.append((task_id, result))

        # Execute concurrent updates
        tasks = [update_task(i) for i in range(10)]
        await asyncio.gather(*tasks)

        # One of the values should be stored
        final_value = await cache.get(key)
        assert final_value is not None
        assert isinstance(final_value, str)
        assert final_value.startswith("updated_by_task_")


@pytest.mark.unit
class TestCacheNamespaceIsolation:
    """Test caller-supplied namespace separation across cache backends."""

    @pytest.mark.parametrize(
        "tenant_config",
        [
            (TestTenantIDs.DEFAULT, TestTenantIDs.INTEGRATION),
            (TestTenantIDs.ISOLATED, TestTenantIDs.CROSS_TENANT_A),
            (TestTenantIDs.CROSS_TENANT_A, TestTenantIDs.CROSS_TENANT_B),
        ],
    )
    async def test_namespace_data_isolation(self, cache_factory: CacheFactory, tenant_config: tuple[str, str]):
        """Different namespace prefixes return their respective values."""
        tenant1_id, tenant2_id = tenant_config

        cache1 = await cache_factory(tenant1_id)
        cache2 = await cache_factory(tenant2_id)

        # Store data under each namespace.
        key = "shared_key_name"
        value1: CacheableValue = {"tenant": tenant1_id, "data": "secret_data_1"}
        value2: CacheableValue = {"tenant": tenant2_id, "data": "secret_data_2"}

        await cache1.set(key, value1)
        await cache2.set(key, value2)

        # Verify each namespace resolves its own value.
        result1 = await cache1.get(key)
        result2 = await cache2.get(key)

        assert result1 == value1
        assert result2 == value2
        assert result1 != result2

    async def test_namespace_clear_isolation(self, cache_factory: CacheFactory):
        """Clearing one namespace leaves the other namespace intact."""
        cache1 = await cache_factory(TestTenantIDs.DEFAULT)
        cache2 = await cache_factory(TestTenantIDs.INTEGRATION)

        # Store data in both namespaces.
        keys = ["key1", "key2", "key3"]
        for key in keys:
            await cache1.set(key, f"tenant1_{key}")
            await cache2.set(key, f"tenant2_{key}")

        # Verify data exists in both
        for key in keys:
            assert await cache1.get(key) == f"tenant1_{key}"
            assert await cache2.get(key) == f"tenant2_{key}"

        # Clear first tenant
        await cache1.clear()

        # First namespace is empty; the second is unchanged.
        for key in keys:
            assert await cache1.get(key) is None
            assert await cache2.get(key) == f"tenant2_{key}"


@pytest.mark.unit
class TestCachePerformancePatterns:
    """Test cache performance patterns and bulk operations."""

    @pytest.mark.parametrize("batch_size", [10, 50, 100])
    async def test_bulk_operations(self, cache_factory: CacheFactory, batch_size: int):
        """Test bulk cache operations performance patterns."""
        cache = await cache_factory()

        # Bulk write
        write_tasks = []
        for i in range(batch_size):
            key = f"bulk_key_{i}"
            value: CacheableValue = {"index": i, "data": f"bulk_data_{i}"}
            write_tasks.append(cache.set(key, value))

        await asyncio.gather(*write_tasks)

        # Bulk read
        read_tasks = []
        for i in range(batch_size):
            key = f"bulk_key_{i}"
            read_tasks.append(cache.get(key))

        results = await asyncio.gather(*read_tasks)

        # Verify all results
        assert len(results) == batch_size
        for i, result in enumerate(results):
            expected = {"index": i, "data": f"bulk_data_{i}"}
            assert result == expected

    async def test_cache_turnover_pattern(self, cache_factory: CacheFactory):
        """Test high turnover cache usage pattern."""
        cache = await cache_factory()

        # Simulate high turnover: set -> get -> delete cycles
        for cycle in range(20):
            key = f"turnover_key_{cycle}"
            value = f"turnover_value_{cycle}"

            # Set
            await cache.set(key, value)

            # Verify set
            result = await cache.get(key)
            assert result == value

            # Delete
            await cache.delete(key)

            # Verify deleted
            result = await cache.get(key)
            assert result is None

    async def test_cache_memory_usage_pattern(self, cache_factory: CacheFactory):
        """Test cache behavior with memory usage patterns."""
        cache = await cache_factory()

        # Pattern 1: Many small items
        small_items: dict[str, CacheableValue] = {}
        for i in range(100):
            key = f"small_{i}"
            val: CacheableValue = {"id": i, "small": True}
            await cache.set(key, val)
            small_items[key] = val

        # Pattern 2: Few large items
        large_items: dict[str, CacheableValue] = {}
        for i in range(5):
            key = f"large_{i}"
            lval: CacheableValue = {"id": i, "data": "x" * 1000, "large": True}
            await cache.set(key, lval)
            large_items[key] = lval

        # Verify all items are accessible
        for key, expected in small_items.items():
            result = await cache.get(key)
            assert result == expected

        for key, expected in large_items.items():
            result = await cache.get(key)
            assert result == expected
