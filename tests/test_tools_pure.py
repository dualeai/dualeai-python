"""Focused tests for the SDK's private callable compiler and pure tool helpers."""

import dataclasses
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Annotated, TypeVar
from uuid import UUID

import pytest
from pydantic import BaseModel, Field

from dualeai.models.bridge import BridgeToolUseResponse
from dualeai.models.json_value import JsonValue
from dualeai.tools._contract import (
    _compile_tool_callable,
    _CompiledToolCallable,
    _contains_non_finite_number,
    _validate_callable_default,
)
from dualeai.tools.errors import (
    TOOL_ERROR_MESSAGE_MAX_CHARS,
    apply_tool_error,
    format_registered_tool_error,
    truncate_to_wire_max,
)
from dualeai.tools.serialization import tool_success_output


class AliasedToolResult(BaseModel):
    field_defs: dict[str, int] = Field(alias="$defs")
    optional: str | None = None


_UnboundParameter = TypeVar("_UnboundParameter")


async def _aliased_gate(gate_id: Annotated[str, Field(alias="gateId")]) -> dict[str, str]:
    return {"gate_id": gate_id}


async def _validation_aliased_gate(
    gate_id: Annotated[str, Field(validation_alias="gateId")],
) -> dict[str, str]:
    return {"gate_id": gate_id}


async def _duplicate_aliases(
    gate_id: Annotated[str, Field(alias="target")],
    zone_id: Annotated[str, Field(alias="target")],
) -> dict[str, str]:
    return {"gate_id": gate_id, "zone_id": zone_id}


async def _nested_typevar(values: list[_UnboundParameter]) -> list[str]:  # type: ignore[valid-type]
    return [str(value) for value in values]


async def _model_config_parameter(model_config: str) -> str:
    return model_config


async def _model_fields_parameter(model_fields: str) -> str:
    return model_fields


def _positional_only(value: int, /) -> int:
    return value


def _variadic(*values: int) -> int:
    return sum(values)


def _variadic_keywords(**values: int) -> int:
    return sum(values.values())


def _missing_annotation(value) -> object:  # type: ignore[no-untyped-def]
    return value


def _tool_use(**tool_input: JsonValue) -> BridgeToolUseResponse:
    return BridgeToolUseResponse(
        type="tool.use",
        timestamp=datetime.now(timezone.utc),
        tool_call_id="c",
        name="t",
        input=tool_input,
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=10),
    )


def _contract(func) -> _CompiledToolCallable:  # type: ignore[no-untyped-def]
    return _compile_tool_callable(func)


@pytest.mark.unit
def test_compiled_contract_validates_without_sdk() -> None:
    async def gate(width: int, height: int) -> dict[str, int]:
        return {"width": width, "height": height}

    assert _contract(gate).tool_kwargs(_tool_use(width=1, height=2)) == {"width": 1, "height": 2}


@pytest.mark.unit
def test_compiled_contract_names_missing_required_parameter() -> None:
    async def gate(gate_id: str) -> dict[str, str]:
        return {"gate_id": gate_id}

    with pytest.raises(ValueError, match="Missing required tool input parameter: gate_id"):
        _contract(gate).tool_kwargs(_tool_use())


@pytest.mark.unit
def test_compiled_contract_rejects_unexpected_parameter_and_preserves_callable_default() -> None:
    async def gate(width: int = 3) -> dict[str, int]:
        return {"width": width}

    contract = _contract(gate)
    assert contract.tool_kwargs(_tool_use()) == {}
    with pytest.raises(ValueError, match=r"Unexpected tool input parameter\(s\): extra"):
        contract.tool_kwargs(_tool_use(width=1, extra=2))


@pytest.mark.unit
def test_compiled_contract_rejects_int_for_optional_bool() -> None:
    async def gate(active: bool | None) -> dict[str, object]:
        return {"active": active}

    with pytest.raises(ValueError, match="'active'"):
        _contract(gate).tool_kwargs(_tool_use(active=1))


@pytest.mark.unit
@pytest.mark.parametrize("value", [True, "1", 1.0])
def test_compiled_contract_rejects_non_integer_json_values(value: JsonValue) -> None:
    async def gate(retries: int) -> dict[str, int]:
        return {"retries": retries}

    with pytest.raises(ValueError, match="'retries'"):
        _contract(gate).tool_kwargs(_tool_use(retries=value))


@pytest.mark.unit
def test_compiled_contract_rejects_nested_bool_for_integer() -> None:
    class GateSettings(BaseModel):
        retries: int

    async def gate(settings: GateSettings) -> dict[str, int]:
        return {"retries": settings.retries}

    with pytest.raises(ValueError, match="'settings'"):
        _contract(gate).tool_kwargs(_tool_use(settings={"retries": True}))


