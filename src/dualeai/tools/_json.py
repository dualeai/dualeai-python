"""Shared strict JSON adapters."""

from __future__ import annotations

from pydantic import TypeAdapter

from dualeai.models.json_value import JsonValue

json_schema_adapter: TypeAdapter[dict[str, JsonValue | None]] = TypeAdapter(dict[str, JsonValue | None])
