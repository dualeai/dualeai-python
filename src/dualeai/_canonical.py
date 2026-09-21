"""Best-effort canonical serialization for the SDK's validated wire JSON.

Standalone helper: the published SDK carries zero private Duale AI dependencies,
so this deliberately reimplements only the required slice of the frozen
recipe (sorted keys, compact separators, ASCII escapes, no non-finite floats,
negative zero collapsed) that validated wire JSON can exercise. Callers must
pre-normalize input to a JsonValue-shaped tree (via ``dump_wire_model``); this
function does not canonicalize models, sets, or non-JSON-native leaf types.
The recipe keeps an identical validated manifest byte-stable across SDK
processes. The resulting ``config_hash`` identifies the manifest carried by
registration and heartbeat messages; it is not a server-side verification
proof.
"""

import json
from collections.abc import Mapping, Sequence


def _normalize_negative_zero(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _normalize_negative_zero(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_normalize_negative_zero(item) for item in value]
    if isinstance(value, float) and value == 0.0:
        return 0.0
    return value


def canonical_json_bytes(value: object) -> bytes:
    """Serialize validated wire JSON to the platform's canonical byte form."""
    return json.dumps(
        _normalize_negative_zero(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
