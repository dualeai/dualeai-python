"""Production streaming example with logging and error handling."""

import asyncio
import contextlib

import structlog

from dualeai import DualeAISDK, ask
from dualeai.models.bridge import BridgeContentResetResponse

logger = structlog.get_logger(__name__)


async def main() -> None:
    async with DualeAISDK(auto_start=False) as sdk:
        response = await ask("Write a poem about the ocean", streaming=True, sdk=sdk)

        chunk_count = 0
        async for event in response.stream():
            if isinstance(event, BridgeContentResetResponse):
                chunk_count = 0
                print("\r\033[2K", end="", flush=True)  # noqa: T201
                continue
            print(event.delta, end="", flush=True)  # noqa: T201
            chunk_count += 1

        # Printed deltas were a preview. The terminal answer is authoritative
        # and is the one carrying the machine-generated content mark, so it is
        # what you keep, store, or forward.
        # `model()` is typed `object` for an untyped response — render it,
        # do not assume `str`.
        answer = f"{await response.model()}"
        logger.info("stream_complete", chunks=chunk_count, final_length=len(answer))
        print(f"\n\n{answer}\n")  # noqa: T201


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
