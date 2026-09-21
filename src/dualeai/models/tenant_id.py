"""Auto-generated Pydantic models from the resolved schema compiler graph. Do not edit manually."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, StrictStr
from typing_extensions import TypeAliasType

__all__: list[str] = ["TenantId"]
TenantId = TypeAliasType(
    "TenantId",
    Annotated[
        Annotated[StrictStr, Field(min_length=3, max_length=50, pattern="^[a-zA-Z][a-zA-Z0-9_-]*$")],
        Field(title="TenantId", description="Unique tenant identifier", examples=["company-a", "company-b"]),
    ],
)