@pytest.mark.unit
def test_compiled_contract_keeps_strict_json_native_types() -> None:
    class GateState(str, Enum):
        closed = "closed"

    async def gate(at: datetime, gate_id: UUID, state: GateState) -> dict[str, str]:
        return {"gate_id": str(gate_id), "state": state.value}

    at = datetime(2026, 7, 26, 12, 30, tzinfo=timezone.utc)
    gate_id = UUID("9072e0b5-44aa-4c72-b775-46c50df6aaea")
    kwargs = _contract(gate).tool_kwargs(_tool_use(at=at.isoformat(), gate_id=str(gate_id), state="closed"))

    assert kwargs == {"at": at, "gate_id": gate_id, "state": GateState.closed}


@pytest.mark.unit
def test_format_registered_tool_error_prefixes_and_bounds() -> None:
    assert format_registered_tool_error(ValueError("boom")) == "builtins.ValueError: boom"
    assert format_registered_tool_error(ValueError()) == "builtins.ValueError"
    bounded = format_registered_tool_error(ValueError("x" * (TOOL_ERROR_MESSAGE_MAX_CHARS + 100)))
    assert bounded.endswith("…")
    assert len(bounded) == TOOL_ERROR_MESSAGE_MAX_CHARS


@pytest.mark.unit
def test_apply_tool_error_rejects_non_str_transform() -> None:
    with pytest.raises(TypeError, match="must return str"):
        apply_tool_error(RuntimeError("x"), lambda exc: [str(exc)])  # ty: ignore[invalid-argument-type]


@pytest.mark.unit
def test_tool_success_output_passes_wraps_and_rejects() -> None:
    assert tool_success_output("ok") == "ok"
    assert tool_success_output({"a": 1}) == {"a": 1}
    assert tool_success_output(5) == {"result": 5}
    assert tool_success_output(None) == {"result": None}
    with pytest.raises(TypeError, match="Tool returned set"):
        tool_success_output({1, 2})


@pytest.mark.unit
def test_tool_success_output_preserves_model_aliases_and_absent_fields() -> None:
    assert tool_success_output(AliasedToolResult.model_validate({"$defs": {"answer": 42}})) == {"$defs": {"answer": 42}}


@pytest.mark.unit
def test_compiled_contract_builds_closed_object() -> None:
    async def gate(width: int, height: int = 3) -> dict[str, int]:
        return {"width": width, "height": height}

    params = _contract(gate).parameters
    assert params.type == "object"
    assert params.additional_properties is False
    assert params.required == ["width"]
    assert params.properties["width"]["type"] == "integer"
    assert params.properties["height"]["default"] == 3


@pytest.mark.unit
def test_annotated_constraints_match_schema_and_input_validation() -> None:
    async def threshold(level: Annotated[int, Field(gt=0, le=10)]) -> int:
        return level

    contract = _contract(threshold)
    params = contract.parameters
    assert params.properties["level"]["exclusiveMinimum"] == 0
    assert params.properties["level"]["maximum"] == 10
    assert contract.tool_kwargs(_tool_use(level=1)) == {"level": 1}
    assert contract.tool_kwargs(_tool_use(level=10)) == {"level": 10}
    with pytest.raises(ValueError, match="'level'"):
        contract.tool_kwargs(_tool_use(level=0))
    with pytest.raises(ValueError, match="'level'"):
        contract.tool_kwargs(_tool_use(level=11))


@pytest.mark.unit
@pytest.mark.parametrize("func", [_aliased_gate, _validation_aliased_gate])
def test_tool_alias_is_the_only_advertised_and_accepted_wire_name(func) -> None:  # type: ignore[no-untyped-def]
    contract = _contract(func)
    params = contract.parameters
    assert set(params.properties) == {"gateId"}
    assert params.required == ["gateId"]
    assert contract.tool_kwargs(_tool_use(gateId="north")) == {"gate_id": "north"}
    with pytest.raises(ValueError, match=r"Unexpected tool input parameter\(s\): gate_id"):
        contract.tool_kwargs(_tool_use(gate_id="north"))


@pytest.mark.unit
def test_compiled_contract_rejects_duplicate_wire_aliases() -> None:
    with pytest.raises(TypeError, match="parameter aliases collide: target"):
        _contract(_duplicate_aliases)


@pytest.mark.unit
def test_compiled_contract_rejects_nested_unbound_typevar() -> None:
    with pytest.raises(TypeError, match="unbound TypeVar"):
        _contract(_nested_typevar)


@pytest.mark.unit
@pytest.mark.parametrize("func", [_model_config_parameter, _model_fields_parameter])
def test_compiled_contract_rejects_pydantic_model_attribute(func) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(TypeError, match="collide with a Pydantic model attribute"):
        _contract(func)


