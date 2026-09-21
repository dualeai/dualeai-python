"""Unit tests for SSE parser with CRC32 checksum validation.

Tests the parse_sse_stream function including:
- Valid checksum validation
- Checksum mismatch detection (SSEChecksumError)
"""

import pytest

from dualeai.events import sse_parser
from dualeai.events.sse_parser import (
    SSEChecksumError,
    SSEParseError,
    parse_sse_stream,
)
from dualeai.models.bridge import (
    BridgeContentDeltaResponse,
    BridgeContentResetResponse,
    BridgeTaskErrorResponse,
)


class MockStreamReader:
    """Mock aiohttp.StreamReader for testing SSE parser."""

    def __init__(self, data: str | bytes) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8")
        self._lines = data.split(b"\n")
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        if self._index >= len(self._lines):
            raise StopAsyncIteration
        line = self._lines[self._index]
        self._index += 1
        return line + b"\n"


# Valid test payloads matching Pydantic models
_CONTENT_DELTA = '{"type":"content.delta","timestamp":"2025-01-01T00:00:00Z","delta":"hello"}'
_CONTENT_DELTA_2 = '{"type":"content.delta","timestamp":"2025-01-01T00:00:01Z","delta":"world"}'
_CONTENT_RESET = '{"type":"content.reset","timestamp":"2025-01-01T00:00:01Z","generation":2}'
_TASK_COMPLETED = '{"type":"task.completed","timestamp":"2025-01-01T00:00:00Z","result":{"completion":"done"}}'
_CONTENT_DELTA_CRC = "18fd7226"
_CONTENT_DELTA_2_CRC = "9e124023"
_CONTENT_RESET_CRC = "b2805961"
_TASK_COMPLETED_MESSAGE_CRC = "c109bc15"

# The frozen `content.delta` event is written out in full rather than assembled
# from the parts above. It pins the exact public wire bytes accepted by the SDK.
_FROZEN_EVENT = (
    ": crc=18fd7226\n"
    "id: 1:1\n"
    "event: content.delta\n"
    'data: {"type":"content.delta","timestamp":"2025-01-01T00:00:00Z","delta":"hello"}\n'
    "\n"
)


