import asyncio
import contextlib
from datetime import timedelta

from pydantic import BaseModel, ConfigDict

from dualeai import DualeAIConfig, DualeAISDK, tool


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
    """Run a customer-hosted registered-tool agent."""
    sdk = DualeAISDK(config=DualeAIConfig())

    @tool(
        sdk=sdk,
        description="Close a named security gate and return the final gate state.",
        timeout=timedelta(seconds=10),
    )
    async def close_security_gate(command: GateCommand) -> GateState:
        """Developer maintenance note; not sent to the language model or router."""
        # Side-effecting tools must be idempotent or retry-safe. If this SDK
        # process restarts before persisting a result, bridge replay can
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