@pytest.mark.unit
def test_compiled_contract_rejects_leading_underscore_parameter() -> None:
    # Pydantic drops a leading-underscore field silently; the compiler must
    # surface that as a rename error, not a Pydantic-attribute collision.
    async def gate(_hidden: int) -> int:
        return _hidden

    with pytest.raises(TypeError, match="leading underscore"):
        _contract(gate)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("func", "message"),
    [
        pytest.param(_positional_only, "positional-only", id="positional-only"),
        pytest.param(_variadic, r"\*args or \*\*kwargs", id="varargs"),
        pytest.param(_variadic_keywords, r"\*args or \*\*kwargs", id="kwargs"),
        pytest.param(_missing_annotation, "needs a type annotation", id="missing-annotation"),
    ],
)
def test_compiled_contract_rejects_unsupported_signatures(func, message: str) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(TypeError, match=message):
        _contract(func)


@pytest.mark.unit
@pytest.mark.parametrize(
    "default",
    [
        pytest.param("not-an-int", id="wrong-type"),
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="infinity"),
    ],
)
def test_compiled_contract_rejects_invalid_defaults(default: object) -> None:
    async def gate(retries: int = default) -> dict[str, object]:  # ty: ignore[invalid-parameter-default]
        return {"retries": retries}

    with pytest.raises(TypeError, match="default"):
        _contract(gate)


@pytest.mark.unit
def test_compiled_contract_error_omits_rejected_value() -> None:
    async def gate(retries: int) -> dict[str, int]:
        return {"retries": retries}

    with pytest.raises(ValueError) as exc_info:
        _contract(gate).tool_kwargs(_tool_use(retries="SENTINEL-SECRET-VALUE"))

    message = str(exc_info.value)
    assert "SENTINEL-SECRET-VALUE" not in message
    assert "'retries'" in message


@pytest.mark.unit
def test_truncate_to_wire_max() -> None:
    oversized = "x" * (TOOL_ERROR_MESSAGE_MAX_CHARS + 50)
    assert len(truncate_to_wire_max(oversized)) == TOOL_ERROR_MESSAGE_MAX_CHARS
    assert truncate_to_wire_max(oversized).endswith("…")
    assert truncate_to_wire_max("short") == "short"


# ============================================================================
# Non-JSON-default security cases
# ============================================================================
#
# `_contains_non_finite_number` keeps callable defaults inside the JSON wire
# domain. The published SDK has no private Duale AI dependencies, so this
# validation remains local and the adversarial cases below pin its behavior.


class _NonFiniteModel(BaseModel):
    """A model carrying a float field, so a non-finite value hides one level down."""

    ratio: float


@dataclasses.dataclass(frozen=True)
class _NonFiniteDataclass:
    """A dataclass carrying a float field, exercised via dataclasses.asdict."""

    ratio: float


def _non_finite_adversarial_cases() -> list[tuple[str, object, bool]]:
    """Return adversarial ``(label, value, expected)`` cases for JSON defaults.

    ``expected is True`` means the value contains a NaN or Infinity somewhere
    that the tool-default check must reject.
    """
    cyclic: list[object] = [1.0]
    cyclic.append(cyclic)  # all-finite cycle: guard must terminate and return False
    return [
        ("finite_float", 1.0, False),
        ("nan", math.nan, True),
        ("inf", math.inf, True),
        ("neg_inf", -math.inf, True),
        ("decimal_finite", Decimal("1.5"), False),
        ("decimal_nan", Decimal("NaN"), True),
        ("decimal_inf", Decimal("Infinity"), True),
        ("int", 5, False),
        ("none", None, False),
        ("string_nan_literal", "nan", False),
        ("clean_nested", {"a": [1, 2], "b": {"c": 3.0}}, False),
        ("nested_list_inf", [1, 2, math.inf], True),
        ("nested_dict_inf", {"a": {"b": math.inf}}, True),
        ("tuple_inf", (1, math.inf), True),
        ("set_inf", {1.0, math.inf}, True),
        ("model_finite", _NonFiniteModel(ratio=1.0), False),
        ("model_inf", _NonFiniteModel(ratio=math.inf), True),
        ("dataclass_nan", _NonFiniteDataclass(ratio=math.nan), True),
        ("all_finite_cycle", cyclic, False),
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "expected"),
    [(value, expected) for _label, value, expected in _non_finite_adversarial_cases()],
    ids=[label for label, _value, _expected in _non_finite_adversarial_cases()],
)
def test_contains_non_finite_number_equivalence(value: object, expected: bool) -> None:
    """Pin the shared non-finite predicate; twin test guards the platform copy."""
    assert _contains_non_finite_number(value) is expected


@pytest.mark.unit
def test_validate_callable_default_rejects_non_finite() -> None:
    """A non-finite default is rejected; a finite one passes (shared contract)."""
    with pytest.raises(TypeError, match="non-finite"):
        _validate_callable_default(
            tool_name="tool",
            parameter_name="p",
            annotation=float,
            default=math.inf,
        )
    # A finite default that satisfies its annotation must not raise.
    _validate_callable_default(
        tool_name="tool",
        parameter_name="p",
        annotation=float,
        default=1.0,
    )
