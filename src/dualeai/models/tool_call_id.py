"""Auto-generated Pydantic models from the resolved schema compiler graph. Do not edit manually."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, StrictStr
from typing_extensions import TypeAliasType

__all__: list[str] = ["ToolCallId"]
ToolCallId = TypeAliasType(
    "ToolCallId",
    Annotated[
        Annotated[StrictStr, Field(min_length=1, max_length=64, pattern="^[a-zA-Z0-9_-]+$")],
        Field(
            title="ToolCallId",
            description="Boundary-scoped Tool Call correlation identifier containing 1 to 64 ASCII letters, digits, underscores, or hyphens. It does not guarantee uniqueness or idempotency; interpret it with the owning Task context.",
            examples=["call_abc123", "toolu_01A2B3C4"],
        ),
    ],
)
