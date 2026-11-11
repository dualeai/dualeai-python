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
            description="Identifier that pairs a Tool Call with its Tool Result: 1 to 64 ASCII letters, digits, underscores, or hyphens. It can repeat; read it together with its Task.",
            examples=["call-0001", "call-0002"],
        ),
    ],
)
