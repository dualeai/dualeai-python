"""Task SSE parser for complete authenticated hpke-http/3 blocks.

Parses the SSE wire format (https://html.spec.whatwg.org/multipage/server-sent-events.html), scoped
to the Task API's emission profile: line terminators are LF/CRLF only (bare-CR
delimiters are not handled), and ``id`` is treated as an opaque server-defined
cursor. This is not a general-purpose SSE reader.

``hpke-http`` authenticates each complete SSE block before ``iter_sse()``
releases it. This parser validates the event shape and payload; comments have
no application integrity semantics.

Parsing and exact-text preservation are covered by ``tests/test_sse_parser.py``
and ``tests/test_marked_answer_parsing.py``.
"""

import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from pydantic import ValidationError

from dualeai.models.bridge import BridgeSSEEvent

# One fixed protocol guard covers both a physical line and the joined data
# payload. It keeps untrusted stream input bounded without adding a user-facing
# tuning knob to the Bridge wire contract.
_MAX_SSE_EVENT_BYTES = 8 * 1024 * 1024


class SSEParseError(Exception):
    """Error parsing SSE stream."""

    def __init__(self, message: str, line: str | None = None) -> None:
        self.line = line
        super().__init__(f"{message}: {line!r}" if line else message)


async def parse_sse_stream(  # noqa: PLR0912, PLR0915  # Complex but clear SSE state machine
    content: AsyncIterator[bytes],
) -> AsyncIterator[BridgeSSEEvent]:
    """Parse SSE stream from byte chunks.

    SSE format (per spec):
        id: 123:1
        event: content.delta
        data: {"type": "content.delta", "delta": "...", "generation": 1, "sequence": 0}
        <empty line = event boundary>

    The SSE ``event`` field and JSON ``type`` discriminator must match.

    Supports:
    - Multi-line data (multiple `data:` lines joined with newline)
    - Comments (lines starting with `:`)
    - Byte chunks containing multiple lines (handles HPKE decrypted events)
    - Fixed byte limits for one line and one accumulated event payload

    Args:
        content: Async iterator of complete checked blocks from HPKE iter_sse

    Yields:
        BridgeSSEEvent for each complete SSE event.

    Raises:
        SSEParseError: On malformed or oversized event data.
    """
    event_id: str = ""  # SSE IDs persist until another id field changes them.
    event_type: str = "message"  # SSE default
    data_lines: list[str] = []
    data_bytes = 0
    buffer = bytearray()
    first_line = True

    async for chunk in content:
        # Add chunk to buffer and process complete lines
        buffer.extend(chunk)

        # Process all complete lines in buffer.
        while (line_end := buffer.find(b"\n")) != -1:
            if line_end > _MAX_SSE_EVENT_BYTES:
                raise SSEParseError(f"SSE line exceeds {_MAX_SSE_EVENT_BYTES} bytes")
            line_bytes = bytes(buffer[:line_end])
            del buffer[: line_end + 1]
            line = line_bytes.decode("utf-8", errors="replace").rstrip("\r")
            if first_line:
                line = line.removeprefix("\ufeff")
                first_line = False

            # Empty line = dispatch event (W3C SSE spec)
            if not line:
                # W3C SSE spec: "If the data buffer is an empty string, set the data
                # buffer and the event type buffer to the empty string and return."
                # An empty payload is ignored; non-empty whitespace is invalid Task JSON.
                data_str = "\n".join(data_lines) if data_lines else ""
                if data_str:
                    # NOTE: CRC32 validation and required checksum comments were
                    # removed because hpke-http authenticates each complete block
                    # before iter_sse() releases it. Payload validation stays here.
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError as e:
                        raise SSEParseError(
                            f"Invalid JSON in SSE data: {e}",
                            line=data_str,
                        ) from e

                    event_data_type = data.get("type") if isinstance(data, dict) else None
                    if event_type != event_data_type:
                        raise SSEParseError(
                            f"SSE event type {event_type!r} does not match payload type {event_data_type!r}"
                        )

                    try:
                        yield BridgeSSEEvent(
                            id=event_id,
                            data=data,  # Pydantic discriminates on data.type
                            timestamp=datetime.now(timezone.utc),
                        )
                    except ValidationError as error:
                        raise SSEParseError("Invalid SSE event", line=data_str) from error

                # Reset event fields while retaining the SSE cursor.
                event_type = "message"
                data_lines = []
                data_bytes = 0
                continue

            if line.startswith(":"):
                continue

            # Parse field:value
            if ":" in line:
                field, _, value = line.partition(":")
                # SSE spec: single space after colon is optional, strip it
                if value.startswith(" "):
                    value = value[1:]
            else:
                # Field only, no value (e.g., "data" without colon)
                field = line
                value = ""

            # Process fields per SSE spec
            if field == "id":
                # ID must not contain null (spec requirement)
                if "\0" not in value:
                    event_id = value
            elif field == "event":
                event_type = value
            elif field == "data":
                value_bytes = len(value.encode("utf-8"))
                next_data_bytes = data_bytes + value_bytes + (1 if data_lines else 0)
                if next_data_bytes > _MAX_SSE_EVENT_BYTES:
                    raise SSEParseError(f"SSE event data exceeds {_MAX_SSE_EVENT_BYTES} bytes")
                data_lines.append(value)
                data_bytes = next_data_bytes
            elif field == "retry":
                # Ignore server retry values; HTTPTransport uses its own retry policy.
                # test_parse_sse_skips_events_without_payload covers retry-only frames.
                pass
            # Unknown fields are ignored per spec

        if len(buffer) > _MAX_SSE_EVENT_BYTES:
            raise SSEParseError(f"SSE line exceeds {_MAX_SSE_EVENT_BYTES} bytes")
