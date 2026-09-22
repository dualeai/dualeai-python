"""Cache backends for distributed (Redis) and local (SQLite) caching.

Backend behavior is covered by ``tests/test_cache.py``,
``tests/test_cache_enhanced.py``, and ``tests/test_cache_cleanup.py``.
No focused automated test currently covers the SDK's token-to-namespace
selection or token-rotation behavior.
"""

import asyncio
import contextlib
import json
import random
import warnings
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generic, TypeVar

import aiosqlite
import redis.asyncio as redis
import structlog
from pydantic import BaseModel, ConfigDict, Field
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from dualeai.constants import CacheLimits
from dualeai.models.json_value import JsonValue

# UTC constant removed - use timezone.utc directly

logger = structlog.get_logger(__name__)

REDIS_KEY_NAMESPACE = "dualeai:sdk"
REDIS_CLEAR_BATCH_SIZE = 500

# Type variables and definitions
T = TypeVar("T")

# Type alias for cacheable values
CacheableValue = JsonValue


class CacheConfig(BaseModel):
    """Cache TTL, cleanup, and compatibility limit settings.

    TTL jitter applies to every backend. ``cleanup_interval`` is used after
    :meth:`CacheBackend.start_cleanup_loop` is called; the factory starts that
    loop automatically, while direct backend construction does not.

    ``max_entries`` is enforced only by ``MockCacheBackend``. The current
    Redis and SQLite implementations do not enforce ``max_entries``,
    ``max_size_bytes``, ``lru_eviction_enabled``, or ``eviction_batch_size``;
    those fields are retained for configuration compatibility. Mock overflow
    eviction uses earliest expiry, not access-order LRU.
    """

    model_config = ConfigDict(use_attribute_docstrings=True)

    # Memory management
    max_entries: int = Field(
        default=100_000,
        ge=1000,
        le=10_000_000,
        description="Entry bound enforced by MockCacheBackend only",
    )
    max_size_bytes: int = Field(
        default=1_000_000_000,
        ge=10_000_000,
        description="Compatibility field; current backends do not enforce a byte-size bound",
    )

    # TTL jitter to prevent thundering herd
    ttl_jitter_enabled: bool = Field(default=True, description="Apply randomized jitter to TTL values")
    ttl_jitter_factor: float = Field(
        default=0.1,
        ge=0.0,
        le=0.5,
        description="Maximum proportional TTL variation in either direction",
    )

    # Compatibility eviction fields; current backends do not implement LRU.
    lru_eviction_enabled: bool = Field(
        default=True,
        description="Compatibility field; current backends do not implement access-order LRU",
    )
    eviction_batch_size: int = Field(
        default=1000,
        ge=100,
        le=10000,
        description="Compatibility field; current backends do not use this batch size",
    )

    # Cleanup interval used when a cleanup loop is started.
    cleanup_interval: timedelta = Field(
        default=timedelta(minutes=10),
        description="Automatic cleanup interval for expired entries (time-based background task)",
    )


