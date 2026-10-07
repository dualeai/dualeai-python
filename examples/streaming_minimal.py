"""Minimal live streaming example.

Requires ``DUALEAI_TOKEN`` and access to a configured model. Run with
``python examples/streaming_minimal.py``. This manual example is not executed by
the automated test suite.

Streamed chunks can arrive out of order. The example keeps the highest
``generation``, orders its chunks by ``sequence``, and prints the text again
when a late chunk or a new generation changes text it already printed.
``await response.model()`` returns the final answer.
"""

import asyncio
import contextlib
from dataclasses import dataclass, field

from dualeai import BridgeContentDeltaResponse, BridgeContentResetResponse, ask, create_sdk


@dataclass
class OrderedText:
    """Current answer text under the client ordering rule."""

    generation: int = 0
    parts: dict[int, str] = field(default_factory=dict)

    def apply(self, event: BridgeContentDeltaResponse | BridgeContentResetResponse) -> bool:
        """Keep only the highest generation, and store each delta at its sequence.

        Returns whether the event changed the text: a delta of the current generation, or any
        event that starts a higher one. An older generation, or a late reset of the current one,
        changes nothing.
        """
        if event.generation < self.generation:
            return False
        started = event.generation > self.generation
        if started:
            self.generation, self.parts = event.generation, {}
        if isinstance(event, BridgeContentDeltaResponse):
            self.parts[event.sequence] = event.delta
            return True
        return started

    def text(self) -> str:
        """Join the kept chunks in sequence order."""
        return "".join(self.parts[sequence] for sequence in sorted(self.parts))


async def main() -> None:
    """Print the answer while it streams, then the final result."""
    async with create_sdk() as sdk:
        response = await ask("Write a short poem", streaming=True, sdk=sdk)

        answer = OrderedText()
        printed = ""
        async for event in response.stream():
            if not answer.apply(event):
                continue
            current = answer.text()
            if current.startswith(printed):
                print(current[len(printed) :], end="", flush=True)  # noqa: T201
            else:
                # A terminal cannot withdraw printed text, so print the corrected text again.
                print(f"\n[corrected]\n{current}", end="", flush=True)  # noqa: T201
            printed = current

        result = await response.model()
        print("\n\nFinal result:")  # noqa: T201
        print(result)  # noqa: T201


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
