"""Keep content, status, and metadata separate while streaming to a web UI.

Streamed deltas are a replaceable preview. When the stream ends, replace the
displayed text with `await response.model()` — that terminal answer is the
authoritative one and can differ from the accumulated preview.

Requires ``DUALEAI_TOKEN`` and access to a configured model. Run with
``python examples/streaming_web_ui_pattern.py``. This manual pattern prints the
state that a real application would send; the automated test suite does not
execute it.
"""

import asyncio
import contextlib
from dataclasses import dataclass, field
from enum import Enum

from dualeai import BridgeContentResetResponse, ask, create_sdk


class StreamState(str, Enum):
    """Stream states for UI."""

    IDLE = "idle"
    STREAMING = "streaming"
    COMPLETE = "complete"
    ERROR = "error"


@dataclass
class UIState:
    """State sent to the frontend after each stream change."""

    # Content
    content: list[str] = field(default_factory=list)

    # Status
    state: StreamState = StreamState.IDLE
    status_message: str | None = None

    # Metadata
    chunk_count: int = 0

    def get_content_text(self) -> str:
        """Join the current content buffer."""
        return "".join(self.content)

    def to_dict(self) -> dict[str, object]:
        """Serialize the state for a WebSocket or SSE payload."""
        return {
            "content": self.get_content_text(),
            "state": self.state.value,
            "statusMessage": self.status_message,
            "metadata": {
                "chunkCount": self.chunk_count,
            },
        }


async def simulate_websocket_send(state: UIState) -> None:
    """Stand in for sending one WebSocket or SSE update."""
    print(f"Frontend update: {state.to_dict()}")  # noqa: T201


async def stream_with_ui_state_management() -> None:
    """Stream one Task while publishing separate content and status state."""
    ui_state = UIState()

    try:
        async with create_sdk() as sdk:
            response = await ask(
                action="Write a creative story about AI and humans working together.",
                streaming=True,
                sdk=sdk,
            )

            # Update state: streaming started
            ui_state.state = StreamState.STREAMING
            ui_state.status_message = "Generating response..."
            await simulate_websocket_send(ui_state)

            async for event in response.stream():
                if isinstance(event, BridgeContentResetResponse):
                    ui_state.content.clear()
                    ui_state.chunk_count = 0
                    ui_state.status_message = "Replacing response..."
                    await simulate_websocket_send(ui_state)
                    continue
                ui_state.content.append(event.delta)
                ui_state.chunk_count += 1
                ui_state.status_message = None
                await simulate_websocket_send(ui_state)

            # Replace the preview with the authoritative terminal answer before
            # storing, indexing, or exporting it.
            preview_text = ui_state.get_content_text()
            ui_state.content = [f"{await response.model()}"]
            ui_state.state = StreamState.COMPLETE
            ui_state.status_message = "Response complete"
            await simulate_websocket_send(ui_state)

            # Summary
            print("\nStream complete:")  # noqa: T201
            print(f"   Preview length: {len(preview_text)} chars")  # noqa: T201
            print(f"   Final length: {len(ui_state.get_content_text())} chars")  # noqa: T201
            print(f"   Chunks received: {ui_state.chunk_count}")  # noqa: T201
    except Exception as error:
        # Keep the user-facing message type-only; exception text can contain
        # request or response data. A real application would log under its own
        # redaction policy and map the error to an appropriate HTTP status.
        ui_state.state = StreamState.ERROR
        ui_state.status_message = f"Response failed ({type(error).__name__})"
        await simulate_websocket_send(ui_state)
        raise


async def main() -> None:
    """Run web UI pattern demo."""
    await stream_with_ui_state_management()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