class CacheBackend(ABC, Generic[T]):
    """Abstract cache backend with a caller-supplied key namespace.

    ``DualeAISDK`` supplies a SHA-256 fingerprint of its API token as the
    namespace. It does not use ``DualeAIConfig.tenant_id``; rotating a token
    therefore selects a new cache namespace.
    """

    def __init__(self, tenant_id: str, config: CacheConfig | None = None):
        """Initialize a cache backend with its key namespace and configuration.

        Args:
            tenant_id: Namespace prefix. Despite the historical parameter name,
                this need not be a tenant UUID.
            config: Cache configuration. See ``CacheConfig`` for which limits
                each backend enforces.
        """
        self.tenant_id = tenant_id
        self.config = config or CacheConfig()
        # Pre-bake the namespace prefix once. Cache get/set/delete calls
        # this on every operation; concatenation is faster than an
        # f-string format and the prefix never changes.
        self._tenant_prefix = f"{tenant_id}:"
        # Background cleanup task
        self._cleanup_task: asyncio.Task[None] | None = None

    def _get_tenant_key(self, key: str) -> str:
        """Prefix a cache key with this backend's namespace.

        Args:
            key: Base cache key

        Returns:
            The namespace prefix followed by ``key``.
        """
        return self._tenant_prefix + key

    def _validate_cache_key(self, key: str) -> None:
        """Validate cache key for security and formatting requirements.

        Args:
            key: Cache key to validate

        Raises:
            ValueError: If key is invalid or poses security risk
        """
        if not key:
            raise ValueError("Cache key cannot be empty")

        if len(key) > CacheLimits.MAX_KEY_LENGTH:  # Redis key limit is 512MB, but practical limit
            raise ValueError("Cache key too long (max 250 characters)")

        # Check for potentially unsafe characters that could cause injection
        unsafe_chars = ["\n", "\r", "\0"]  # Allow spaces and tabs for compatibility
        if any(char in key for char in unsafe_chars):
            raise ValueError("Cache key contains unsafe characters (newlines, null bytes)")

        # Allow colons in keys; the namespace prefix remains separate.

        # Prevent directory traversal-style attacks
        if ".." in key or key.startswith(("/", "\\")):
            raise ValueError("Cache key cannot contain path traversal patterns")

    def _validate_tenant_scoped_key(self, tenant_key: str) -> None:
        """Validate that a prefixed key belongs to this backend namespace.

        Args:
            tenant_key: Fully qualified namespaced key.

        Raises:
            ValueError: If the key has a different namespace prefix.
        """
        if not tenant_key.startswith(self._tenant_prefix):
            raise ValueError(
                f"Tenant key validation failed: key '{tenant_key}' does not belong to tenant '{self.tenant_id}'"
            )

    def _validate_and_scope_key(self, key: str) -> str:
        """Validate a user key, prefix it, and verify the resulting namespace.

        Centralises the 3-line prelude that get/set/delete all need
        (validate, scope, tenant-check). Returns the scoped key ready
        for backend use.
        """
        self._validate_cache_key(key)
        tenant_key = self._get_tenant_key(key)
        self._validate_tenant_scoped_key(tenant_key)
        return tenant_key

    def _apply_ttl_jitter(self, ttl: timedelta) -> timedelta:
        """Apply jitter to TTL to prevent thundering herd pattern.

        Returns the input unchanged if jitter is disabled or the TTL
        is below one second (jitter on sub-second TTLs would round
        down to zero in some backends — better to keep the original).
        Otherwise scales the TTL by a uniform factor in
        ``[1 - jitter_factor, 1 + jitter_factor]``.
        """
        if not self.config.ttl_jitter_enabled or ttl.total_seconds() < 1:
            return ttl

        jitter_range = self.config.ttl_jitter_factor
        jitter = random.uniform(1 - jitter_range, 1 + jitter_range)
        jittered_seconds = ttl.total_seconds() * jitter

        return timedelta(seconds=jittered_seconds)

    def _serialize_value(self, value: CacheableValue) -> str:
        """Serialize cache value to JSON string."""
        return json.dumps(value)

    def _deserialize_value(self, serialized: str) -> CacheableValue:
        """Deserialize JSON string to cache value."""
        try:
            return json.loads(serialized)
        except json.JSONDecodeError as e:
            logger.warning("Failed to deserialize cache value", error=str(e))
            raise

    @abstractmethod
    async def get(self, key: str) -> T | None:
        """Get a cached value from this backend namespace.

        Type Safety Rationale:
            - Returns T | None (explicit None) to indicate cache miss
            - All implementations return None for missing/expired keys
            - A stored JSON null is therefore indistinguishable from a miss

        Args:
            key: Base cache key; the backend prefixes it internally.

        Returns:
            Cached value or None if not found/expired
        """

    @abstractmethod
    async def set(self, key: str, value: T, ttl: timedelta | None = None) -> None:
        """Set a namespaced cached value with an optional TTL.

        Args:
            key: Base cache key; the backend prefixes it internally.
            value: Value to cache
            ttl: Time to live. ``None`` means no backend expiry.
        """

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Delete a value from this backend namespace.

        Args:
            key: Base cache key; the backend prefixes it internally.
        """

    @abstractmethod
    async def clear(self) -> None:
        """Clear cached values in this backend namespace."""

    @abstractmethod
    async def cleanup_expired(self) -> int:
        """Remove expired entries and return count removed.

        Backend-Specific Behavior:
            - Redis: Returns 0 (Redis expires keys automatically after SET with EX)
            - SQLite: Deletes expired rows, returns count (reclaims disk space)
            - Mock: Removes expired entries, returns count (maintains test accuracy)

        Called by:
            - Background cleanup loop (automatic, periodic)
            - SDK.cleanup_expired_cache() (manual, on-demand)

        Returns:
            Number of entries removed (0 for Redis)
        """

    @abstractmethod
    async def close(self) -> None:
        """Close the cache backend."""

    async def start_cleanup_loop(self) -> None:
        """Start automatic cleanup background task.

        Design Rationale:
            - Started automatically by ``create_cache_backend``
            - Directly constructed backends require an explicit call
            - Time-based (timedelta) for predictable resource management
            - Background task pattern with async cleanup loop
            - Redis: cleanup_expired() is no-op (TTL built-in), but loop runs
            - SQLite: cleanup_expired() reclaims disk, prevents unbounded growth
            - Mock: cleanup_expired() maintains accurate state for testing

        Side Effects:
            - Creates background asyncio.Task that must be cancelled in close()
            - Task runs indefinitely until stop_cleanup_loop() called
            - Logs debug message on each successful cleanup cycle (if count > 0)
        """
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            logger.debug("Cache cleanup loop started", tenant_id=self.tenant_id, interval=self.config.cleanup_interval)

    async def _cleanup_loop(self) -> None:
        """Periodic cleanup loop.

        Implementation Notes:
            - Sleeps BEFORE first cleanup
            - Catches CancelledError for graceful shutdown
            - Logs errors but continues running (resilient to transient failures)
            - Uses timedelta.total_seconds() for asyncio.sleep()
        """
        while True:
            try:
                await asyncio.sleep(self.config.cleanup_interval.total_seconds())
                count = await self.cleanup_expired()
                if count > 0:
                    logger.debug(
                        "Cache cleanup completed",
                        count=count,
                        tenant_id=self.tenant_id,
                        interval=self.config.cleanup_interval,
                    )
            except asyncio.CancelledError:  # noqa: PERF203  # Necessary for graceful shutdown
                logger.debug("Cache cleanup loop cancelled", tenant_id=self.tenant_id)
                break
            except Exception as e:
                logger.error("Error in cache cleanup loop", error=str(e), tenant_id=self.tenant_id, exc_info=True)
                # Continue running despite errors

    async def stop_cleanup_loop(self) -> None:
        """Stop cleanup background task.

        CRITICAL: Must be called BEFORE close() in all backends to prevent:
            - Task accessing closed resources (Redis connection, SQLite DB)
            - Warnings about tasks not being awaited
            - Resource leaks from background tasks

        Side Effects:
            - Cancels background task
            - Waits for task to finish (catches CancelledError)
            - Sets _cleanup_task to None for idempotency
        """
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task
            self._cleanup_task = None


class RedisCacheBackend(CacheBackend[CacheableValue]):
    """Redis cache with namespaced keys and best-effort item operations.

    After initialization, Redis/serialization failures in ``get`` become cache
    misses and failures in ``set`` or ``delete`` are logged and suppressed.
    ``clear`` and initialization failures still propagate. Redis itself owns TTL
    expiry, so ``cleanup_expired`` always returns zero.
    """

    def __init__(self, redis_url: str, tenant_id: str, config: CacheConfig | None = None):
        """Initialize a Redis cache backend with its key namespace.

        Args:
            redis_url: Redis connection URL
            tenant_id: Caller-supplied key namespace.
            config: Cache configuration for behavior and limits
        """
        super().__init__(tenant_id, config)
        self._tenant_prefix = f"{REDIS_KEY_NAMESPACE}:{tenant_id}:"
        self.redis_url = redis_url
        self._redis: redis.Redis | None = None
        self._initialized = False

    async def ensure_initialized(self) -> None:
        """Ensure Redis client is initialized with Tenacity retry."""
        if not self._initialized:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=1, min=1, max=5),
                retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
                reraise=True,
            ):
                with attempt:
                    self._redis = redis.from_url(
                        self.redis_url,
                        decode_responses=True,
                        socket_connect_timeout=2,
                        socket_timeout=2,
                        retry_on_timeout=True,
                    )
                    # Test connection
                    await self._redis.ping()
                    self._initialized = True
                    logger.info("Redis cache initialized")

    async def get(self, key: str) -> CacheableValue | None:
        """Get a namespaced value; failures after initialization become misses."""
        await self.ensure_initialized()
        assert self._redis is not None  # Guaranteed by ensure_initialized

        tenant_key = self._validate_and_scope_key(key)

        try:
            value = await self._redis.get(tenant_key)
            if value is not None:
                return self._deserialize_value(value)
            return None
        except (redis.RedisError, json.JSONDecodeError, OSError) as e:
            logger.warning("Redis get failed", tenant_key=tenant_key, error=str(e))
            return None

    async def set(self, key: str, value: CacheableValue, ttl: timedelta | None = None) -> None:
        """Set a namespaced value; Redis write failures are logged and suppressed."""
        await self.ensure_initialized()
        assert self._redis is not None  # Guaranteed by ensure_initialized

        tenant_key = self._validate_and_scope_key(key)

        try:
            serialized_value = self._serialize_value(value)

            if ttl:
                # Apply TTL jitter to prevent thundering herd
                jittered_ttl = self._apply_ttl_jitter(ttl)
                ttl_seconds = int(jittered_ttl.total_seconds())
                if ttl_seconds > 0:
                    # redis-py 8 deprecates setex(); SET with EX is the supported atomic equivalent.
                    # Ref: https://github.com/redis/redis-py/pull/4051
                    await self._redis.set(tenant_key, serialized_value, ex=ttl_seconds)
                else:
                    await self._redis.set(tenant_key, serialized_value)
            else:
                await self._redis.set(tenant_key, serialized_value)

        except (redis.RedisError, OSError, ValueError, TypeError) as e:
            logger.warning("Redis set failed", tenant_key=tenant_key, error=str(e))

    async def delete(self, key: str) -> None:
        """Delete a namespaced value; Redis failures are logged and suppressed."""
        await self.ensure_initialized()
        assert self._redis is not None  # Guaranteed by ensure_initialized

        tenant_key = self._validate_and_scope_key(key)

        try:
            await self._redis.delete(tenant_key)
        except (redis.RedisError, OSError) as e:
            logger.warning("Redis delete failed", tenant_key=tenant_key, error=str(e))

    async def clear(self) -> None:
        """Clear only keys in this backend namespace."""
        await self.ensure_initialized()
        assert self._redis is not None  # Guaranteed by ensure_initialized
        try:
            tenant_keys: list[str] = []
            async for tenant_key in self._redis.scan_iter(
                match=f"{self._tenant_prefix}*",
                count=REDIS_CLEAR_BATCH_SIZE,
            ):
                tenant_keys.append(tenant_key)
                if len(tenant_keys) == REDIS_CLEAR_BATCH_SIZE:
                    await self._redis.delete(*tenant_keys)
                    tenant_keys.clear()

            if tenant_keys:
                await self._redis.delete(*tenant_keys)
        except (redis.RedisError, OSError) as e:
            logger.warning("Redis clear failed", error=str(e))
            raise

    async def cleanup_expired(self) -> int:
        """Remove expired entries and return count removed."""
        # Redis handles TTL expiration automatically
        return 0

    async def close(self) -> None:
        """Close the Redis connection.

        CRITICAL: Stop cleanup BEFORE closing connection to prevent task accessing closed resource.
        """
        await self.stop_cleanup_loop()
        if self._redis:
            await self._redis.aclose()
            self._redis = None
            self._initialized = False


class SQLiteCacheBackend(CacheBackend[CacheableValue]):
    """SQLite cache with namespaced keys and explicit expiry cleanup.

    Unlike Redis item operations, database and serialization failures propagate.
    The backend creates the database parent directory during construction.
    """

    def __init__(self, db_path: str | Path, tenant_id: str, config: CacheConfig | None = None):
        """Initialize a SQLite cache backend with its key namespace.

        Args:
            db_path: Path to SQLite database file
            tenant_id: Caller-supplied key namespace.
            config: Cache configuration for behavior and limits
        """
        super().__init__(tenant_id, config)
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialized = False
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def ensure_initialized(self) -> None:
        """Ensure SQLite database is initialized with proper schema."""
        if not self._initialized:
            async with self._lock:
                if not self._initialized:
                    self._db = await aiosqlite.connect(str(self.db_path))
                    # Create tables with proper schema
                    await self._db.execute(
                        """
                        CREATE TABLE IF NOT EXISTS cache (
                            key TEXT PRIMARY KEY,
                            value TEXT NOT NULL,
                            expires_at REAL NULL,
                            created_at REAL NOT NULL
                        )
                    """
                    )
                    # Create index for cleanup queries
                    await self._db.execute("CREATE INDEX IF NOT EXISTS idx_cache_expires ON cache(expires_at)")
                    await self._db.commit()
                    self._initialized = True

    async def get(self, key: str) -> CacheableValue | None:
        """Get a namespaced value and propagate database/serialization errors."""
        tenant_key = self._validate_and_scope_key(key)
        await self.ensure_initialized()
        assert self._db is not None  # Guaranteed by ensure_initialized

        current_time = datetime.now(timezone.utc).timestamp()

        async with self._db.execute(
            "SELECT value FROM cache WHERE key = ? AND (expires_at IS NULL OR expires_at > ?)",
            (tenant_key, current_time),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return self._deserialize_value(row[0])
        return None

    async def set(self, key: str, value: CacheableValue, ttl: timedelta | None = None) -> None:
        """Set a namespaced value with an optional expiry."""
        tenant_key = self._validate_and_scope_key(key)
        await self.ensure_initialized()
        assert self._db is not None  # Guaranteed by ensure_initialized

        serialized_value = self._serialize_value(value)
        current_time = datetime.now(timezone.utc).timestamp()
        expires_at = None

        if ttl:
            # Apply TTL jitter to prevent thundering herd
            jittered_ttl = self._apply_ttl_jitter(ttl)
            expires_at = current_time + jittered_ttl.total_seconds()

        await self._db.execute(
            "INSERT OR REPLACE INTO cache (key, value, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (tenant_key, serialized_value, expires_at, current_time),
        )
        await self._db.commit()

    async def delete(self, key: str) -> None:
        """Delete a value from this backend namespace."""
        tenant_key = self._validate_and_scope_key(key)
        await self.ensure_initialized()
        assert self._db is not None  # Guaranteed by ensure_initialized

        await self._db.execute("DELETE FROM cache WHERE key = ?", (tenant_key,))
        await self._db.commit()

    async def clear(self) -> None:
        """Clear all cached values in this backend namespace."""
        await self.ensure_initialized()
        assert self._db is not None  # Guaranteed by ensure_initialized

        await self._db.execute("DELETE FROM cache WHERE key LIKE ?", (self._tenant_prefix + "%",))
        await self._db.commit()

    async def cleanup_expired(self) -> int:
        """Remove expired entries and return count removed.

        Design Rationale:
            - SQLite doesn't auto-expire keys (Redis does after SET with EX)
            - Manual deletion reclaims disk space
            - Count enables monitoring and observability
        """
        await self.ensure_initialized()
        assert self._db is not None  # Guaranteed by ensure_initialized

        current_time = datetime.now(timezone.utc).timestamp()

        # Count cache entries to remove
        async with self._db.execute(
            "SELECT COUNT(*) FROM cache WHERE expires_at IS NOT NULL AND expires_at <= ?", (current_time,)
        ) as cursor:
            cache_row = await cursor.fetchone()
            cache_count = cache_row[0] if cache_row else 0

        # Delete expired cache entries
        await self._db.execute("DELETE FROM cache WHERE expires_at IS NOT NULL AND expires_at <= ?", (current_time,))
        await self._db.commit()

        return cache_count

    async def close(self) -> None:
        """Close the SQLite connection.

        CRITICAL: Stop cleanup BEFORE closing DB to prevent task accessing closed resource.
        """
        await self.stop_cleanup_loop()
        if self._db:
            await self._db.close()
            self._db = None
            self._initialized = False

    async def connect(self) -> None:
        """Connect to SQLite database (for compatibility)."""
        await self.ensure_initialized()

    async def disconnect(self) -> None:
        """Disconnect from SQLite database (for compatibility)."""
        await self.close()


class MockCacheBackend(CacheBackend[CacheableValue]):
    """In-memory test backend approximating JSON serialization and TTL expiry.

    It enforces ``max_entries`` by removing entries with the earliest expiry;
    this is not access-order LRU. A missing TTL is represented internally by an
    expiry 100 years in the future rather than true persistence.
    """

    def __init__(self, tenant_id: str, config: CacheConfig | None = None):
        """Initialize in-memory mock cache backend.

        Args:
            tenant_id: Caller-supplied logical namespace.
            config: Cache configuration; TTL jitter and ``max_entries`` apply.
        """
        super().__init__(tenant_id, config)
        self._data: dict[str, tuple[str, datetime]] = {}  # key -> (value, expires_at)
        self._initialized = True
        # Mock uses a "tenant:" sentinel before the id so test fixtures
        # can spot mock-scoped keys at a glance. Override the parent's
        # bare prefix so _validate_tenant_scoped_key (called via the
        # _validate_and_scope_key helper, if it ever gets adopted by
        # the Mock backend) checks the right form.
        self._tenant_prefix = f"tenant:{tenant_id}:"

    async def ensure_initialized(self) -> None:
        """Mock cache is always initialized."""

    def _get_tenant_key(self, key: str) -> str:
        """Create a namespaced in-memory key."""
        return self._tenant_prefix + key

    async def get(self, key: str) -> CacheableValue | None:
        """Get value from mock cache with expiration check."""
        self._validate_cache_key(key)
        tenant_key = self._get_tenant_key(key)
        entry = self._data.get(tenant_key)

        if entry is None:
            return None

        value, expires_at = entry
        if datetime.now(timezone.utc) > expires_at:
            # Entry expired, remove it
            del self._data[tenant_key]
            return None

        # Deserialize through the base helper so Mock preserves types
        # identically to the Redis and SQLite backends.
        return self._deserialize_value(value)

    async def set(self, key: str, value: CacheableValue, ttl: timedelta | None = None) -> None:
        """Set value in mock cache with TTL."""
        self._validate_cache_key(key)
        tenant_key = self._get_tenant_key(key)

        # Serialize through the base helper so Mock matches the Redis and
        # SQLite backends byte-for-byte (a bare string is JSON-quoted, not
        # stored raw — otherwise get() would json.loads "42" back to int 42).
        serialized_value = self._serialize_value(value)

        # Calculate expiration
        if ttl:
            expires_at = datetime.now(timezone.utc) + self._apply_ttl_jitter(ttl)
        else:
            # No TTL means expire far in the future (100 years)
            expires_at = datetime.now(timezone.utc) + timedelta(days=36500)

        self._data[tenant_key] = (serialized_value, expires_at)

        # Basic memory management - remove oldest entries if too many
        if len(self._data) > self.config.max_entries:
            # Remove oldest 10% of entries
            num_to_remove = len(self._data) // 10
            oldest_keys = sorted(self._data.keys(), key=lambda k: self._data[k][1])[:num_to_remove]
            for old_key in oldest_keys:
                del self._data[old_key]

    async def delete(self, key: str) -> None:
        """Delete key from mock cache."""
        self._validate_cache_key(key)
        tenant_key = self._get_tenant_key(key)
        if tenant_key in self._data:
            del self._data[tenant_key]

    async def cleanup_expired(self) -> int:
        """Remove expired entries from mock cache."""
        now = datetime.now(timezone.utc)
        expired_keys = [k for k, (_, expires_at) in self._data.items() if now > expires_at]

        for key in expired_keys:
            del self._data[key]

        return len(expired_keys)

    async def clear(self) -> None:
        """Clear all entries in this backend namespace."""
        keys_to_remove = [k for k in self._data if k.startswith(self._tenant_prefix)]
        for key in keys_to_remove:
            del self._data[key]

    async def close(self) -> None:
        """Close mock cache.

        Stop cleanup task before clearing data for clean shutdown.
        """
        await self.stop_cleanup_loop()
        self._data.clear()

    async def connect(self) -> None:
        """Connect to mock cache (no-op for compatibility)."""

    async def disconnect(self) -> None:
        """Disconnect from mock cache (no-op for compatibility)."""


async def create_cache_backend(
    tenant_id: str, redis_url: str | None, sqlite_path: str | None = None, config: CacheConfig | None = None
) -> CacheBackend[CacheableValue]:
    """Create and start a namespaced Mock, Redis, or SQLite backend.

    ``mock://`` selects the in-memory backend. Otherwise the factory tries
    Redis when a URL is present and, on supported connection failures, emits a
    ``RuntimeWarning`` and falls back to SQLite. Without ``sqlite_path``, the
    fallback creates ``~/.dualeai/cache/<namespace>/cache.db``. The selected
    backend's cleanup loop is started before return.

    Args:
        tenant_id: Cache-key namespace. ``DualeAISDK`` passes a token fingerprint.
        redis_url: Redis connection URL, None to skip Redis, "mock://" for mock backend
        sqlite_path: SQLite database file path; omission uses a namespace-specific file.
        config: Cache configuration for behavior and limits

    Returns:
        Initialized backend with namespaced keys and a running cleanup loop.
    """
    cache_config = config or CacheConfig()

    # Use mock backend for testing
    if redis_url and redis_url.startswith("mock://"):
        logger.info("Mock cache backend selected for testing", tenant_id=tenant_id)
        mock_backend = MockCacheBackend(tenant_id, cache_config)
        await mock_backend.ensure_initialized()
        await mock_backend.start_cleanup_loop()
        return mock_backend

    # Try Redis first if URL is provided
    if redis_url:
        try:
            redis_backend = RedisCacheBackend(redis_url, tenant_id, cache_config)
            await redis_backend.ensure_initialized()
            await redis_backend.start_cleanup_loop()
            logger.info("Redis cache backend initialized", tenant_id=tenant_id)
            return redis_backend
        except (redis.RedisError, ConnectionError, TimeoutError, OSError) as e:
            warnings.warn(
                f"Redis cache unavailable ({e}), falling back to SQLite. "
                "This limits scalability in distributed environments.",
                RuntimeWarning,
                stacklevel=2,
            )

    # Fall back to SQLite with a namespace-specific database.
    if not sqlite_path:
        # Create a namespace-specific SQLite database path.
        cache_dir = Path.home() / ".dualeai" / "cache" / tenant_id
        cache_dir.mkdir(parents=True, exist_ok=True)
        sqlite_path = str(cache_dir / "cache.db")

    warnings.warn(
        "Using SQLite cache backend - limits scalability in distributed environments. "
        "Consider using Redis for production deployments.",
        RuntimeWarning,
        stacklevel=2,
    )

    sqlite_backend = SQLiteCacheBackend(sqlite_path, tenant_id, cache_config)
    await sqlite_backend.ensure_initialized()
    await sqlite_backend.start_cleanup_loop()
    logger.info("SQLite cache backend initialized", tenant_id=tenant_id, sqlite_path=sqlite_path)
    return sqlite_backend
