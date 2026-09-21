"""Generated SDK models serialize omission and null according to their schemas."""

import pytest
from pydantic import ValidationError

from dualeai._wire import dump_wire_model
from dualeai.models.problem_details import ProblemDetails
from dualeai.models.tool import Parameters, Tool

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
