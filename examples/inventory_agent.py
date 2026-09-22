"""Customer-hosted inventory agent showing representative ``@tool`` behavior.

Requires ``DUALEAI_TOKEN``, ``DUALEAI_AGENT_ID``, and access to a configured
model. Run it with::

    python examples/inventory_agent.py

Set ``DUALEAI_ENDPOINT`` only when your access instructions name a non-default
environment.

This is a live, manually run example; the automated test suite does not execute
it.

``sdk.serve()`` publishes the Tool manifest and heartbeats until shutdown; it
does not receive Tool calls. The demo Tasks opened by the same SDK instance own
the streams on which matching ``tool.use`` events are dispatched to the
callables below. Nothing here talks to a real warehouse — the bodies stand in
for your side effects so the lifecycle is what the example demonstrates.

Four tools, one concept each:

* ``lookup_stock``    — async, typed Pydantic in/out, model-facing ``Annotated`` field constraints.
* ``estimate_restock`` — plain ``def`` (blocking CPU work) offloaded to a worker thread.
* ``reserve_units``   — side-effecting; demonstrates same-process replay detection and the durable-ledger boundary.
* ``adjust_price``    — ``error_transform`` redacts an upstream secret before the message reaches the model.
"""

import asyncio
import contextlib
from datetime import timedelta
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from dualeai import DualeAISDK, ask, current_tool_context, tool

# A stand-in "warehouse" so the example runs with no external system. Both the
# stock and replay ledger are process-local; production code must persist the
# idempotency key and side effect atomically before it can be restart-safe.
_STOCK: dict[str, int] = {"sku-widget": 120, "sku-gadget": 4, "sku-sprocket": 0}
_RESERVATIONS: dict[tuple[str, str], int] = {}

# Prices above this (in cents) breach the pricing guardrail and raise.
_MAX_PRICE_CENTS = 50_000


class StockQuery(BaseModel):
    """Model-facing input schema for stock lookup.

    ``Annotated[..., Field(...)]`` constraints and descriptions reach the language
    model's tool schema; the Python docstring does not. Document caller-facing
    hints here, not in the docstring.
    """

    model_config = ConfigDict(extra="forbid")

    sku: Annotated[str, Field(description="Stock-keeping unit, e.g. 'sku-widget'.", min_length=1)]


class StockLevel(BaseModel):
    """Model-facing result schema for stock lookup."""

    model_config = ConfigDict(extra="forbid")

    sku: str
    on_hand: int
    in_stock: bool


class PriceOutOfBandError(Exception):
    """Raised when a price change would breach the configured guardrail.

    Its message carries the upstream connection string to show why
    ``error_transform`` matters — that text must never reach the model.
    """


