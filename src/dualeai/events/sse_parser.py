"""SSE (Server-Sent Events) parser for aiohttp.

Custom implementation since aiohttp-sse-client is inactive. Parses the SSE wire
format (https://html.spec.whatwg.org/multipage/server-sent-events.html) SCOPED to
the bridge's emission profile: line terminators are LF/CRLF only (bare-CR
delimiters are NOT handled), and ``id`` is the strict
``NATS-sequence:event-index`` cursor. Not a general-purpose SSE reader.

Integrity validation: Server sends `: crc={hex}` comment before each event.
Client validates CRC32 of "{event_type}:{data}" against checksum.
On mismatch, raises SSEChecksumError for retry with Last-Event-ID.
"""

import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from zlib import crc32

from pydantic import ValidationError

from dualeai.models.bridge import BridgeSSEEvent

_CRC32_HEX_LENGTH = 8
# One fixed protocol guard covers both a physical line and the joined data
# payload. It keeps untrusted stream input bounded without adding a user-facing
# tuning knob to the Bridge wire contract.
_MAX_SSE_EVENT_BYTES = 8 * 1024 * 1024


class SSEParseError(Exception):
    """Error parsing SSE stream."""

    def __init__(self, message: str, line: str | None = None) -> None:
        self.line = line
        super().__init__(f"{message}: {line!r}" if line else message)


class SSEChecksumError(SSEParseError):
    """CRC32 checksum mismatch - includes event_id for retry with Last-Event-ID."""

    def __init__(self, event_id: str, expected: str, actual: str, event_type: str) -> None:
        self.event_id = event_id
        self.expected = expected
        self.actual = actual
        self.event_type = event_type
        super().__init__(f"CRC32 mismatch for {event_type} (id={event_id}): expected {expected}, got {actual}")


def _compute_checksum(event_type: str, data_str: str) -> str:
    """Compute CRC32 checksum matching server format."""
    payload = f"{event_type}:{data_str}"
    checksum = crc32(payload.encode("utf-8")) & 0xFFFFFFFF
    return f"{checksum:08x}"


async def parse_sse_stream(  # noqa: PLR0912, PLR0915  # Complex but clear SSE state machine
    content: AsyncIterator[bytes],
) -> AsyncIterator[BridgeSSEEvent]:
    """Parse SSE stream from byte chunks.

    SSE format (per spec):
        : crc=a1b2c3d4
        id: 123:1
        event: content.delta
        data: {"type": "content.delta", "delta": "..."}
        <empty line = event boundary>

    The SSE ``event`` field and JSON ``type`` discriminator must match.

    Supports:
    - Multi-line data (multiple `data:` lines joined with newline)
    - Comments (lines starting with `:`)
    - Required CRC32 checksum validation (`: crc={hex}` comments)
    - Byte chunks containing multiple lines (handles HPKE decrypted events)
    - Fixed byte limits for one line and one accumulated event payload

    Args:
        content: Async iterator of bytes (from aiohttp.StreamReader or HPKE iter_sse)

    Yields:
        BridgeSSEEvent for each complete SSE event.

    Raises:
        SSEParseError: On malformed or oversized event data.
        SSEChecksumError: On CRC32 mismatch (triggers retry with Last-Event-ID).
    """
    event_id: str = ""
    event_type: str = "message"  # SSE default
    data_lines: list[str] = []
    data_bytes = 0
    pending_checksum: str | None = None
    buffer = bytearray()

    async for chunk in content:
        # Add chunk to buffer and process complete lines
        buffer.extend(chunk)

        # Process all complete lines in buffer. One scan per line via find()
        # (the previous ``b"\n" in buffer`` + ``.index`` scanned twice).
        while (line_end := buffer.find(b"\n")) != -1:
            if line_end > _MAX_SSE_EVENT_BYTES:
                raise SSEParseError(f"SSE line exceeds {_MAX_SSE_EVENT_BYTES} bytes")
            line_bytes = bytes(buffer[:line_end])
            del buffer[: line_end + 1]
            line = line_bytes.decode("utf-8").rstrip("\r")

            # Empty line = dispatch event (W3C SSE spec)
            if not line:
                # W3C SSE spec: "If the data buffer is an empty string, set the data
                # buffer and the event type buffer to the empty string and return."
                # This means: empty data = no event dispatch. Using strip() for robustness.
                data_str = "\n".join(data_lines) if data_lines else ""
                if data_str.strip():
                    if pending_checksum is None:
                        raise SSEParseError("Missing CRC32 checksum")

                    actual = _compute_checksum(event_type, data_str)
                    if actual != pending_checksum:
                        raise SSEChecksumError(
                            event_id=event_id,
                            expected=pending_checksum,
                            actual=actual,
                            event_type=event_type,
                        )

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

                # Reset state for next event (always, even for skipped events)
                event_id = ""
                event_type = "message"
                data_lines = []
                data_bytes = 0
                pending_checksum = None
                continue

            # Comment line - check for checksum
            if line.startswith(":"):
                comment = line[1:].lstrip()  # Strip colon and leading whitespace
                if comment.startswith("crc="):
                    checksum = comment[4:]
                    if len(checksum) != _CRC32_HEX_LENGTH or any(
                        character not in "0123456789abcdef" for character in checksum
                    ):
                        raise SSEParseError("Invalid CRC32 checksum", line=checksum)
                    pending_checksum = checksum
                # Other comments (heartbeat, etc.) ignored
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
                event_type = value  # Capture for checksum validation
            elif field == "data":
                value_bytes = len(value.encode("utf-8"))
                next_data_bytes = data_bytes + value_bytes + (1 if data_lines else 0)
                if next_data_bytes > _MAX_SSE_EVENT_BYTES:
                    raise SSEParseError(f"SSE event data exceeds {_MAX_SSE_EVENT_BYTES} bytes")
                data_lines.append(value)
                data_bytes = next_data_bytes
            elif field == "retry":
                # Retry field - ignored, handled at transport level
                pass
            # Unknown fields are ignored per spec

        if len(buffer) > _MAX_SSE_EVENT_BYTES:
            raise SSEParseError(f"SSE line exceeds {_MAX_SSE_EVENT_BYTES} bytes")
