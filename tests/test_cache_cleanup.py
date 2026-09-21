"""Tests for automatic cache cleanup with timedelta-based intervals."""

import asyncio
from datetime import timedelta
from unittest.mock import patch

import pytest

from dualeai.cache import CacheConfig, MockCacheBackend, SQLiteCacheBackend, create_cache_backend


@pytest.mark.unit
class TestAutomaticCleanup:
    """Test automatic cleanup background task."""

    async def test_cleanup_interval_uses_timedelta(self):
        """Verify cleanup_interval is timedelta, not int/float."""
        config = CacheConfig(cleanup_interval=timedelta(seconds=5))
        assert config.cleanup_interval == timedelta(seconds=5)
        assert isinstance(config.cleanup_interval, timedelta)

    async def test_cleanup_always_enabled(self):
        """Verify cleanup is ALWAYS enabled (no disable flag)."""
        _config = CacheConfig()
        # No cleanup_enabled field should exist - verify via model_fields
        assert "cleanup_enabled" not in CacheConfig.model_fields

    async def test_sqlite_cleanup_loop_runs(self):
        """Test SQLite automatic cleanup removes expired entries."""
        config = CacheConfig(cleanup_interval=timedelta(seconds=1))
        cache = SQLiteCacheBackend(":memory:", "12345678-1234-5678-1234-123456789012", config)
        await cache.ensure_initialized()
        await cache.start_cleanup_loop()

        # Set with 1 second TTL
        await cache.set("key1", "value1", ttl=timedelta(seconds=1))

        # Wait for expiration + cleanup
        await asyncio.sleep(2.5)

        # Should be cleaned up
        result = await cache.get("key1")
        assert result is None

        await cache.close()

    async def test_cleanup_task_stops_on_close(self):
        """Verify cleanup task is cancelled when cache closes."""
        cache = MockCacheBackend("test-tenant")
        await cache.ensure_initialized()
        await cache.start_cleanup_loop()

        assert cache._cleanup_task is not None
        assert not cache._cleanup_task.done()

        await cache.close()

        # Task should be cancelled and cleared
        assert cache._cleanup_task is None

    async def test_factory_starts_cleanup_automatically(self):
        """Verify create_cache_backend() starts cleanup loop."""
        cache = await create_cache_backend(tenant_id="test-tenant", redis_url="mock://test")

        # Cleanup should be running
        assert cache._cleanup_task is not None
        assert not cache._cleanup_task.done()

        await cache.close()

    async def test_default_sqlite_path_uses_dualeai_namespace(self, tmp_path):
        """The default local cache lives under the Duale AI application directory."""
        with (
            patch("dualeai.cache.Path.home", return_value=tmp_path),
            pytest.warns(RuntimeWarning, match="Using SQLite cache backend"),
        ):
            cache = await create_cache_backend(tenant_id="test-tenant", redis_url=None)

        try:
            assert isinstance(cache, SQLiteCacheBackend)
            assert cache.db_path == tmp_path / ".dualeai" / "cache" / "test-tenant" / "cache.db"
        finally:
            await cache.close()

    async def test_sqlite_cleanup_returns_count(self):
        """Test SQLite cleanup_expired() returns accurate count."""
        cache = SQLiteCacheBackend(":memory:", "12345678-1234-5678-1234-123456789012")
        await cache.ensure_initialized()

        # Set 3 entries with 0.5s TTL
        await cache.set("key1", "val1", ttl=timedelta(seconds=0.5))
        await cache.set("key2", "val2", ttl=timedelta(seconds=0.5))
        await cache.set("key3", "val3", ttl=timedelta(seconds=0.5))

        # Wait for expiration
        await asyncio.sleep(1)

        # Manual cleanup should return 3
        count = await cache.cleanup_expired()
        assert count == 3

        await cache.close()

    async def test_mock_cleanup_expired_returns_count(self):
        """Test Mock cleanup_expired() returns accurate count."""
        cache = MockCacheBackend("test-tenant")
        await cache.ensure_initialized()

        # Set entries with short TTL
        await cache.set("key1", "val1", ttl=timedelta(seconds=0.1))
        await cache.set("key2", "val2", ttl=timedelta(seconds=0.1))

        # Wait for expiration
        await asyncio.sleep(0.5)

        # Manual cleanup should return 2
        count = await cache.cleanup_expired()
        assert count == 2

        await cache.close()

    async def test_cleanup_loop_handles_errors_gracefully(self):
        """Test cleanup loop continues running despite errors."""
        cache = MockCacheBackend("test-tenant", CacheConfig(cleanup_interval=timedelta(milliseconds=1)))
        await cache.ensure_initialized()
        second_attempt = asyncio.Event()
        attempts = 0

        async def failing_cleanup():
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                second_attempt.set()
            raise ValueError("Test error")

        with patch.object(cache, "cleanup_expired", new=failing_cleanup):
            await cache.start_cleanup_loop()
            await asyncio.wait_for(second_attempt.wait(), timeout=1)

        assert attempts >= 2
        assert cache._cleanup_task is not None
        assert not cache._cleanup_task.done()

        await cache.close()

    async def test_multiple_start_cleanup_is_idempotent(self):
        """Test starting cleanup multiple times is safe."""
        cache = MockCacheBackend("test-tenant")
        await cache.ensure_initialized()

        await cache.start_cleanup_loop()
        task1 = cache._cleanup_task

        # Start again - should be no-op
        await cache.start_cleanup_loop()
        task2 = cache._cleanup_task

        # Should be the same task
        assert task1 is task2

        await cache.close()

    async def test_stop_cleanup_without_task_is_safe(self):
        """Test stopping cleanup when no task exists is safe."""
        cache = MockCacheBackend("test-tenant")
        await cache.ensure_initialized()

        # Stop without starting - should not raise
        await cache.stop_cleanup_loop()

        await cache.close()
