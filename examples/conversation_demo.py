# ruff: noqa: T201

import asyncio
import contextlib
from random import randint

import structlog
from pydantic import BaseModel

from dualeai import (
    DualeAIConfig,
    DualeAISDK,
    RoutingPolicy,
    ask,
)

logger = structlog.get_logger(__name__)


class BookChapter(BaseModel):
    title: str
    sentences: list[str]


async def main():
    config = DualeAIConfig()

    # --- Step 1: Structured output (BookChapter) ---
    print("\n--- Step 1: ask() with structured output (BookChapter) ---")
    async with DualeAISDK(config=config, auto_start=False) as sdk:
        response = await ask(
            sdk=sdk,
            action=(
                "Generate a short story about a friendly robot (3 sentences)."
                f" Title: The Friendly Robot #{randint(1, 1000)}"
            ),
            res=BookChapter,
            routing=RoutingPolicy(
                target_accuracy=0.3,
            ),
        )
        book = await response.model()
        print(f"  Result: {book}")

    # --- Step 2: Unstructured ask + continuation ---
    print("\n--- Step 2: ask() without structured output ---")
    async with DualeAISDK(config=config, auto_start=False) as sdk:
        response = await ask(
            action="Generate a short story about a friendly robot (3 sentences).",
            sdk=sdk,
        )
        draft = await response.model()
        print(f"  First draft: {draft}")

        print("\n--- Step 3: response.next() with structured output ---")
        continued = await response.next(
            message="Add a princess character.",
            res=BookChapter,
        )
        book2 = await continued.model()
        print(f"  Second book: {book2}")

    print("\n--- All steps completed ---")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
