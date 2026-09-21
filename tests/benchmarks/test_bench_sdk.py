"""Performance benchmarks for Duale AI Python SDK.

Covers the hot paths: Pydantic model validation (config, SSE events),
cache key generation/validation, JSON serialization, SSE checksum,
and utility functions (string truncation, backoff math).

Run with: make test-bench

CodSpeed tracks CPU (simulation mode) and memory (allocation count, peak RSS).
Sync benchmarks use the @benchmark fixture callback pattern.
"""

import json
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from pytest_codspeed import BenchmarkFixture

from dualeai.cache import CacheConfig, MockCacheBackend
from dualeai.config import DualeAIConfig
from dualeai.events.sse_parser import _compute_checksum, parse_sse_stream
from dualeai.models.bridge import (
    BridgeContentDeltaResponse,
    BridgeSSEEvent,
    BridgeTaskCompletedResponse,
)
from dualeai.models.llm_result import LLMResult
from dualeai.sdk import JsonValue
from dualeai.utils import truncate_error_preview

pytestmark = pytest.mark.benchmark


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mock_cache() -> MockCacheBackend:
    return MockCacheBackend(tenant_id="bench-tenant-001")


@pytest.fixture(scope="module")
def content_delta_json() -> str:
    """Pre-built JSON for BridgeContentDeltaResponse."""
    return json.dumps(
        {
            "type": "content.delta",
            "timestamp": "2025-01-01T00:00:00Z",
            "delta": "Hello, world!",
        }
    )


@pytest.fixture(scope="module")
def task_completed_json() -> str:
    """Pre-built JSON for BridgeTaskCompletedResponse."""
    return json.dumps(
        {
            "type": "task.completed",
            "timestamp": "2025-01-01T00:00:00Z",
            "result": {
                "completion": "The answer is 42.",
                "cache_hit": False,
            },
        }
    )


@pytest.fixture(scope="module")
def sse_event_content_delta() -> dict[str, object]:
    """Raw dict for BridgeSSEEvent with content.delta payload."""
    return {
        "id": "42:1",
        "data": {
            "type": "content.delta",
            "timestamp": "2025-01-01T00:00:00Z",
            "delta": "streaming chunk",
        },
        "timestamp": "2025-01-01T00:00:00Z",
    }


@pytest.fixture(scope="module")
def sse_event_task_completed() -> dict[str, object]:
    """Raw dict for BridgeSSEEvent with task.completed payload."""
    return {
        "id": "99:1",
        "data": {
            "type": "task.completed",
            "timestamp": "2025-01-01T00:00:00Z",
            "result": {"completion": "Done.", "cache_hit": False},
        },
        "timestamp": "2025-01-01T00:00:00Z",
    }


@pytest.fixture(scope="module")
def sse_content_delta_wire(content_delta_json: str) -> bytes:
    """One complete content-delta event in the Bridge wire format."""
    checksum = _compute_checksum("content.delta", content_delta_json)
    return (f": crc={checksum}\nid: 42:1\nevent: content.delta\ndata: {content_delta_json}\n\n").encode()


# ===========================================================================
# Config validation
# ===========================================================================


class TestConfigValidation:
    """DualeAIConfig instantiation with Pydantic validators."""

    def test_config_create_minimal(self, benchmark: BenchmarkFixture) -> None:
        @benchmark
        def _() -> None:
            DualeAIConfig(token="dualeai_test_token_12345")

    def test_config_create_full(self, benchmark: BenchmarkFixture) -> None:
        @benchmark
        def _() -> None:
            DualeAIConfig(
                token="dualeai_test_token_12345",
                endpoint="https://api.duale.ai",
            )


# ===========================================================================
# Pydantic model validation — SSE event deserialization
# ===========================================================================


