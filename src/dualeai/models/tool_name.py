"""Auto-generated Pydantic models from the resolved schema compiler graph. Do not edit manually."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, StrictStr
from typing_extensions import TypeAliasType

__all__: list[str] = ["ToolName"]
ToolName = TypeAliasType(
    "ToolName",
    Annotated[
        Annotated[StrictStr, Field(min_length=1, max_length=64, pattern="^[a-zA-Z0-9_-]+$")],
        Field(title="ToolName", description="Short model-facing Tool name controlled by Duale AI."),
    ],
)
