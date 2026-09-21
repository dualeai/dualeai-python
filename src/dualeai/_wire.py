"""Generated-model serialization at SDK wire boundaries."""

from pydantic import BaseModel

from dualeai.models.json_value import JsonValue
from dualeai.tools._json import json_schema_adapter


def dump_wire_model(model: BaseModel) -> dict[str, JsonValue | None]:
    """Serialize present fields only, preserving explicitly supplied nullable nulls."""
    # exclude_unset keeps an explicitly-set null on the wire. A null-dropping reader
    # on the other side of a shared content hash (e.g. the registered-tool config
    # hash) agrees with this dump ONLY while no wire-model field is emitted as an
    # explicit null. The generated Tool manifest enforces that (reject-null $defs).
    # Do not emit a new nullable manifest field as an explicit null without a shared
    # exclude policy on both sides of the hash.
    dumped = model.model_dump(mode="json", by_alias=True, exclude_unset=True)
    return json_schema_adapter.validate_python(dumped)
