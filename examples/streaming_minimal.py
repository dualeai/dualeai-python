"""Minimal live streaming example.

Requires ``DUALEAI_TOKEN`` and access to a configured model. Run with
``python examples/streaming_minimal.py``. This manual example is not executed by
the automated test suite.
"""

import asyncio
import contextlib

from dualeai import BridgeContentResetResponse, ask, create_sdk


async def main() -> None:
    """Print preview events and then the authoritative terminal result."""
    async with create_sdk() as sdk:
        response = await ask("Write a short poem", streaming=True, sdk=sdk)

        print("Preview:")  # noqa: T201
        async for event in response.stream():
            if isinstance(event, BridgeContentResetResponse):
                # Previously printed text cannot be withdrawn portably. A real
                # UI should clear its whole preview buffer at this point.
                print("\n[preview reset; subsequent text replaces it]")  # noqa: T201
                continue
            print(event.delta, end="", flush=True)  # noqa: T201

        print("\n\nFinal result:")  # noqa: T201
        print(await response.model())  # noqa: T201


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
