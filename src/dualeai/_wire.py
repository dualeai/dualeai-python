"""Generated-model serialization at SDK wire boundaries."""

from pydantic import BaseModel

from dualeai.models.json_value import JsonValue


def dump_wire_model(model: BaseModel) -> dict[str, JsonValue | None]:
    """Serialize present fields only, preserving explicitly supplied nullable nulls.

    ONE PASS OVER THE PAYLOAD. `mode="json"` already yields JSON-native values,
    and the generator gives every `int` and `float` field the same safe-integer
    bound `JsonValue` carries, so re-validating the dump through
    `tools._json.json_schema_adapter` would walk the whole structure to hand
    back exactly what it was given. `tests/test_wire.py` compares representative
    alias, null, nested-model and JSON-leaf serialization against that adapter;
    it does not cover every generated model or future field.
    """
    # exclude_unset keeps an explicitly-set null on the wire. A null-dropping reader
    # on the other side of a shared content hash (e.g. the registered-tool config
    # hash) agrees with this dump ONLY while no wire-model field is emitted as an
    # explicit null. The generated Tool manifest enforces that (reject-null $defs).
    # Do not emit a new nullable manifest field as an explicit null without a shared
    # exclude policy on both sides of the hash.
    return model.model_dump(mode="json", by_alias=True, exclude_unset=True)
