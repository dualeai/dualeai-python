"""
Web UI streaming pattern example.

Demonstrates how to handle streaming in a web application context.
This pattern can be adapted for:
- FastAPI/Flask websockets
- Server-Sent Events (SSE)
- React/Vue/Svelte frontends

The key concept: maintain separate states for content, status, and metadata
that your frontend can reactively update.

Streamed deltas are a replaceable preview. When the stream ends, replace the
displayed text with `await response.model()` — that terminal answer is the
authoritative one, and the only one that carries the platform's
machine-generated content mark.
"""

import asyncio
import random
from dataclasses import dataclass, field
from enum import Enum

from dualeai import DualeAIConfig, DualeAISDK, SkillEnum, ask
from dualeai.models.bridge import BridgeContentResetResponse


class StreamState(str, Enum):
    """Stream states for UI."""

    IDLE = "idle"
    STREAMING = "streaming"
    COMPLETE = "complete"
    ERROR = "error"


@dataclass
class UIState:
    """
    State container for web UI.

    Your frontend would subscribe to changes in this state object.
    For React, this would be useState hooks.
    For Vue, this would be reactive refs.
    """

    # Content
    content: list[str] = field(default_factory=list)

    # Status
    state: StreamState = StreamState.IDLE
    status_message: str | None = None

    # Metadata
    chunk_count: int = 0

    def get_content_text(self) -> str:
        """Get full content as string."""
        return "".join(self.content)

    def to_dict(self) -> dict[str, object]:
        """
        Serialize for JSON API response.

        This is what you'd send to your frontend via WebSocket or SSE.
        """
        return {
            "content": self.get_content_text(),
            "state": self.state.value,
            "statusMessage": self.status_message,
            "metadata": {
                "chunkCount": self.chunk_count,
            },
        }


async def simulate_websocket_send(state: UIState) -> None:
    """
    Simulate sending state to frontend via WebSocket/SSE.

    In a real application, this would be:
    - await websocket.send_json(state.to_dict())  # WebSocket
    - yield f"data: {json.dumps(state.to_dict())}\\n\\n"  # SSE
    """
    print(f"Frontend update: {state.to_dict()}")  # noqa: T201


async def stream_with_ui_state_management() -> None:
    """
    Demonstrates streaming with proper UI state management.

    This pattern separates concerns:
    1. Business logic (streaming)
    2. UI state (what to show user)
    3. Communication (WebSocket/SSE updates)
    """
    ui_state = UIState()

    config = DualeAIConfig()  # Reads DUALEAI_TOKEN and optional DUALEAI_ENDPOINT from the environment.

    async with DualeAISDK(config=config, auto_start=False) as sdk:
        response = await ask(
            action=f"Write a creative story about AI and humans working together. ID: {random.randint(100, 999)}",
            skills=[SkillEnum.general],
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

        # The deltas were a preview. Replace them with the authoritative
        # answer before showing, storing, indexing or exporting anything:
        # the terminal result is the only source of truth, and it can differ
        # from the accumulated text.
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


async def main() -> None:
    """Run web UI pattern demo."""
    print("Web UI Streaming Pattern Demo\n")  # noqa: T201
    print("This shows how to handle streaming in a web application.")  # noqa: T201
    print("Replace 'simulate_websocket_send' with real WebSocket/SSE.\n")  # noqa: T201
    print("-" * 60 + "\n")  # noqa: T201

    await stream_with_ui_state_management()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user")  # noqa: T201
