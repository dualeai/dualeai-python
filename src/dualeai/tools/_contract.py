"""Compile one registered-tool callable contract for schema and execution."""

from __future__ import annotations

import dataclasses
import inspect
import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import TypeVar, get_args, get_type_hints

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError, create_model
from pydantic.fields import FieldInfo

from dualeai.models.bridge import BridgeToolUseResponse
from dualeai.models.tool import Parameters as ToolParameters
from dualeai.tools._util import callable_name


def _contains_non_finite_number(value: object, seen: frozenset[int] = frozenset()) -> bool:
    """Return whether a callable default contains NaN or infinity."""
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, Decimal):
        return not value.is_finite()

    value_id = id(value)
    if value_id in seen:
        return False
    next_seen = seen | {value_id}

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python")
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        value = dataclasses.asdict(value)

    if isinstance(value, Mapping):
        nested_values = value.values()
    elif isinstance(value, list | tuple | set | frozenset):
        nested_values = value
    else:
        nested_values = ()
    return any(_contains_non_finite_number(item, next_seen) for item in nested_values)


def _validate_callable_default(
    *,
    tool_name: str,
    parameter_name: str,
    annotation: object,
    default: object,
) -> None:
    """Reject a default that violates its annotation or cannot be represented faithfully."""
    try:
        TypeAdapter(annotation).validate_python(default, strict=True)
    except ValidationError as exc:
        raise TypeError(
            f"Tool parameter {parameter_name!r} on {tool_name} has a default that does not satisfy its annotation"
        ) from exc
    if _contains_non_finite_number(default):
        raise TypeError(f"Tool parameter {parameter_name!r} on {tool_name} has a non-finite default")


def _contains_unbound_typevar(annotation: object, seen: frozenset[int] = frozenset()) -> bool:
    """Return whether an annotation contains an unresolved type variable."""
    if isinstance(annotation, TypeVar):
        return True
    annotation_id = id(annotation)
    if annotation_id in seen:
        return False
    next_seen = seen | {annotation_id}
    parameters = getattr(annotation, "__parameters__", ())
    if isinstance(parameters, tuple) and any(isinstance(parameter, TypeVar) for parameter in parameters):
        return True
    return any(_contains_unbound_typevar(argument, next_seen) for argument in get_args(annotation))


def _wire_input_name(parameter_name: str, field: FieldInfo, tool_name: str) -> str:
    """Return the one JSON property name accepted for a callable parameter."""
    validation_alias = field.validation_alias
    if isinstance(validation_alias, str):
        return validation_alias
    if validation_alias is not None:
        raise TypeError(
            f"Tool parameter {parameter_name!r} on {tool_name} uses an unsupported "
            "multi-path validation alias; use one string alias."
        )
    if isinstance(field.alias, str):
        return field.alias
    return parameter_name


