"""Task event parsing after hpke-http has authenticated each complete SSE block."""

import pytest

from dualeai.events import sse_parser
from dualeai.events.sse_parser import (
    SSEParseError,
    parse_sse_stream,
)
from dualeai.models.bridge import (
    BridgeContentDeltaResponse,
    BridgeContentResetResponse,
    BridgeTaskErrorResponse,
)


class CheckedBlockReader:
    """Feed complete SSE blocks as the checked HPKE reader delivers them."""

    def __init__(self, data: str | bytes) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8")
        if not data.endswith(b"\n\n"):
            raise ValueError("SSE test fixture must end at a block boundary")
        self._blocks = [block + b"\n\n" for block in data.split(b"\n\n") if block]
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        if self._index >= len(self._blocks):
            raise StopAsyncIteration
        block = self._blocks[self._index]
        self._index += 1
        return block


# Valid test payloads matching Pydantic models
_CONTENT_DELTA = '{"type":"content.delta","delta":"hello","generation":1,"sequence":0}'
_CONTENT_DELTA_2 = '{"type":"content.delta","delta":"world","generation":1,"sequence":1}'
_CONTENT_RESET = '{"type":"content.reset","generation":2}'
_TASK_COMPLETED = '{"type":"task.completed","timestamp":"2025-01-01T00:00:00Z","result":{"completion":"done"}}'

# The frozen `content.delta` event is written out in full rather than assembled
# from the parts above. It pins the exact public wire bytes accepted by the SDK.
_FROZEN_EVENT = (
    'id: 1:1\nevent: content.delta\ndata: {"type":"content.delta","delta":"hello","generation":3,"sequence":7}\n\n'
)

# Two `data:` lines: 24 bytes, then 50 bytes in 46 characters ("é" is U+00E9, two bytes; "🚀" is four bytes).
_MULTIBYTE_TWO_LINE_DELTA = (
    "id: 1:1\nevent: content.delta\n"
    'data: {"type":"content.delta",\n'
    'data: "delta":"héllo 🚀","generation":1,"sequence":1}\n\n'
)


class TestSSEParserEvents:
    """Typed events need no application checksum after transport authentication."""

    @pytest.mark.unit
    async def test_parser_accepts_the_frozen_content_delta_wire_event(self):
        """Accept the public wire event without application checksum metadata."""
        events = [event async for event in parse_sse_stream(CheckedBlockReader(_FROZEN_EVENT))]

        assert len(events) == 1
        assert events[0].id == "1:1"
        assert isinstance(events[0].data, BridgeContentDeltaResponse)
        assert (events[0].data.delta, events[0].data.generation, events[0].data.sequence) == ("hello", 3, 7)

    @pytest.mark.unit
    async def test_initial_bom_and_persistent_id_follow_sse_rules(self):
        first = f"\ufeffid: 3:1\nevent: content.delta\ndata: {_CONTENT_DELTA}\n\n"
        second = f"event: content.delta\ndata: {_CONTENT_DELTA_2}\n\n"
        events = [event async for event in parse_sse_stream(CheckedBlockReader(first + second))]
        assert [event.id for event in events] == ["3:1", "3:1"]
        assert [event.data.delta for event in events if isinstance(event.data, BridgeContentDeltaResponse)] == [
            "hello",
            "world",
        ]

    @pytest.mark.unit
    async def test_parse_sse_preserves_content_replacement(self):
        stream = f"id: 2:1\nevent: content.reset\ndata: {_CONTENT_RESET}\n\n"

        events = [event async for event in parse_sse_stream(CheckedBlockReader(stream))]

        assert len(events) == 1
        assert isinstance(events[0].data, BridgeContentResetResponse)
        assert events[0].data.generation == 2

    @pytest.mark.unit
    async def test_parse_sse_rejects_default_event_type_for_typed_payload(self):
        """Bridge events must name the same type in the SSE and JSON layers."""
        stream = f"id: 1:1\ndata: {_TASK_COMPLETED}\n\n"

        with pytest.raises(SSEParseError, match="does not match payload type"):
            _ = [event async for event in parse_sse_stream(CheckedBlockReader(stream))]

    @pytest.mark.unit
    @pytest.mark.parametrize("comment", ["heartbeat", "crc=00000000", "crc=invalid"])
    async def test_parse_sse_ignores_comment_contents(self, comment: str):
        """SSE comments have no application integrity semantics."""
        stream = f": {comment}\nid: 1:1\nevent: content.delta\ndata: {_CONTENT_DELTA}\n\n"
        events = [event async for event in parse_sse_stream(CheckedBlockReader(stream))]

        assert len(events) == 1
        assert events[0].id == "1:1"
        assert isinstance(events[0].data, BridgeContentDeltaResponse)
        assert events[0].data.delta == "hello"

    @pytest.mark.unit
    async def test_parse_sse_preserves_multiple_event_payloads(self):
        """Each complete block produces its own typed payload."""
        stream = (
            f"id: 1:1\nevent: content.delta\ndata: {_CONTENT_DELTA}\n\n"
            f"id: 2:1\nevent: content.delta\ndata: {_CONTENT_DELTA_2}\n\n"
        )
        events = [e async for e in parse_sse_stream(CheckedBlockReader(stream))]

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
        stream = f"{ignored_prelude}id: 1:1\nevent: content.delta\ndata: {_CONTENT_DELTA}\n\n"
        events = [e async for e in parse_sse_stream(CheckedBlockReader(stream))]

        assert len(events) == 1
        assert events[0].id == "1:1"
        assert isinstance(events[0].data, BridgeContentDeltaResponse)
        assert events[0].data.delta == "hello"

    @pytest.mark.unit
    async def test_parse_sse_rejects_whitespace_only_task_payload(self):
        stream = "id: 1:1\nevent: content.delta\ndata:  \n\n"

        with pytest.raises(SSEParseError, match="Invalid JSON"):
            _ = [event async for event in parse_sse_stream(CheckedBlockReader(stream))]


