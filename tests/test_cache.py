"""Consolidated cache tests for all backend types.

This module unifies cache testing across Redis, SQLite, and Mock backends using
parametrized tests for consistency and maintainability. All tests use deterministic
patterns without timing dependencies.
"""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
import redis.asyncio as redis
from pydantic import ValidationError

from dualeai.cache import CacheableValue, CacheBackend, CacheConfig, RedisCacheBackend
from tests.conftest import CacheFactory


@pytest.mark.unit
def test_cache_config_has_no_ineffective_capacity_settings() -> None:
    assert not {"max_entries", "max_size_bytes", "lru_eviction_enabled", "eviction_batch_size"} & set(
        CacheConfig.model_fields
    )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CacheConfig.model_validate({"max_size_bytes": 10_000_000})


def _create_mock_redis_backend() -> tuple[RedisCacheBackend, AsyncMock]:
    backend = RedisCacheBackend(
        redis_url="redis://localhost:6379/0",
        tenant_id="tenant-test",
        config=CacheConfig(ttl_jitter_enabled=False),
    )
    client = AsyncMock(spec=redis.Redis)
    client.set = AsyncMock()
    client.setex = AsyncMock()
    backend._redis = client
    backend._initialized = True
    return backend, client


@pytest.mark.unit
class TestRedisCacheCommands:
    """Redis command contract tests at the redis-py boundary."""

    async def test_set_with_ttl_uses_set_ex(self):
        """A positive TTL uses the supported atomic SET EX command form."""
        backend, client = _create_mock_redis_backend()
        value: CacheableValue = {"data": "value"}

        await backend.set("key", value, ttl=timedelta(seconds=60))

        # Independent literal — the on-wire payload is the JSON encoding.
        client.set.assert_awaited_once_with("dualeai:sdk:tenant-test:key", '{"data": "value"}', ex=60)
        client.setex.assert_not_awaited()

    async def test_set_without_ttl_uses_plain_set(self):
        """A missing TTL keeps the key persistent."""
        backend, client = _create_mock_redis_backend()

        await backend.set("key", "value")

        client.set.assert_awaited_once_with("dualeai:sdk:tenant-test:key", '"value"')
        client.setex.assert_not_awaited()

    async def test_clear_deletes_only_the_backend_namespace(self):
        """Redis clear targets only the current backend namespace."""
        backend, client = _create_mock_redis_backend()
        scanned: list[dict[str, str | int]] = []

        async def scan_iter(*, match: str, count: int):
            scanned.append({"match": match, "count": count})
            yield "dualeai:sdk:tenant-test:first"
            yield "dualeai:sdk:tenant-test:second"

        client.scan_iter = scan_iter
        client.delete = AsyncMock()
        client.flushdb = AsyncMock()

        await backend.clear()

        assert scanned == [{"match": "dualeai:sdk:tenant-test:*", "count": 500}]
        client.delete.assert_awaited_once_with(
            "dualeai:sdk:tenant-test:first",
            "dualeai:sdk:tenant-test:second",
        )
        client.flushdb.assert_not_awaited()

    async def test_clear_reports_a_redis_failure(self):
        """A failed destructive request must not look successful to the caller."""
        backend, client = _create_mock_redis_backend()

        class FailingScan:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise redis.RedisError("scan failed")

        def scan_iter(*, match: str, count: int):
            del match, count
            return FailingScan()

        client.scan_iter = scan_iter

        with pytest.raises(redis.RedisError, match="scan failed"):
            await backend.clear()

    async def test_tls_url_reaches_redis_without_reaching_logs(self):
        """Redis receives its TLS URL while logs omit its userinfo and full value."""
        redis_url = "rediss://sdk-user:fake-secret@cache.example:6380/0"
        backend = RedisCacheBackend(redis_url=redis_url, tenant_id="tenant-test")
        client = AsyncMock(spec=redis.Redis)
        client.ping = AsyncMock()

        with (
            patch("dualeai.cache.redis.from_url", return_value=client) as from_url,
            patch("dualeai.cache.logger.info") as log_info,
        ):
            await backend.ensure_initialized()

        from_url.assert_called_once_with(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            retry_on_timeout=True,
        )
        rendered_events = repr(log_info.call_args_list)
        assert redis_url not in rendered_events
        assert "fake-secret" not in rendered_events


