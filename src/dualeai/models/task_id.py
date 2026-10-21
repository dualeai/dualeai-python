"""Auto-generated Pydantic models from the resolved schema compiler graph. Do not edit manually."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, StrictStr
from typing_extensions import TypeAliasType

__all__: list[str] = ["TaskId"]
TaskId = TypeAliasType(
    "TaskId",
    Annotated[
        Annotated[StrictStr, Field(min_length=10, max_length=128, pattern="^[a-zA-Z0-9_-]+$")],
        Field(
            title="TaskId",
            description="Execution request identifier. At public admission, it is the Task identifier that the developer or the SDK selects.",
            examples=["task-12345678901234567890", "req-abcdef123456"],
        ),
    ],
)