class TestSSEParserEdges:
    """Malformed and forward-compat edge cases."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("event", "payload"),
        [
            pytest.param(
                "tool.use",
                '{"type":"tool.use","timestamp":"2026-07-07T00:00:00Z","tool_call_id":"c1",'
                '"name":"gate","input":{},"deadline_at":"2026-07-07T00:00:10Z",'
                '"future_server_field":"ignored"}',
                id="tool_use",
            ),
            pytest.param(
                "content.delta",
                '{"type":"content.delta","delta":"hello","generation":1,"sequence":1,"delta_tokens":5}',
                id="content_delta",
            ),
        ],
    )
    async def test_parse_sse_rejects_added_fields_on_a_known_event(self, event: str, payload: str):
        stream = f"id: 1:1\nevent: {event}\ndata: {payload}\n\n"

        with pytest.raises(SSEParseError, match="Invalid SSE event"):
            _ = [parsed async for parsed in parse_sse_stream(CheckedBlockReader(stream))]

    @pytest.mark.unit
    async def test_parse_sse_rejects_added_fields_in_a_known_nested_result(self):
        payload = (
            '{"type":"task.completed","timestamp":"2026-07-07T00:00:00Z",'
            '"result":{"completion":"done","future_nested_field":"ignored"}}'
        )
        stream = f"id: 2:1\nevent: task.completed\ndata: {payload}\n\n"

        with pytest.raises(SSEParseError, match="Invalid SSE event"):
            _ = [event async for event in parse_sse_stream(CheckedBlockReader(stream))]

    @pytest.mark.unit
    async def test_parse_sse_preserves_problem_details_extensions(self):
        payload = (
            '{"type":"task.error","timestamp":"2026-07-30T00:00:00Z",'
            '"data":{"title":"No acceptable model response","status":400,'
            '"detail":"No selected model produced an acceptable response.",'
            '"error_code":"CONTENT_FILTERED","retryable":false,'
            '"support_reference":"case-123"}}'
        )
        stream = f"id: 3:1\nevent: task.error\ndata: {payload}\n\n"

        events = [event async for event in parse_sse_stream(CheckedBlockReader(stream))]

        assert len(events) == 1
        assert isinstance(events[0].data, BridgeTaskErrorResponse)
        assert events[0].data.data.model_extra == {"support_reference": "case-123"}

    @pytest.mark.unit
    async def test_parse_sse_raises_on_malformed_json(self):
        """Malformed JSON in an event's data raises SSEParseError (not a silent skip)."""
        stream = "id: 1:1\nevent: content.delta\ndata: {not valid json\n\n"
        with pytest.raises(SSEParseError, match="Invalid JSON"):
            _ = [e async for e in parse_sse_stream(CheckedBlockReader(stream))]

    @pytest.mark.unit
    async def test_parse_sse_rejects_unknown_event_type(self):
        """An unknown discriminator is a wire contract violation."""
        unknown = '{"type":"quantum.event","timestamp":"2025-01-01T00:00:00Z","foo":"bar"}'
        stream = (
            f"id: 1:1\nevent: quantum.event\ndata: {unknown}\n\n"
            f"id: 2:1\nevent: content.delta\ndata: {_CONTENT_DELTA}\n\n"
        )

        with pytest.raises(SSEParseError, match="Invalid SSE event"):
            _ = [event async for event in parse_sse_stream(CheckedBlockReader(stream))]

    @pytest.mark.unit
    async def test_parse_sse_accepts_event_at_data_byte_limit(self, monkeypatch: pytest.MonkeyPatch):
        """The event-data cap is inclusive and counts UTF-8 wire bytes."""
        # 24 + 1 joining newline + 50 bytes = 75 bytes, but 71 characters: "é" and "🚀" are multibyte.
        monkeypatch.setattr(sse_parser, "_MAX_SSE_EVENT_BYTES", 75)

        events = [event async for event in parse_sse_stream(CheckedBlockReader(_MULTIBYTE_TWO_LINE_DELTA))]

        assert len(events) == 1
        assert events[0].id == "1:1"
        assert events[0].data == BridgeContentDeltaResponse(
            type="content.delta", delta="héllo 🚀", generation=1, sequence=1
        )

    @pytest.mark.unit
    async def test_parse_sse_rejects_event_one_byte_over_data_limit(self, monkeypatch: pytest.MonkeyPatch):
        """The same 75-byte event is refused one byte under its size."""
        monkeypatch.setattr(sse_parser, "_MAX_SSE_EVENT_BYTES", 74)

        with pytest.raises(SSEParseError, match="SSE event data exceeds 74 bytes"):
            _ = [event async for event in parse_sse_stream(CheckedBlockReader(_MULTIBYTE_TWO_LINE_DELTA))]

    @pytest.mark.unit
    async def test_parse_sse_rejects_oversized_multiline_event(self, monkeypatch: pytest.MonkeyPatch):
        """Complete short lines cannot grow one event beyond the data cap."""
        monkeypatch.setattr(sse_parser, "_MAX_SSE_EVENT_BYTES", 16)
        stream = "data: 12345678\ndata: 12345678\n\n"

        with pytest.raises(SSEParseError, match="SSE event data exceeds 16 bytes"):
            _ = [event async for event in parse_sse_stream(CheckedBlockReader(stream))]
