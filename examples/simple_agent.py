"""Publish a Tool manifest and maintain Agent lifecycle state.

This serve-only process opens no Task stream, so it cannot execute Tool calls
by itself. See ``tool_execution.py`` for the smallest same-SDK execution path.

Requires ``DUALEAI_TOKEN`` and ``DUALEAI_AGENT_ID``. Run with
``python examples/simple_agent.py`` and interrupt it to stop heartbeats. This
manual example is not executed by the automated test suite.
"""

import asyncio
import contextlib
from datetime import timedelta

from pydantic import BaseModel, ConfigDict

from dualeai import DualeAISDK, tool


class GateCommand(BaseModel):
    """Serializable input schema for the model-facing tool."""

    model_config = ConfigDict(extra="forbid")

    gate_id: str
    reason: str


class GateState(BaseModel):
    """Serializable result returned to the bridge as tool output."""

    model_config = ConfigDict(extra="forbid")

    gate_id: str
    state: str
    command_id: str


async def main() -> None:
    """Publish one registered Tool and serve lifecycle heartbeats."""
    sdk = DualeAISDK(auto_start=False)

    @tool(
        sdk=sdk,
        description="Close a named security gate and return the final gate state.",
        timeout=timedelta(seconds=10),
    )
    async def close_security_gate(command: GateCommand) -> GateState:
        """Developer maintenance note; not sent to the language model or service."""
        # Side-effecting tools must be idempotent or retry-safe. If this SDK
        # process restarts before persisting a result, event replay can
        # redeliver a received tool.use event.
        return GateState(
            gate_id=command.gate_id,
            state="closed",
            command_id=f"close-{command.gate_id}",
        )

    assert close_security_gate  # Registered via decorator side effect.

    print("Registered tools published. Heartbeats run until shutdown.")  # noqa: T201
    await sdk.serve()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
