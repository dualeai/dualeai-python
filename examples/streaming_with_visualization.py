"""Advanced streaming example with Rich visualization.

Demonstrates real-time streaming visualization with:
- Live updating panels
- Color-coded status

Requires ``rich``, ``DUALEAI_TOKEN``, and access to a configured model. Install
the extra dependency with ``python -m pip install rich``, then run
``python examples/streaming_with_visualization.py``. This manual example is not
executed by the automated test suite.
"""

import asyncio
import contextlib

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from dualeai import BridgeContentResetResponse, ask, create_sdk

console = Console()


async def main() -> None:
    """Run advanced streaming demo with Rich visualization."""
    async with create_sdk() as sdk:
        response = await ask(
            action="Write a detailed story about space exploration.",
            streaming=True,
            sdk=sdk,
        )

        content_buffer: list[str] = []
        chunk_count = 0

        with Live(console=console, refresh_per_second=10, vertical_overflow="visible") as live:
            async for event in response.stream():
                if isinstance(event, BridgeContentResetResponse):
                    content_buffer.clear()
                    chunk_count = 0
                    live.update(
                        Panel(
                            Text(""),
                            title="Replacing response",
                            title_align="left",
                            border_style="yellow",
                            padding=(1, 2),
                        )
                    )
                    continue
                content_buffer.append(event.delta)
                chunk_count += 1

                title = f"Streaming | Chunks: {chunk_count}"
                live.update(
                    Panel(
                        Text("".join(content_buffer)),
                        title=title,
                        title_align="left",
                        border_style="green",
                        padding=(1, 2),
                    )
                )

            # Final panel shows the authoritative answer, which can differ
            # from the accumulated preview.
            live.update(
                Panel(
                    Text(f"{await response.model()}"),
                    title=f"Complete | Chunks: {chunk_count}",
                    title_align="left",
                    border_style="blue",
                    padding=(1, 2),
                )
            )

        console.print("\n[bold]Stream complete[/bold]")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
