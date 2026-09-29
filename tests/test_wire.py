"""Generated SDK models serialize omission and null according to their schemas."""

import pytest
from pydantic import BaseModel, ValidationError

from dualeai._canonical import canonical_json_bytes
from dualeai._wire import dump_wire_model
from dualeai.models.bridge import BridgeToolResultsRequest, BridgeToolResultSuccess
from dualeai.models.problem_details import ProblemDetails
from dualeai.models.tool import Parameters, Tool
from dualeai.tools._json import json_schema_adapter

pytestmark = pytest.mark.unit


def test_dump_wire_model_omits_unset_optional_nonnull_fields() -> None:
    parameters = Parameters.model_validate(
        {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }
    )

    assert "$defs" not in dump_wire_model(parameters)


def test_dump_wire_model_preserves_explicit_nullable_null() -> None:
    problem = ProblemDetails(
        type="about:blank",
        title="Example",
        status=400,
        detail="Example failure",
        error_code="EXAMPLE_FAILURE",
        instance=None,
    )

    assert dump_wire_model(problem)["instance"] is None


def test_generated_tool_omits_absent_defs_during_default_serialization() -> None:
    tool = Tool.model_validate(
        {
            "name": "lookup_stock",
            "description": "Look up stock.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        }
    )

    assert "$defs" not in tool.model_dump(mode="json", by_alias=True)["parameters"]


def test_generated_parameters_preserve_empty_defs_and_reject_null() -> None:
    parameters = Parameters.model_validate(
        {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
            "$defs": {},
        }
    )

    assert parameters.model_dump(mode="json", by_alias=True)["$defs"] == {}
    with pytest.raises(ValidationError, match="explicit null is not allowed"):
        Parameters.model_validate(
            {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
                "$defs": None,
            }
        )


def _wire_models() -> list[BaseModel]:
    """One model per shape the dump has to carry through unchanged.

    Nested models, an explicitly supplied null, every JSON leaf type including
    a float and a non-ASCII string, and a bounded integer — the one leaf the
    JSON-shape adapter can still reject.
    """
    return [
        Parameters.model_validate(
            {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
                "additionalProperties": False,
            }
        ),
        Tool.model_validate(
            {
                "name": "lookup_stock",
                "description": "Look up stock.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            }
        ),
        ProblemDetails(
            type="about:blank",
            title="Example",
            status=400,
            detail="Example failure",
            error_code="EXAMPLE_FAILURE",
            instance=None,
        ),
        BridgeToolResultsRequest(
            type="tool_results",
            tool_results=[
                BridgeToolResultSuccess(
                    type="success",
                    tool_call_id="call_0001",
                    output={
                        "text": "naïve — ok",
                        "count": 9007199254740991,
                        "ratio": 0.5,
                        "flag": True,
                        "missing": None,
                        "items": [1, "two", [3.5], {"four": False}],
                    },
                )
            ],
        ),
    ]


def test_the_single_pass_leaves_the_wire_bytes_alone() -> None:
    """The single pass gives byte-for-byte what a second walk would.

    `dump_wire_model` makes one `model_dump` and hands it straight on. The
    result is hashed into the registered-tool `config_hash`
    (`LifecycleManager._manifest_snapshot`), so any value `json_schema_adapter`
    reshaped would move the hash of every agent manifest. The oracle is that
    adapter, run here on the same dump.
    """
    for model in _wire_models():
        dumped = model.model_dump(mode="json", by_alias=True, exclude_unset=True)
        assert canonical_json_bytes(dump_wire_model(model)) == canonical_json_bytes(
            json_schema_adapter.validate_python(dumped)
        )


def test_the_single_pass_still_omits_what_was_never_set() -> None:
    """exclude_unset is load-bearing, not tidiness.

    A null-dropping reader on the other side of the shared content hash agrees
    with this dump only while no wire-model field is emitted as an explicit
    null, which is why the policy survives any change to how the dump is made.
    """
    problem = ProblemDetails(title="Example", status=400, detail="Example failure", error_code="EXAMPLE_FAILURE")

    assert set(dump_wire_model(problem)) == {"title", "status", "detail", "error_code"}
