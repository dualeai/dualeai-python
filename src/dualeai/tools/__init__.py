"""Tool-result serialization and model-facing error rendering."""

from dualeai.tools.errors import apply_tool_error, format_registered_tool_error, truncate_to_wire_max
from dualeai.tools.serialization import tool_success_output

__all__ = [
    "apply_tool_error",
    "format_registered_tool_error",
    "tool_success_output",
    "truncate_to_wire_max",
]
