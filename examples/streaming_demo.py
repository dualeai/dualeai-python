"""Record live streaming resets and completion with structured logging.

Requires ``DUALEAI_TOKEN`` and access to a configured model. Run with
``python examples/streaming_demo.py``. This manual example is not executed by
the automated test suite.
"""

import asyncio
import contextlib

import structlog

from dualeai import BridgeContentResetResponse, ask, create_sdk

logger = structlog.get_logger(__name__)


async def main() -> None:
    """Stream one live Task and log reset and completion metadata."""
    async with create_sdk() as sdk:
        response = await ask("Write a poem about the ocean", streaming=True, sdk=sdk)

        preview: list[str] = []
        content_events = 0
        reset_count = 0
        async for event in response.stream():
            if isinstance(event, BridgeContentResetResponse):
                preview.clear()
                reset_count += 1
                logger.info("stream_reset", resets=reset_count)
                continue
            preview.append(event.delta)
            content_events += 1

        answer = f"{await response.model()}"
        logger.info(
            "stream_complete",
            content_events=content_events,
            resets=reset_count,
            preview_length=len("".join(preview)),
            final_length=len(answer),
        )
        print(f"Final result:\n{answer}")  # noqa: T201


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
