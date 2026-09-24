"""The SDK returns terminal answer text with every code point intact.

The fixture contains visible text plus an opaque suffix. The JSON-escaped SSE
wire form must decode exactly; the SDK neither assigns meaning to the suffix nor
modifies it.

The fixture is real codec output, so this suite does not need the codec.
"""

import json
from pathlib import Path

import pytest

from dualeai.events.sse_parser import _MAX_SSE_EVENT_BYTES, _compute_checksum, parse_sse_stream
from dualeai.models.bridge import BridgeTaskCompletedResponse
from tests.test_sse_parser import CheckedBlockReader

_FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "marked_answer.json").read_text())
_ANSWER: str = _FIXTURE["answer"]
_MARKED: str = _FIXTURE["marked"]


def _completed_stream() -> str:
    """Build the SSE bytes the Bridge writes for a marked answer."""
    payload = {
        "type": "task.completed",
        "timestamp": "2026-08-14T00:00:00Z",
        "result": {"completion": _MARKED, "tool_calls": []},
    }
    data_str = json.dumps(payload, separators=(",", ":"))
    checksum = _compute_checksum("task.completed", data_str)
    data_lines = "\n".join(f"data: {line}" for line in data_str.split("\n"))
    return f": crc={checksum}\nid: 1:1\nevent: task.completed\n{data_lines}\n\n"


def _completion_of(event_data: object) -> str:
    """Narrow the terminal event union to its completion text."""
    assert isinstance(event_data, BridgeTaskCompletedResponse)
    completion = event_data.result.completion
    assert completion is not None
    return completion


class TestMarkedAnswerReachesTheCaller:
    """The terminal text emitted by the server is what the SDK returns."""

    @pytest.mark.unit
    async def test_completion_survives_parsing(self) -> None:
        """Byte-for-byte equality, not just visual equality."""
        events = [event async for event in parse_sse_stream(CheckedBlockReader(_completed_stream()))]

        assert len(events) == 1
        completion = _completion_of(events[0].data)
        assert completion == _MARKED
        assert list(completion) == list(_MARKED)

    @pytest.mark.unit
    async def test_the_visible_answer_is_unchanged(self) -> None:
        """The carrier appends; it never edits the answer a human reads."""
        events = [event async for event in parse_sse_stream(CheckedBlockReader(_completed_stream()))]

        assert _completion_of(events[0].data).startswith(_ANSWER)

    @pytest.mark.unit
    async def test_a_marked_answer_stays_under_the_event_size_guard(self) -> None:
        """The fixed carrier cost must not push a normal answer past the parser limit.

        Imports the real bound and parses the real stream rather than restating
        the literal: lowering `_MAX_SSE_EVENT_BYTES` is a plausible hardening
        change, and it would make every marked delivery raise while a test that
        re-typed the number kept passing.
        """
        stream = _completed_stream()

        assert len(stream.encode()) < _MAX_SSE_EVENT_BYTES
        events = [event async for event in parse_sse_stream(CheckedBlockReader(stream))]
        assert len(events) == 1