class TestModelValidation:
    """Pydantic model_validate on SSE event payloads (hot path for streaming)."""

    def test_content_delta_from_json(self, benchmark: BenchmarkFixture, content_delta_json: str) -> None:
        @benchmark
        def _() -> None:
            BridgeContentDeltaResponse.model_validate_json(content_delta_json)

    def test_task_completed_from_json(self, benchmark: BenchmarkFixture, task_completed_json: str) -> None:
        @benchmark
        def _() -> None:
            BridgeTaskCompletedResponse.model_validate_json(task_completed_json)

    def test_sse_event_content_delta(
        self,
        benchmark: BenchmarkFixture,
        sse_event_content_delta: dict[str, object],
    ) -> None:
        @benchmark
        def _() -> None:
            BridgeSSEEvent.model_validate(sse_event_content_delta)

    def test_sse_event_task_completed(
        self,
        benchmark: BenchmarkFixture,
        sse_event_task_completed: dict[str, object],
    ) -> None:
        @benchmark
        def _() -> None:
            BridgeSSEEvent.model_validate(sse_event_task_completed)

    def test_llm_result_simple(self, benchmark: BenchmarkFixture) -> None:
        data = {"completion": "The answer is 42.", "cache_hit": False}

        @benchmark
        def _() -> None:
            LLMResult.model_validate(data)

    def test_llm_result_with_tool_calls(self, benchmark: BenchmarkFixture) -> None:
        data = {
            "completion": None,
            "cache_hit": False,
            "tool_calls": [
                {
                    "id": "call_001",
                    "name": "search",
                    "arguments": {"query": "benchmark test"},
                },
                {
                    "id": "call_002",
                    "name": "calculate",
                    "arguments": {"expression": "2+2"},
                },
            ],
        }

        @benchmark
        def _() -> None:
            LLMResult.model_validate(data)


# ===========================================================================
# SSE checksum computation
# ===========================================================================


class TestSSEChecksum:
    """CRC32 checksum computation (per-event overhead)."""

    def test_checksum_short_payload(self, benchmark: BenchmarkFixture) -> None:
        @benchmark
        def _() -> None:
            _compute_checksum("content.delta", '{"delta":"hello"}')

    def test_checksum_large_payload(self, benchmark: BenchmarkFixture) -> None:
        large_data = json.dumps({"delta": "x" * 10_000})

        @benchmark
        def _() -> None:
            _compute_checksum("content.delta", large_data)

    async def test_parse_content_delta(self, sse_content_delta_wire: bytes) -> None:
        """Parse one complete Bridge event through checksum and model validation."""

        async def chunks() -> AsyncIterator[bytes]:
            yield sse_content_delta_wire

        events = [event async for event in parse_sse_stream(chunks())]
        assert len(events) == 1
        assert isinstance(events[0].data, BridgeContentDeltaResponse)


# ===========================================================================
# Cache operations — key generation, validation, serialization
# ===========================================================================


class TestCacheOperations:
    """Cache hot-path operations (called on every get/set)."""

    def test_tenant_key_generation(self, benchmark: BenchmarkFixture, mock_cache: MockCacheBackend) -> None:
        @benchmark
        def _() -> None:
            mock_cache._get_tenant_key("user:session:abc123")

    def test_key_validation(self, benchmark: BenchmarkFixture, mock_cache: MockCacheBackend) -> None:
        @benchmark
        def _() -> None:
            mock_cache._validate_cache_key("user:session:abc123")

    def test_ttl_jitter(self, benchmark: BenchmarkFixture, mock_cache: MockCacheBackend) -> None:
        ttl = timedelta(hours=1)

        @benchmark
        def _() -> None:
            mock_cache._apply_ttl_jitter(ttl)

    def test_json_serialize_small(self, benchmark: BenchmarkFixture, mock_cache: MockCacheBackend) -> None:
        value: dict[str, JsonValue] = {"user": "alice", "score": 42}

        @benchmark
        def _() -> None:
            mock_cache._serialize_value(value)

    def test_json_serialize_large(self, benchmark: BenchmarkFixture) -> None:
        raw = json.dumps([{"id": i, "data": f"value_{i}"} for i in range(100)])

        @benchmark
        def _() -> None:
            json.loads(raw)

    def test_json_roundtrip(self, benchmark: BenchmarkFixture, mock_cache: MockCacheBackend) -> None:
        value: dict[str, JsonValue] = {"user": "alice", "score": 99}

        @benchmark
        def _() -> None:
            serialized = mock_cache._serialize_value(value)
            mock_cache._deserialize_value(serialized)

    def test_cache_config_creation(self, benchmark: BenchmarkFixture) -> None:
        @benchmark
        def _() -> None:
            CacheConfig(
                max_entries=50_000,
                ttl_jitter_factor=0.15,
                cleanup_interval=timedelta(minutes=5),
            )


# ===========================================================================
# Utility functions — string truncation, backoff math
# ===========================================================================


class TestUtilities:
    """Utility hot paths (called in logging/observability)."""

    def test_truncate_error(self, benchmark: BenchmarkFixture) -> None:
        error = "Traceback (most recent call last):\n" + "  File..." * 100

        @benchmark
        def _() -> None:
            truncate_error_preview(error)

    # Backoff-tracker benchmarks were removed alongside ConsecutiveTimeoutTracker.