class TestSSEParserChecksum:
    """Tests for CRC32 checksum validation in SSE parser."""

    @pytest.mark.unit
    async def test_parser_accepts_the_frozen_content_delta_wire_event(self):
        """The consuming side accepts exactly the bytes the bridge emits.

        This test pins one complete literal event so a parser change cannot
        silently change the public wire bytes it accepts or the payload it
        reconstructs.

        The checksum `18fd7226` is `zlib.crc32` over
        `content.delta:{"type":"content.delta","timestamp":"2025-01-01T00:00:00Z","delta":"hello"}`,
        derived on the bridge side by
        `test_frozen_checksum_is_crc32_of_the_type_and_data_framing`.
        """
        events = [event async for event in parse_sse_stream(MockStreamReader(_FROZEN_EVENT))]

        assert len(events) == 1
        assert events[0].id == "1:1"
        assert isinstance(events[0].data, BridgeContentDeltaResponse)
        assert events[0].data.delta == "hello"

    @pytest.mark.unit
    async def test_parse_sse_validates_checksum(self):
        """A wire checksum produced outside the parser accepts the matching event."""
        stream = f": crc={_CONTENT_DELTA_CRC}\nid: 1:1\nevent: content.delta\ndata: {_CONTENT_DELTA}\n\n"
        events = [e async for e in parse_sse_stream(MockStreamReader(stream))]

        assert len(events) == 1
        assert events[0].id == "1:1"
        assert isinstance(events[0].data, BridgeContentDeltaResponse)
        assert events[0].data.delta == "hello"

    @pytest.mark.unit
    async def test_parse_sse_preserves_content_replacement(self):
        stream = f": crc={_CONTENT_RESET_CRC}\nid: 2:1\nevent: content.reset\ndata: {_CONTENT_RESET}\n\n"

        events = [event async for event in parse_sse_stream(MockStreamReader(stream))]

        assert len(events) == 1
        assert isinstance(events[0].data, BridgeContentResetResponse)
        assert events[0].data.generation == 2

    @pytest.mark.unit
    async def test_parse_sse_raises_on_checksum_mismatch(self):
        """Test that checksum mismatch raises SSEChecksumError."""
        stream = f": crc=00000000\nid: 42:3\nevent: content.delta\ndata: {_CONTENT_DELTA}\n\n"

        with pytest.raises(SSEChecksumError) as exc_info:
            _events = [e async for e in parse_sse_stream(MockStreamReader(stream))]

        assert exc_info.value.event_id == "42:3"
        assert exc_info.value.expected == "00000000"
        assert exc_info.value.event_type == "content.delta"
        assert "mismatch" in str(exc_info.value).lower()

    @pytest.mark.unit
    async def test_parse_sse_rejects_default_event_type_for_typed_payload(self):
        """Bridge events must name the same type in the SSE and JSON layers."""
        stream = f": crc={_TASK_COMPLETED_MESSAGE_CRC}\nid: 1:1\ndata: {_TASK_COMPLETED}\n\n"

        with pytest.raises(SSEParseError, match="does not match payload type"):
            _ = [event async for event in parse_sse_stream(MockStreamReader(stream))]

    @pytest.mark.unit
    async def test_parse_sse_heartbeat_comments_ignored(self):
        """Test that non-checksum comments (heartbeats) are ignored."""
        # heartbeat comment before checksum
        stream = f": heartbeat\n: crc={_CONTENT_DELTA_CRC}\nid: 1:1\nevent: content.delta\ndata: {_CONTENT_DELTA}\n\n"
        events = [e async for e in parse_sse_stream(MockStreamReader(stream))]

        assert len(events) == 1

    @pytest.mark.unit
    async def test_parse_sse_multiple_events_each_validated(self):
        """Test that each event in stream has its checksum validated."""
        stream = (
            f": crc={_CONTENT_DELTA_CRC}\nid: 1:1\nevent: content.delta\ndata: {_CONTENT_DELTA}\n\n"
            f": crc={_CONTENT_DELTA_2_CRC}\nid: 2:1\nevent: content.delta\ndata: {_CONTENT_DELTA_2}\n\n"
        )
        events = [e async for e in parse_sse_stream(MockStreamReader(stream))]

        assert len(events) == 2
        assert isinstance(events[0].data, BridgeContentDeltaResponse)
        assert events[0].data.delta == "hello"
        assert isinstance(events[1].data, BridgeContentDeltaResponse)
        assert events[1].data.delta == "world"