@pytest.mark.unit
class TestCacheBasicOperations:
    """Basic cache operations tests for all backends."""

    async def test_get_set_basic(self, cache_backend: CacheBackend[object]):
        """Test basic get/set operations."""
        key = "test_key"
        value = {"data": "test_value", "number": 42}

        # Set value
        await cache_backend.set(key, value)

        # Get value
        result = await cache_backend.get(key)
        assert result == value

    async def test_get_nonexistent(self, cache_backend: CacheBackend[object]):
        """Test getting non-existent key returns None."""
        result = await cache_backend.get("nonexistent_key")
        assert result is None

    async def test_delete(self, cache_backend: CacheBackend[object]):
        """Test delete operation."""
        key = "delete_test"
        value = "test_data"

        # Set and verify
        await cache_backend.set(key, value)
        assert await cache_backend.get(key) == value

        # Delete and verify
        await cache_backend.delete(key)
        assert await cache_backend.get(key) is None

    async def test_clear(self, cache_backend: CacheBackend[object]):
        """Test clear operation removes all keys in the backend namespace."""
        # Set multiple keys
        keys = ["key1", "key2", "key3"]
        for key in keys:
            await cache_backend.set(key, f"value_{key}")

        # Verify all exist
        for key in keys:
            assert await cache_backend.get(key) is not None

        # Clear cache
        await cache_backend.clear()

        # Verify all removed
        for key in keys:
            assert await cache_backend.get(key) is None

    async def test_overwrite(self, cache_backend: CacheBackend[object]):
        """Test overwriting existing key."""
        key = "overwrite_test"

        # Set initial value
        await cache_backend.set(key, "initial")
        assert await cache_backend.get(key) == "initial"

        # Overwrite
        await cache_backend.set(key, "updated")
        assert await cache_backend.get(key) == "updated"

    async def test_json_scalar_string_round_trips_as_string(self, cache_backend: CacheBackend[object]):
        """A JSON-scalar string must survive round-trip as a str on every backend.

        Regression: the Mock backend stored bare strings raw and json.loads'd
        them on read, so ``set("k", "42")`` returned int ``42`` while Redis and
        SQLite returned ``"42"``. All backends must preserve the type identically.
        """
        await cache_backend.set("scalar_digits", "42")
        digits = await cache_backend.get("scalar_digits")
        assert digits == "42"
        assert isinstance(digits, str)

        # A JSON-boolean-looking string must not decode to a bool either.
        await cache_backend.set("scalar_bool", "true")
        boolean = await cache_backend.get("scalar_bool")
        assert boolean == "true"
        assert isinstance(boolean, str)


@pytest.mark.unit
class TestCacheNamespaceIsolation:
    """Test caller-supplied namespace separation in cache backends."""

    @pytest.mark.parametrize("cache_factory", ["sqlite"], indirect=True)
    async def test_namespace_isolation(self, cache_factory: CacheFactory):
        """Different namespace prefixes isolate otherwise identical keys."""
        cache1 = await cache_factory("12345678-1234-5678-9012-123456789001")
        cache2 = await cache_factory("12345678-1234-5678-9012-123456789002")

        await cache1.set("shared_key", "tenant1_value")
        await cache2.set("shared_key", "tenant2_value")

        assert await cache1.get("shared_key") == "tenant1_value"
        assert await cache2.get("shared_key") == "tenant2_value"

        await cache1.clear()
        assert await cache1.get("shared_key") is None
        assert await cache2.get("shared_key") == "tenant2_value"


@pytest.mark.unit
class TestCacheValidation:
    """Test cache key and value validation."""

    async def test_empty_key_rejected(self, cache_backend: CacheBackend[object]):
        """Test that empty keys are rejected."""
        with pytest.raises(ValueError, match="Cache key cannot be empty"):
            await cache_backend.set("", "value")

        with pytest.raises(ValueError, match="Cache key cannot be empty"):
            await cache_backend.get("")

    async def test_long_key_rejected(self, cache_backend: CacheBackend[object]):
        """Test that overly long keys are rejected."""
        # Create a key longer than 250 characters
        long_key = "x" * 251

        with pytest.raises(ValueError, match="Cache key too long"):
            await cache_backend.set(long_key, "value")

    async def test_invalid_characters_in_key(self, cache_backend: CacheBackend[object]):
        """Test that keys with invalid characters are handled."""
        # These should work but be sanitized internally
        special_keys = ["key:with:colons", "key/with/slashes", "key with spaces"]

        for key in special_keys:
            await cache_backend.set(key, f"value_for_{key}")
            result = await cache_backend.get(key)
            assert result == f"value_for_{key}"


