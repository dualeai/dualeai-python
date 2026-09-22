"""Register and execute one customer-hosted Tool on the same SDK instance.

Requires ``DUALEAI_TOKEN``, ``DUALEAI_AGENT_ID``, and access to a configured
model. Run with ``python examples/tool_execution.py``. The Tool returns synthetic
data and has no external side effect. This live example is not executed by the
automated test suite.
"""

import asyncio
import contextlib
from datetime import timedelta

from dualeai import DualeAISDK, ask, tool


async def main() -> None:
    """Publish one Tool, open one Task stream, and print its final result."""
    # Tool registration binds to a specific SDK, so construct that instance
    # directly and start it only after every Tool has been registered.
    sdk = DualeAISDK(auto_start=False)

    @tool(
        sdk=sdk,
        description="Return the available quantity for a stock-keeping unit.",
        timeout=timedelta(seconds=5),
    )
    async def lookup_stock(sku: str) -> dict[str, int | str]:
        return {"sku": sku, "available": 12}

    assert lookup_stock  # Registered through the decorator.

    async with sdk:
        await sdk.start()  # Publish the manifest and start heartbeats.
        response = await ask(
            "Use lookup_stock to report the available quantity for sku-widget.",
            sdk=sdk,
        )
        print(f"Task: {response.task_id}")  # noqa: T201
        print(f"Result: {await response.model()}")  # noqa: T201


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
