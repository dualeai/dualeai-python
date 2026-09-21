"""Auto-generated Pydantic models from the resolved schema compiler graph. Do not edit manually."""

from __future__ import annotations

from re import fullmatch
from typing import Annotated
from uuid import UUID

from pydantic import BeforeValidator, Field
from typing_extensions import TypeAliasType

__all__: list[str] = ["DocumentId"]


def _validate_string_constraints_1(value: object) -> object:
    if not isinstance(value, str):
        return value
    if fullmatch("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", value) is None:
        raise ValueError("string is not a canonical uuid")
    return value


DocumentId = TypeAliasType(
    "DocumentId",
    Annotated[
        Annotated[UUID, BeforeValidator(_validate_string_constraints_1)],
        Field(
            title="DocumentId",
            description="Deterministic document identifier (UUIDv5, RFC 9562 §5.5). Unique within a Library.",
            examples=["376dbee0-d70a-52e4-a801-d9dd72a5a083"],
        ),
    ],
)
