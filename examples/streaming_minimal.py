"""Minimal streaming example."""

import asyncio

from dualeai import DualeAISDK, ask
from dualeai.models.bridge import BridgeContentResetResponse


async def main() -> None:
    async with DualeAISDK(auto_start=False) as sdk:
        response = await ask("Write a short poem", streaming=True, sdk=sdk)

        async for event in response.stream():
            if isinstance(event, BridgeContentResetResponse):
                print("\r\033[2K", end="", flush=True)  # noqa: T201
                continue
            print(event.delta, end="", flush=True)  # noqa: T201

        # Deltas are a display preview; this is the authoritative answer.
        print(f"\n\n{await response.model()}")  # noqa: T201


if __name__ == "__main__":
    asyncio.run(main())
