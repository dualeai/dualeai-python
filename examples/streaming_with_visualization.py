"""
Advanced streaming example with Rich visualization.

Demonstrates real-time streaming visualization with:
- Live updating panels
- Color-coded status

Requires: pip install rich
"""

import asyncio
import random

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from dualeai import DualeAIConfig, DualeAISDK, SkillEnum, ask
from dualeai.models.bridge import BridgeContentResetResponse

console = Console()


async def main() -> None:
    """Run advanced streaming demo with Rich visualization."""
    config = DualeAIConfig()

    async with DualeAISDK(config=config, auto_start=False) as sdk:
        response = await ask(
            action=f"Write a detailed story about space exploration. Make it engaging! ID: {random.randint(100, 999)}",
            skills=[SkillEnum.general],
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

            # Final panel shows the authoritative answer, not the accumulated
            # preview: the terminal result is the source of truth and the only
            # text carrying the machine-generated content mark.
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
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