async def main() -> None:
    """Register the inventory tools, serve them, then drive a task through the flow."""
    sdk = DualeAISDK(auto_start=False)

    @tool(
        sdk=sdk,
        description="Return the on-hand quantity for a single SKU.",
        timeout=timedelta(seconds=5),
    )
    async def lookup_stock(query: StockQuery) -> StockLevel:
        """Read-only; safe to retry, so no idempotency handling needed."""
        on_hand = _STOCK.get(query.sku, 0)
        return StockLevel(sku=query.sku, on_hand=on_hand, in_stock=on_hand > 0)

    @tool(
        sdk=sdk,
        description="Estimate days until the next restock arrives for a SKU.",
        timeout=timedelta(seconds=10),
    )
    def estimate_restock(
        sku: Annotated[str, Field(description="SKU to estimate restock for.", min_length=1)],
        daily_demand: Annotated[int, Field(gt=0, le=10_000, description="Expected units sold per day.")],
    ) -> dict[str, int | str]:
        # Plain ``def`` — a blocking body runs in a worker thread, so this CPU-bound
        # loop never stalls the SDK event loop or the heartbeat.
        on_hand = _STOCK.get(sku, 0)
        days_of_cover = 0
        remaining = on_hand
        while remaining >= daily_demand:
            remaining -= daily_demand
            days_of_cover += 1
        return {"sku": sku, "days_of_cover": days_of_cover, "on_hand": on_hand}

    @tool(
        sdk=sdk,
        description="Reserve units of a SKU for an order. Reduces on-hand stock.",
        timeout=timedelta(seconds=15),
    )
    async def reserve_units(
        sku: Annotated[str, Field(description="SKU to reserve.", min_length=1)],
        units: Annotated[int, Field(gt=0, le=1_000, description="How many units to reserve.")],
    ) -> dict[str, int | str]:
        # Keyed on (task_id, tool_call_id): the call id comes from the model
        # provider and is not unique on its own. This detects a duplicate only
        # while this example process remains alive. In production, store that key
        # and the inventory mutation in one durable transaction before returning
        # success -- and note the key still cannot separate two genuine identical
        # reservations in one task, so a real system gives each one its own id.
        context = current_tool_context()
        key = (context.task_id, context.tool_call_id) if context is not None else None
        if key is not None and key in _RESERVATIONS:
            already = _RESERVATIONS[key]
            return {"sku": sku, "reserved": already, "on_hand": _STOCK.get(sku, 0), "status": "already-reserved"}

        on_hand = _STOCK.get(sku, 0)
        if units > on_hand:
            raise ValueError(f"cannot reserve {units} of {sku}: only {on_hand} on hand")

        _STOCK[sku] = on_hand - units
        if key is not None:
            _RESERVATIONS[key] = units
        return {"sku": sku, "reserved": units, "on_hand": _STOCK[sku], "status": "reserved"}

    @tool(
        sdk=sdk,
        description="Adjust the list price of a SKU within the pricing guardrail.",
        timeout=timedelta(seconds=8),
        error_transform=_redact_pricing_error,
    )
    async def adjust_price(
        sku: Annotated[str, Field(description="SKU to reprice.", min_length=1)],
        new_price_cents: Annotated[int, Field(gt=0, le=1_000_000, description="New list price in cents.")],
    ) -> dict[str, int | str]:
        # A raised exception is rendered to the model as its message. This one
        # embeds a secret to prove error_transform strips it before the wire.
        if new_price_cents > _MAX_PRICE_CENTS:
            raise PriceOutOfBandError(
                f"price {new_price_cents} exceeds guardrail "
                f"(upstream=postgresql://pricing:s3cr3t@db.internal:5432/prices)"
            )
        return {"sku": sku, "new_price_cents": new_price_cents, "status": "repriced"}

    # Registered via decorator side effect; asserted so linters keep the bindings.
    assert lookup_stock and estimate_restock and reserve_units and adjust_price

    # Register the manifest + start heartbeats BEFORE we submit any task, so the
    # agent is online before the platform can select a Tool host. serve() reuses the
    # same started lifecycle (its internal start() is a no-op) and keeps the
    # heartbeat running while the demo task streams; tool.use events dispatch on
    # the task's own SSE stream, not on serve().
    await sdk.start()
    print("Inventory tools published; agent online.")  # noqa: T201

    stop = asyncio.Event()
    serve_task = asyncio.create_task(sdk.serve(stop_event=stop))
    try:
        await _run_demo(sdk)
    finally:
        stop.set()  # end the lifecycle loop; serve() runs cleanup on exit.
        await serve_task


async def _run_demo(sdk: DualeAISDK) -> None:
    """Submit a few Tasks that exercise several registered Tools.

    Each ``ask`` opens a task SSE stream; when the model calls a registered tool,
    the ``tool.use`` is dispatched to the matching callable above, its result is
    submitted back, and the stream continues to a terminal answer.
    """
    prompts = [
        "How many units of sku-widget do we have on hand?",
        "Reserve 5 units of sku-widget for order 42, then tell me the remaining stock.",
        "Reprice sku-gadget to 90000 cents.",  # Trips the guardrail; error_transform redacts the secret.
    ]
    for prompt in prompts:
        response = await ask(prompt, sdk=sdk)
        answer = await response.model()
        print(f"\n> {prompt}\n{answer}")  # noqa: T201


def _redact_pricing_error(exc: Exception) -> str:
    """Map a pricing failure to a model-safe message, dropping any secret payload.

    State the actionable bound so the model converges on a valid price; drop only
    the secret. (A *static* bound would instead belong in a typed
    ``Field(le=...)`` constraint, which reaches the model's tool schema; this
    guardrail is treated as dynamic, so it stays a runtime check that reports its
    limit here.)
    """
    if isinstance(exc, PriceOutOfBandError):
        return f"Requested price exceeds the pricing guardrail; maximum is {_MAX_PRICE_CENTS} cents."
    return "Pricing service is unavailable; try again shortly."


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
