"""Auto-generated Pydantic models from the resolved schema compiler graph. Do not edit manually."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from dualeai.models import json_value as json_value_module
from dualeai.models import tool_call_id as tool_call_id_module

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
        tool_call_id_module.ToolCallId,
        Field(
            description="Identifier that correlates this Tool Call with its Tool Result. Return it unchanged in the matching Tool Result. It is not guaranteed unique."
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
