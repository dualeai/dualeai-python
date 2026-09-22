"""Continue one live Task and validate the continuation as structured output.

Requires ``DUALEAI_TOKEN`` and access to a configured model. Run with
``python examples/conversation_demo.py``. This manual example is not executed by
the automated test suite.
"""

# ruff: noqa: T201

import asyncio
import contextlib

from pydantic import BaseModel

from dualeai import ask, create_sdk


class BookChapter(BaseModel):
    """Structured result requested from the continuation."""

    title: str
    sentences: list[str]


async def main() -> None:
    """Submit one root Task and continue it with structured output."""
    async with create_sdk() as sdk:
        response = await ask(
            action="Write a three-sentence story about a friendly robot.",
            sdk=sdk,
        )
        print(f"First result: {await response.model()}")

        continued = await response.next(
            message="Add a princess, then return a title and exactly three sentences.",
            res=BookChapter,
        )
        chapter = await continued.model()
        print(f"Continued result: {chapter}")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