class TestSSEParserEmptyData:
    """Tests for handling empty/retry-only SSE events."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "ignored_prelude",
        [
            pytest.param("retry: 3000\n\n", id="retry-only"),
            pytest.param("data: \n\n", id="empty-data"),
            pytest.param("retry: 3000\n\nretry: 5000\n\n", id="multiple-retries"),
        ],
    )
    async def test_parse_sse_skips_events_without_payload(self, ignored_prelude: str):
        """Events without a payload do not affect the following complete event."""
        stream = (
            f"{ignored_prelude}: crc={_CONTENT_DELTA_CRC}\nid: 1:1\nevent: content.delta\ndata: {_CONTENT_DELTA}\n\n"
        )
        events = [e async for e in parse_sse_stream(MockStreamReader(stream))]

        assert len(events) == 1
        assert events[0].id == "1:1"
        assert isinstance(events[0].data, BridgeContentDeltaResponse)
        assert events[0].data.delta == "hello"


class TestSSEParserEdges:
    """Malformed and forward-compat edge cases."""

    @pytest.mark.unit
    async def test_parse_sse_rejects_added_fields_on_a_known_event(self):
        payload = (
            '{"type":"tool.use","timestamp":"2026-07-07T00:00:00Z","tool_call_id":"c1",'
            '"name":"gate","input":{},"deadline_at":"2026-07-07T00:00:10Z",'
            '"future_server_field":"ignored"}'
        )
        stream = f": crc=38725c16\nid: 1:1\nevent: tool.use\ndata: {payload}\n\n"

        with pytest.raises(SSEParseError, match="Invalid SSE event"):
            _ = [event async for event in parse_sse_stream(MockStreamReader(stream))]

    @pytest.mark.unit
    async def test_parse_sse_rejects_added_fields_in_a_known_nested_result(self):
        payload = (
            '{"type":"task.completed","timestamp":"2026-07-07T00:00:00Z",'
            '"result":{"completion":"done","future_nested_field":"ignored"}}'
        )
        stream = f": crc=7bece536\nid: 2:1\nevent: task.completed\ndata: {payload}\n\n"

        with pytest.raises(SSEParseError, match="Invalid SSE event"):
            _ = [event async for event in parse_sse_stream(MockStreamReader(stream))]

    @pytest.mark.unit
    async def test_parse_sse_preserves_problem_details_extensions(self):
        payload = (
            '{"type":"task.error","timestamp":"2026-07-30T00:00:00Z",'
            '"data":{"title":"No acceptable model response","status":400,'
            '"detail":"No selected model produced an acceptable response.",'
            '"error_code":"CONTENT_FILTERED","retryable":false,'
            '"support_reference":"case-123"}}'
        )
        stream = f": crc=f3ffddf5\nid: 3:1\nevent: task.error\ndata: {payload}\n\n"

        events = [event async for event in parse_sse_stream(MockStreamReader(stream))]

        assert len(events) == 1
        assert isinstance(events[0].data, BridgeTaskErrorResponse)
        assert events[0].data.data.model_extra == {"support_reference": "case-123"}

    @pytest.mark.unit
    async def test_parse_sse_raises_on_malformed_json(self):
        """Malformed JSON in an event's data raises SSEParseError (not a silent skip)."""
        stream = ": crc=b65c98ae\nid: 1:1\nevent: content.delta\ndata: {not valid json\n\n"
        with pytest.raises(SSEParseError):
            _ = [e async for e in parse_sse_stream(MockStreamReader(stream))]

    @pytest.mark.unit
    async def test_parse_sse_rejects_unknown_event_type(self):
        """An unknown discriminator is a wire contract violation."""
        unknown = '{"type":"quantum.event","timestamp":"2025-01-01T00:00:00Z","foo":"bar"}'
        stream = (
            f": crc=9d8918d1\nid: 1:1\nevent: quantum.event\ndata: {unknown}\n\n"
            f": crc={_CONTENT_DELTA_CRC}\nid: 2:1\nevent: content.delta\ndata: {_CONTENT_DELTA}\n\n"
        )

        with pytest.raises(SSEParseError, match="Invalid SSE event"):
            _ = [event async for event in parse_sse_stream(MockStreamReader(stream))]

    @pytest.mark.unit
    async def test_parse_sse_requires_checksum_for_every_payload(self):
        stream = f"id: 1:1\nevent: content.delta\ndata: {_CONTENT_DELTA}\n\n"

        with pytest.raises(SSEParseError, match="Missing CRC32 checksum"):
            _ = [event async for event in parse_sse_stream(MockStreamReader(stream))]

    @pytest.mark.unit
    async def test_parse_sse_accepts_event_at_data_byte_limit(self, monkeypatch: pytest.MonkeyPatch):
        """The event-data cap is inclusive and counts UTF-8 wire bytes."""
        boundary_data = '{"type":"content.delta",\n"timestamp":"2025-01-01T00:00:00Z","delta":"hello"}'
        monkeypatch.setattr(sse_parser, "_MAX_SSE_EVENT_BYTES", len(boundary_data.encode()))
        stream = (
            ": crc=16fac066\nid: 1:1\nevent: content.delta\n"
            'data: {"type":"content.delta",\n'
            'data: "timestamp":"2025-01-01T00:00:00Z","delta":"hello"}\n\n'
        )

        events = [event async for event in parse_sse_stream(MockStreamReader(stream))]

        assert len(events) == 1

    @pytest.mark.unit
    async def test_parse_sse_rejects_oversized_multiline_event(self, monkeypatch: pytest.MonkeyPatch):
        """Complete short lines cannot grow one event beyond the data cap."""
        monkeypatch.setattr(sse_parser, "_MAX_SSE_EVENT_BYTES", 16)
        stream = ": crc=00000000\ndata: 12345678\ndata: 12345678\n"

        with pytest.raises(SSEParseError, match="SSE event data exceeds 16 bytes"):
            _ = [event async for event in parse_sse_stream(MockStreamReader(stream))]

    @pytest.mark.unit
    async def test_parse_sse_rejects_oversized_unterminated_line(self, monkeypatch: pytest.MonkeyPatch):
        """An attacker cannot grow the pending line buffer without a newline."""
        monkeypatch.setattr(sse_parser, "_MAX_SSE_EVENT_BYTES", 8)

        async def chunks():
            yield b"data"
            yield b": 1234"

        with pytest.raises(SSEParseError, match="SSE line exceeds 8 bytes"):
            _ = [event async for event in parse_sse_stream(chunks())]
