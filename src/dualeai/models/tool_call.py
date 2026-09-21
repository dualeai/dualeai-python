"""Auto-generated Pydantic models from the resolved schema compiler graph. Do not edit manually."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from dualeai.models import json_value as json_value_module

__all__: list[str] = ["ToolCall"]


class ToolCall(BaseModel):
    """Model-requested logical Tool Call in the language-model wire shape."""

    model_config = ConfigDict(
        extra="forbid",
        title="ToolCall",
        json_schema_extra={
            "examples": [
                {
                    "id": "call_abc123",
                    "name": "get_weather",
                    "arguments": {"location": "San Francisco", "units": "celsius"},
                },
                {"id": "call_def456", "name": "search_database", "arguments": {"query": "recent orders", "limit": 10}},
            ]
        },
    )
    id: Annotated[
        Annotated[StrictStr, Field(min_length=1, max_length=256)],
        Field(
            description="Provider-supplied Tool Call correlation identifier containing 1 to 256 unrestricted characters. It is not guaranteed unique. Customer Tool delivery accepts only the narrower ToolCallId shape, so some values accepted here cannot cross that boundary unchanged."
        ),
    ]
    name: Annotated[
        Annotated[StrictStr, Field(min_length=1, max_length=64, pattern="^[a-zA-Z0-9_-]+$")],
        Field(description="Name of the tool to call"),
    ]
    arguments: Annotated[
        Annotated[dict[StrictStr, json_value_module.JsonValue], Field(strict=True)],
        Field(description="Arguments to pass to the tool"),
    ]
