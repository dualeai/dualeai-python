"""Auto-generated Pydantic models from the resolved schema compiler graph. Do not edit manually."""

from __future__ import annotations

from enum import Enum

from pydantic import GetJsonSchemaHandler
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema

__all__: list[str] = ["ReservedToolName"]


class ReservedToolName(str, Enum):
    """A platform-owned Tool name. A customer Tool registration or per-Task Tool Definition that uses one is rejected."""

    exec_agent = "exec_agent"
    finish = "finish"
    list_agents = "list_agents"
    search_available_tools = "search_available_tools"
    context_ls = "context_ls"
    context_search = "context_search"
    context_grep = "context_grep"
    context_cat = "context_cat"
    conversation_ls = "conversation_ls"
    conversation_search = "conversation_search"
    conversation_grep = "conversation_grep"
    conversation_cat = "conversation_cat"

    @staticmethod
    def __get_pydantic_json_schema__(core_schema: CoreSchema, handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        json_schema = handler.resolve_ref_schema(handler(core_schema))
        json_schema.update(
            {
                "title": "ReservedToolName",
                "description": "A platform-owned Tool name. A customer Tool registration or per-Task Tool Definition that uses one is rejected.",
            }
        )
        return json_schema