@pytest.mark.unit
class TestCacheStressAndEdgeCases:
    """Stress tests and edge cases for cache backends."""

    async def test_extremely_large_data(self, cache_backend: CacheBackend[object]):
        """Test handling extremely large data objects."""
        # 10MB of data
        large_data = {"content": "x" * (10 * 1024 * 1024)}
        key = "extreme_large"

        await cache_backend.set(key, large_data)
        result = await cache_backend.get(key)
        assert result == large_data

    async def test_rapid_key_creation_and_deletion(self, cache_backend: CacheBackend[object]):
        """Test rapid creation and deletion of many keys."""
        # Create 1000 keys rapidly
        for i in range(1000):
            await cache_backend.set(f"rapid_{i}", f"value_{i}")

        # Verify all exist
        for i in range(0, 1000, 100):  # Sample every 100th
            result = await cache_backend.get(f"rapid_{i}")
            assert result == f"value_{i}"

        # Delete all rapidly
        for i in range(1000):
            await cache_backend.delete(f"rapid_{i}")

        # Verify all deleted
        for i in range(0, 1000, 100):  # Sample every 100th
            result = await cache_backend.get(f"rapid_{i}")
            assert result is None

    async def test_binary_data_edge_cases(self, cache_backend: CacheBackend[object]):
        """Test handling of binary-like data structures."""
        binary_data = {
            "bytes_as_list": list(b"hello world"),
            "unicode_points": [ord(c) for c in "hello 世界"],
            "mixed_encoding": "café résumé naïve",
        }

        await cache_backend.set("binary_test", binary_data)
        result = await cache_backend.get("binary_test")
        assert result == binary_data

    async def test_deeply_nested_structures(self, cache_backend: CacheBackend[object]):
        """Test very deeply nested data structures."""
        # Create 20-level deep nesting
        deep_data: dict[str, object] = {"level": 0}
        current: dict[str, object] = deep_data
        for i in range(1, 20):
            next_level: dict[str, object] = {"level": i}
            current["nested"] = next_level
            current = next_level
        current["final"] = "deep_value"

        await cache_backend.set("deep_nest", deep_data)
        result = await cache_backend.get("deep_nest")

        assert result == deep_data

    async def test_high_frequency_updates_same_key(self, cache_backend: CacheBackend[object]):
        """Test high-frequency updates to the same key."""
        key = "high_freq_key"

        # Rapidly update the same key 500 times
        for i in range(500):
            await cache_backend.set(key, {"iteration": i, "data": f"update_{i}"})

        # Final value should be the last update
        result = await cache_backend.get(key)
        assert result == {"iteration": 499, "data": "update_499"}

    async def test_memory_pressure_simulation(self, cache_backend: CacheBackend[object]):
        """Simulate memory pressure with many large objects."""
        # Create 100 moderately large objects (100KB each)
        large_objects = []
        for i in range(100):
            obj = {
                "id": i,
                "data": "x" * (100 * 1024),  # 100KB string
                "metadata": {"created": i, "type": "test"},
            }
            large_objects.append(obj)
            await cache_backend.set(f"memory_pressure_{i}", obj)

        # Verify all objects are still accessible
        for i in range(0, 100, 10):  # Check every 10th object
            result = await cache_backend.get(f"memory_pressure_{i}")
            expected = large_objects[i]
            assert result == expected
            data = expected["data"]
            assert isinstance(data, str)
            assert len(data) == 100 * 1024

        # Clear to free memory
        await cache_backend.clear()

    @pytest.mark.parametrize("cache_factory", ["sqlite"], indirect=True)
    async def test_sqlite_transaction_integrity(self, cache_factory: CacheFactory):
        """Test SQLite cache transaction integrity under concurrent access."""
        cache_backend = await cache_factory()

        # Simulate concurrent database operations
        async def batch_operations(batch_id: int):
            for i in range(50):
                key = f"batch_{batch_id}_item_{i}"
                await cache_backend.set(key, {"batch": batch_id, "item": i})

            # Verify immediately after batch
            for i in range(0, 50, 10):  # Check every 10th
                key = f"batch_{batch_id}_item_{i}"
                result = await cache_backend.get(key)
                assert result == {"batch": batch_id, "item": i}

        # Run 5 concurrent batches
        tasks = [batch_operations(batch_id) for batch_id in range(5)]
        await asyncio.gather(*tasks)

        # Final verification - all data should be present
        for batch_id in range(5):
            for i in [0, 25, 49]:  # Check first, middle, last of each batch
                key = f"batch_{batch_id}_item_{i}"
                result = await cache_backend.get(key)
                assert result == {"batch": batch_id, "item": i}

    async def test_error_recovery_patterns(self, cache_backend: CacheBackend[object]):
        """Test error recovery patterns in cache operations."""
        # Test setting valid data after potential errors
        await cache_backend.set("recovery_test", "initial_value")

        # Overwrite with different type
        await cache_backend.set("recovery_test", {"type": "dict"})
        result = await cache_backend.get("recovery_test")
        assert result == {"type": "dict"}

        # Overwrite with None
        await cache_backend.set("recovery_test", None)
        result = await cache_backend.get("recovery_test")
        assert result is None

        # Set valid data again
        await cache_backend.set("recovery_test", "recovered_value")
        result = await cache_backend.get("recovery_test")
        assert result == "recovered_value"

    async def test_boundary_conditions(self, cache_backend: CacheBackend[object]):
        """Test various boundary conditions in cache operations."""
        # Empty containers
        await cache_backend.set("empty_dict", {})
        await cache_backend.set("empty_list", [])
        await cache_backend.set("empty_string", "")

        assert await cache_backend.get("empty_dict") == {}
        assert await cache_backend.get("empty_list") == []
        assert await cache_backend.get("empty_string") == ""

        # Single character/element
        await cache_backend.set("single_char", "x")
        await cache_backend.set("single_item_list", [42])
        await cache_backend.set("single_key_dict", {"key": "value"})

        assert await cache_backend.get("single_char") == "x"
        assert await cache_backend.get("single_item_list") == [42]
        assert await cache_backend.get("single_key_dict") == {"key": "value"}

        # Maximum reasonable values
        max_int = 2**63 - 1
        min_int = -(2**63)

        await cache_backend.set("max_int", max_int)
        await cache_backend.set("min_int", min_int)

        assert await cache_backend.get("max_int") == max_int
        assert await cache_backend.get("min_int") == min_int
