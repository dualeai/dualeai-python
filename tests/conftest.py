"""Shared deterministic fixtures for SDK tests.

Use ``minimal_mock_sdk`` for SDK orchestration and state tests that need a real
``DualeAISDK`` with an injected transport. ``MockHTTPTransport`` implements the
transport protocol; it does not test HTTP URLs, headers, wire serialization, or
status-code translation. Test those contracts with the real ``HTTPTransport``
and a mocked HTTP server. Pure model and utility tests need neither fixture.
"""

# Add src to path for imports first
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Then import everything else
import logging
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Protocol
from unittest.mock import AsyncMock, Mock

import pytest

from dualeai import DualeAIConfig, DualeAISDK
from dualeai.attachments import PreparedAttachment
from dualeai.cache import CacheableValue, CacheBackend, CacheConfig, create_cache_backend
from dualeai.logging_config import configure_logging
from tests.mocks.mock_http import MockHTTPTransport

configure_logging(level=logging.DEBUG)


@pytest.fixture(autouse=True)
def _hermetic_config(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Isolate config from the developer's ambient .env / DUALEAI_* environment.

    DualeAIConfig.model_config["env_file"] is baked to find_dotenv() at import, so
    any repository-local .env (holding a real dev token/agent_id) leaks DUALEAI_* into
    every DualeAIConfig() built in tests — making config defaults depend on the
    caller's cwd (e.g. agent_id becomes non-None, silently breaking the
    "requires agent_id" guard tests). Disable the dotenv source and strip any
    ambient DUALEAI_* so test configs are deterministic and the suite stays
    self-contained.
    """
    original_env_file = DualeAIConfig.model_config.get("env_file")
    DualeAIConfig.model_config["env_file"] = None
    for key in [name for name in os.environ if name.startswith("DUALEAI_")]:
        monkeypatch.delenv(key, raising=False)
    try:
        yield
    finally:
        DualeAIConfig.model_config["env_file"] = original_env_file


# ============================================================================
# Test Markers Registration
# ============================================================================

# Define custom markers - these are registered via pytest_configure below
# pytest.mark.unit = pytest.mark.unit
# pytest.mark.integration = pytest.mark.integration


# ============================================================================
# Standard Test Data Constants
# ============================================================================


class TestTenantIDs:
    """Standardized tenant IDs for consistent testing across all test files.

    This replaces the 127+ hardcoded tenant ID instances with centralized constants,
    improving maintainability and reducing duplication.

    Note: Uses alphanumeric format (^[a-zA-Z][a-zA-Z0-9_-]*$) as required by TenantId schema,
    not UUID format. Pattern must start with a letter.
    """

    DEFAULT = "tenant-default-001"
    INTEGRATION = "tenant-integration"
    ISOLATED = "tenant-isolated-002"
    CROSS_TENANT_A = "tenant-cross-a"
    CROSS_TENANT_B = "tenant-cross-b"


class CacheFactory(Protocol):
    """Create tracked cache backends for the selected fixture implementation."""

    async def __call__(self, tenant_id: str | None = None) -> CacheBackend[CacheableValue]: ...


class UnstartedSDKFactory(Protocol):
    """Create a real SDK without starting background tasks."""

    def __call__(self, *, agent_id: str | None = None) -> DualeAISDK: ...


@pytest.fixture(params=["mock", "sqlite"])
async def cache_factory(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[CacheFactory]:
    """Create one backend type and close every instance after each test."""
    backend_type = request.param
    created: list[CacheBackend[CacheableValue]] = []
    sqlite_path = tmp_path / "shared-cache.db"

    async def create(tenant_id: str | None = None) -> CacheBackend[CacheableValue]:
        resolved_tenant_id = tenant_id or TestTenantIDs.DEFAULT
        if backend_type == "mock":
            cache = await create_cache_backend(tenant_id=resolved_tenant_id, redis_url="mock://test")
        elif backend_type == "sqlite":
            cache = await create_cache_backend(
                tenant_id=resolved_tenant_id,
                redis_url=None,
                sqlite_path=str(sqlite_path),
                config=CacheConfig(),
            )
        else:
            raise ValueError(f"Unknown cache backend type: {backend_type}")
        created.append(cache)
        return cache

    try:
        yield create
    finally:
        for cache in reversed(created):
            await cache.close()


@pytest.fixture
async def cache_backend(cache_factory: CacheFactory) -> CacheBackend[CacheableValue]:
    """Create one tracked cache for tests that do not need a factory."""
    return await cache_factory()


@pytest.fixture
def unstarted_sdk_factory(config_factory: Callable[..., DualeAIConfig]) -> UnstartedSDKFactory:
    """Create real SDK instances for synchronous registration tests."""

    def create(*, agent_id: str | None = None) -> DualeAISDK:
        return DualeAISDK(config=config_factory(agent_id=agent_id), auto_start=False)

    return create


# ============================================================================
# Test Utilities
# ============================================================================


# NOTE: create_test_streaming_update was removed in favor of Task-stream models.
# Use BridgeContentDeltaResponse for streaming tests instead


def create_mock_scheduler(
    active_count: int = 0,
    pending_count: int = 0,
    limit: int = 100,
    pending_limit: int = 50,
    closed: bool = False,
) -> Mock:
    """Create a mock scheduler for backpressure testing.

    NOTE: This is NOT an SDK mock. It mocks aiojobs.Scheduler for
    BackpressureController tests which take scheduler as constructor arg.

    Args:
        active_count: Number of active jobs
        pending_count: Number of pending jobs
        limit: Job limit
        pending_limit: Pending job limit
        closed: Whether scheduler is closed

    Returns:
        Mock scheduler with integer attributes
    """
    mock_scheduler = Mock()
    mock_scheduler.active_count = active_count
    mock_scheduler.pending_count = pending_count
    mock_scheduler.limit = limit
    mock_scheduler.pending_limit = pending_limit
    mock_scheduler.closed = closed
    mock_scheduler.spawn = AsyncMock()
    return mock_scheduler


# ============================================================================
# SDK and Component Fixtures
# ============================================================================


@pytest.fixture
def config_factory():
    """Provide configuration factory for creating test configs.

    Usage:
        config = config_factory(tenant_id=TestTenantIDs.INTEGRATION)
        config = config_factory(endpoint="http://custom:8080")
    """

    def _create_config(**overrides: object) -> DualeAIConfig:
        # Provide default token if not specified
        if "token" not in overrides:
            overrides["token"] = "dualeai_test_token_12345"
        return DualeAIConfig.model_validate(overrides)

    return _create_config


# ============================================================================
# Async Test Utilities
# ============================================================================


@pytest.fixture
async def temp_db_path(tmp_path: Path) -> Path:
    """Provide temporary database path for testing."""
    return tmp_path / "test.db"


# ============================================================================
# HTTP network-boundary fixtures
# ============================================================================


@pytest.fixture
async def mock_http_transport():
    """MockHTTPTransport with fast cleanup.

    Provides a mock HTTP transport that can inject events and inspect requests.
    Uses stop_event pattern for guaranteed fast cleanup.
    """
    transport = MockHTTPTransport()
    await transport.connect()
    yield transport
    transport.signal_stop()
    await transport.disconnect()


@pytest.fixture
def mock_transport(minimal_mock_sdk: DualeAISDK) -> MockHTTPTransport:
    """Type-narrowed accessor for the SDK's injected transport.

    Saves the boilerplate dance of ``transport = sdk._transport;
    assert isinstance(transport, MockHTTPTransport)`` at every test
    site that needs to inject events or inspect recorded requests.
    """
    transport = minimal_mock_sdk._transport
    assert isinstance(transport, MockHTTPTransport)
    return transport


@pytest.fixture
async def minimal_mock_sdk(config_factory: Callable[..., DualeAIConfig], mock_http_transport: MockHTTPTransport):
    """Real SDK with an in-memory transport-protocol implementation.

    Use this fixture to exercise SDK validation, orchestration, and lifecycle
    behavior without network I/O. The transport records requests and supplies
    injected events. It does not exercise ``HTTPTransport`` request building or
    response parsing; HTTP adapter tests must instantiate that class directly.
    """
    # Create real config
    config = config_factory(tenant_id=TestTenantIDs.DEFAULT, agent_id="agent-test-default")

    # Create REAL SDK with mock transport injected
    sdk = DualeAISDK(config=config, transport=mock_http_transport, auto_start=False)

    try:
        # Initialize events client (uses mocked HTTP transport)
        await sdk._ensure_events_client()

        yield sdk

    finally:
        # Signal stop to all streams BEFORE cleanup
        # This prevents hangs from background tasks waiting for SSE events
        mock_http_transport.signal_stop()

        try:
            await sdk.cleanup()
        except Exception:
            pass


# ============================================================================
# Attachment Test Fixtures
# ============================================================================


@pytest.fixture
def make_file(tmp_path: Path):
    """Factory fixture: create a temp file with given size and content pattern."""

    def _make(name: str = "test.bin", size: int = 1024, pattern: bytes = b"\xab") -> Path:
        path = tmp_path / name
        data = (pattern * ((size // len(pattern)) + 1))[:size]
        path.write_bytes(data)
        return path

    return _make


@pytest.fixture
def make_attachment(make_file):
    """Factory fixture: create a PreparedAttachment from a temp file."""

    def _make(
        name: str = "test.bin",
        size: int = 1024,
        description: str = "test file",
        pattern: bytes = b"\xab",
    ) -> PreparedAttachment:
        path = make_file(name=name, size=size, pattern=pattern)
        return PreparedAttachment(
            key="test-key-001",
            path=path,
            filename=name,
            description=description,
            size=size,
        )

    return _make


# ============================================================================
# Test Configuration
# ============================================================================


def pytest_configure(config: pytest.Config) -> None:
    """Configure pytest with custom markers."""
    config.addinivalue_line("markers", "unit: Fast, isolated unit tests")
    config.addinivalue_line("markers", "integration: Tests requiring infrastructure")
