"""Auto-generated Pydantic models from the resolved schema compiler graph. Do not edit manually."""

from __future__ import annotations

from re import fullmatch, search
from typing import Annotated
from uuid import UUID

from pydantic import BeforeValidator, Field
from typing_extensions import TypeAliasType

__all__: list[str] = ["LibraryId"]


def _validate_string_constraints_1(value: object) -> object:
    if not isinstance(value, str):
        return value
    if fullmatch("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", value) is None:
        raise ValueError("string is not a canonical uuid")
    if search("^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", value) is None:
        raise ValueError("string does not match pattern")
    return value


LibraryId = TypeAliasType(
    "LibraryId",
    Annotated[
        Annotated[
            UUID,
            BeforeValidator(_validate_string_constraints_1),
            Field(
                json_schema_extra={"pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"}
            ),
        ],
        Field(
            title="LibraryId",
            description="Library identifier (uuid7, RFC 9562 §5.7). Immutable across renames and revisions.",
            examples=["01935b8a-7fb0-7b6b-93f7-0123456789ab"],
        ),
    ],
)