@dataclass(frozen=True, slots=True)
class _CompiledToolCallable:
    """SDK-local schema and executable validator compiled from one signature."""

    parameters: ToolParameters
    _parameters_model: type[BaseModel]
    _parameter_names: tuple[str, ...]
    _aliases: Mapping[str, str]
    # Derived once at compile time — the accepted wire names never change, so
    # rebuilding this frozenset on every tool_kwargs call is wasted work.
    _accepted_names: frozenset[str] = dataclasses.field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_accepted_names", frozenset(self._aliases.values()))

    def tool_kwargs(self, tool_use: BridgeToolUseResponse) -> dict[str, object]:
        """Validate one wire tool input and retain typed Python values."""
        unexpected_names = sorted(name for name in tool_use.input if name not in self._accepted_names)
        if unexpected_names:
            raise ValueError(f"Unexpected tool input parameter(s): {', '.join(unexpected_names)}")
        try:
            payload = json.dumps(tool_use.input, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            validated = self._parameters_model.model_validate_json(payload, strict=True)
        except ValidationError as exc:
            # Top-level extra_forbidden cannot fire here: the pre-scan above
            # already rejected every key outside the accepted alias set.
            errors = exc.errors(include_url=False)
            first_error = errors[0] if errors else {}
            location = first_error.get("loc", ())
            input_name = str(location[0]) if location else "<input>"
            parameter_name = next(
                (name for name, alias in self._aliases.items() if alias == input_name),
                input_name,
            )
            if first_error.get("type") == "missing":
                raise ValueError(f"Missing required tool input parameter: {parameter_name}") from exc
            # Schema-side message only — the rejected value must not cross the
            # wire back into model feedback or durable history.
            first_message = str(first_error.get("msg", "invalid value"))
            raise ValueError(f"Invalid value for tool input parameter {parameter_name!r}: {first_message}") from exc

        supplied_fields = validated.model_fields_set
        return {
            parameter_name: getattr(validated, parameter_name)
            for parameter_name in self._parameter_names
            if parameter_name in supplied_fields
        }


def _compile_tool_callable(func: Callable[..., object]) -> _CompiledToolCallable:
    """Compile the model-facing schema and invocation validator once."""
    signature = inspect.signature(func)
    type_hints = get_type_hints(func, include_extras=True)
    tool_name = callable_name(func)

    fields: dict[str, object] = {}
    for parameter in signature.parameters.values():
        if parameter.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
            raise TypeError(
                f"Tool function {tool_name} cannot use *args or **kwargs — declare explicit "
                "typed parameters, or a single Pydantic model."
            )
        if parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
            raise TypeError(
                f"Tool function {tool_name} cannot use positional-only parameters — remove the "
                "'/' marker; the model passes arguments by name."
            )

        annotation = type_hints.get(parameter.name, parameter.annotation)
        if annotation is inspect.Parameter.empty:
            raise TypeError(f"Tool parameter {parameter.name!r} on {tool_name} needs a type annotation")
        if _contains_unbound_typevar(annotation):
            raise TypeError(f"Tool parameter {parameter.name!r} on {tool_name} cannot use an unbound TypeVar")

        if parameter.default is inspect.Parameter.empty:
            default = ...
        else:
            default = parameter.default
            _validate_callable_default(
                tool_name=tool_name,
                parameter_name=parameter.name,
                annotation=annotation,
                default=default,
            )
        fields[parameter.name] = (annotation, default)

    reserved_names = sorted(name for name in fields if hasattr(BaseModel, name))
    if reserved_names:
        raise TypeError(
            f"Tool {tool_name} parameter(s) {reserved_names} collide with a Pydantic model "
            "attribute (e.g. model_config, model_fields) — rename them."
        )

    parameters_model = create_model(  # ty: ignore[no-matching-overload]
        f"{tool_name}Parameters",
        __config__=ConfigDict(
            extra="forbid",
            protected_namespaces=(),
            validate_by_alias=True,
            validate_by_name=False,
        ),
        **fields,
    )
    dropped = [name for name in fields if name not in parameters_model.model_fields]
    if dropped:
        raise TypeError(
            f"Tool {tool_name} parameter(s) {dropped} were dropped when building the "
            "parameter model (a leading underscore marks a name private to Pydantic) — "
            "rename them without a leading underscore."
        )

    aliases = {name: _wire_input_name(name, field, tool_name) for name, field in parameters_model.model_fields.items()}
    duplicate_aliases = sorted(
        alias for alias in set(aliases.values()) if sum(value == alias for value in aliases.values()) > 1
    )
    if duplicate_aliases:
        raise TypeError(f"Tool {tool_name} parameter aliases collide: {', '.join(duplicate_aliases)}")

    generated_schema = parameters_model.model_json_schema(by_alias=True, mode="validation")
    cleaned = {str(key): value for key, value in generated_schema.items()}
    payload: dict[str, object] = {
        "type": "object",
        "properties": cleaned.get("properties", {}),
        "required": cleaned.get("required", []),
        "additionalProperties": False,
    }
    definitions = cleaned.get("$defs")
    if definitions is not None:
        payload["$defs"] = definitions

    return _CompiledToolCallable(
        parameters=ToolParameters.model_validate(payload),
        _parameters_model=parameters_model,
        _parameter_names=tuple(signature.parameters),
        _aliases=aliases,
    )
