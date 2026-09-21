"""Auto-generated Pydantic models from the resolved schema compiler graph. Do not edit manually."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, StrictStr
from typing_extensions import TypeAliasType

__all__: list[str] = ["CanonicalActor", "Identity"]
CanonicalActor = TypeAliasType(
    "CanonicalActor",
    Annotated[
        Annotated[StrictStr, Field(pattern="^(user|agent):[A-Za-z0-9][A-Za-z0-9_-]*$")],
        Field(
            description="Canonical identity of the user or Agent Identity that acted. Use `user:{id}` or `agent:{id}` after resolving the credential to a current identity. Identity-provider aliases are not accepted."
        ),
    ],
)
Identity = TypeAliasType(
    "Identity",
    Annotated[
        "CanonicalActor",
        Field(
            title="Identity", description="Canonical identity of the user or Agent Identity that performed an action."
        ),
    ],
)
