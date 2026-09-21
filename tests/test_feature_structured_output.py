"""Tests for structured output (res=MyModel) feature.

Right-layer testing: every test exercises the production bridge
round-trip via ``MockHTTPTransport``. The injected SSE event is a
``BridgeTaskCompletedResponse`` wrapping an ``LLMResult``, so the
real ``events_client.run_task`` callback dispatch and
``_extract_result`` validation paths run end-to-end.
"""

import pytest
from pydantic import BaseModel, Field

from dualeai import DualeAISDK
from dualeai.exceptions import ValidationError
from dualeai.models.json_value import JsonValue
from dualeai.orchestrator import ask
from dualeai.response import AgentResponse
from dualeai.sdk import _generate_json_schema_cached
from tests.helpers.lifecycle import inject_llm_completion
from tests.mocks.mock_http import MockHTTPTransport, require_mock_http_transport


# Test Models for Validation
class SimpleModel(BaseModel):
    """Simple test model with basic fields."""

    name: str
    age: int
    active: bool = True


class NestedAddress(BaseModel):
    """Nested address model."""

    street: str
    city: str
    zipcode: str


class ComplexUser(BaseModel):
    """Complex model with nested structures."""

    id: str
    name: str
    email: str
    address: NestedAddress
    tags: list[str] = Field(default_factory=list)


class OptionalFieldsModel(BaseModel):
    """Model with optional fields."""

    required_field: str
    optional_field: str | None = None
    optional_with_default: int = 42


class ListOfModels(BaseModel):
    """Model containing a list of other models."""

    items: list[SimpleModel]
    count: int


class StrictTypesModel(BaseModel):
    """Model with strict type validation."""

    integer_field: int
    float_field: float
    boolean_field: bool
    string_field: str


@pytest.mark.unit
class TestUnitStructuredOutput:
    """Test structured output validation with REAL SDK (minimal mocking)."""

    async def test_ask_with_pydantic_model_type(self, minimal_mock_sdk: DualeAISDK) -> None:
        """Test ask() with res=PydanticModel sets expected_type."""
        response = await ask(
            action="Extract user data",
            res=SimpleModel,
            sdk=minimal_mock_sdk,
        )

        # Verify expected_type is set correctly
        assert isinstance(response, AgentResponse)
        assert response.expected_type is SimpleModel
        assert response.task_id is not None  # Real UUID from SDK

        # Verify the spawned task is tracked in the inflight dict
        assert response.task_id in minimal_mock_sdk._inflight_tasks

        # Verify HTTP request was made (network boundary)
        assert len(require_mock_http_transport(minimal_mock_sdk).get_requests()) > 0

    async def test_nested_validation_error(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """Missing required nested fields raise ValidationError."""
        invalid_data: dict[str, JsonValue] = {
            "id": "user-123",
            "name": "Dave",
            "email": "dave@example.com",
            "address": {"city": "Seattle"},  # Missing street and zipcode
            "tags": [],
        }
        task_id = "test-nested-error"
        inject_llm_completion(mock_transport, task_id, validated_data=invalid_data)

        response = await ask(
            action="Get user",
            res=ComplexUser,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )

        with pytest.raises(ValidationError):
            await response.model()

    async def test_list_of_models_validation(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """Lists of Pydantic models validate per-element."""
        valid_data: dict[str, JsonValue] = {
            "items": [
                {"name": "Alice", "age": 25, "active": True},
                {"name": "Bob", "age": 30, "active": False},
                {"name": "Charlie", "age": 35},
            ],
            "count": 3,
        }
        task_id = "test-list-models"
        inject_llm_completion(mock_transport, task_id, validated_data=valid_data)

        response = await ask(
            action="Get users list",
            res=ListOfModels,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )
        result = await response.model()

        assert isinstance(result, ListOfModels)
        assert len(result.items) == 3
        assert all(isinstance(item, SimpleModel) for item in result.items)
        assert result.items[0].name == "Alice"
        assert result.items[1].age == 30
        assert result.items[2].active is True
        assert result.count == 3

    async def test_optional_fields_in_model(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """Optional fields default when omitted."""
        minimal_data = {"required_field": "test"}
        task_id = "test-optional-default"
        inject_llm_completion(mock_transport, task_id, validated_data=minimal_data)

        response = await ask(
            action="Get data",
            res=OptionalFieldsModel,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )
        result = await response.model()

        assert isinstance(result, OptionalFieldsModel)
        assert result.required_field == "test"
        assert result.optional_field is None
        assert result.optional_with_default == 42

    async def test_optional_fields_with_values(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """Provided optional values override defaults."""
        full_data = {
            "required_field": "test",
            "optional_field": "extra",
            "optional_with_default": 100,
        }
        task_id = "test-optional-with-values"
        inject_llm_completion(mock_transport, task_id, validated_data=full_data)

        response = await ask(
            action="Get data",
            res=OptionalFieldsModel,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )
        result = await response.model()

        assert result.required_field == "test"
        assert result.optional_field == "extra"
        assert result.optional_with_default == 100

    def test_json_schema_generation_for_model(self) -> None:
        """Test SDK generates JSON schema from Pydantic model."""
        schema = _generate_json_schema_cached(SimpleModel)

        # Hand-written expected schema (not derived by calling
        # model_json_schema() here) so a change to the SDK's Pydantic branch
        # in _generate_json_schema_cached (e.g. stripping $defs, forcing
        # additionalProperties: false) is caught instead of trivially
        # matching by construction.
        assert schema == {
            "title": "SimpleModel",
            "description": "Simple test model with basic fields.",
            "type": "object",
            "properties": {
                "name": {"title": "Name", "type": "string"},
                "age": {"title": "Age", "type": "integer"},
                "active": {"title": "Active", "type": "boolean", "default": True},
            },
            "required": ["name", "age"],
        }

    def test_json_schema_for_nested_model(self) -> None:
        """Test JSON schema generation for nested models."""
        schema = _generate_json_schema_cached(ComplexUser)

        # Hand-written expected schema — pins Pydantic's standard $ref/$defs
        # nested-model shape so a change to the SDK's Pydantic branch is caught,
        # not just re-derived from calling model_json_schema() in the test.
        assert schema == {
            "title": "ComplexUser",
            "description": "Complex model with nested structures.",
            "type": "object",
            "$defs": {
                "NestedAddress": {
                    "title": "NestedAddress",
                    "description": "Nested address model.",
                    "type": "object",
                    "properties": {
                        "street": {"title": "Street", "type": "string"},
                        "city": {"title": "City", "type": "string"},
                        "zipcode": {"title": "Zipcode", "type": "string"},
                    },
                    "required": ["street", "city", "zipcode"],
                }
            },
            "properties": {
                "id": {"title": "Id", "type": "string"},
                "name": {"title": "Name", "type": "string"},
                "email": {"title": "Email", "type": "string"},
                "address": {"$ref": "#/$defs/NestedAddress"},
                "tags": {"title": "Tags", "type": "array", "items": {"type": "string"}},
            },
            "required": ["id", "name", "email", "address"],
        }

    async def test_strict_type_validation(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """Strict basic types validate without coercion."""
        valid_data = {
            "integer_field": 10,
            "float_field": 3.14,
            "boolean_field": True,
            "string_field": "test",
        }
        task_id = "test-strict-types-valid"
        inject_llm_completion(mock_transport, task_id, validated_data=valid_data)

        response = await ask(
            action="Get strict data",
            res=StrictTypesModel,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )
        result = await response.model()

        assert isinstance(result, StrictTypesModel)
        assert result.integer_field == 10
        assert result.float_field == 3.14
        assert result.boolean_field is True
        assert result.string_field == "test"

    async def test_type_coercion_failure(self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport) -> None:
        """Non-coercible inputs raise ValidationError."""
        invalid_data = {
            "integer_field": "not a number",
            "float_field": "not a float",
            "boolean_field": "not a bool",
            "string_field": 123,
        }
        task_id = "test-type-coercion-fail"
        inject_llm_completion(mock_transport, task_id, validated_data=invalid_data)

        response = await ask(
            action="Get data",
            res=StrictTypesModel,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )

        with pytest.raises(ValidationError):
            await response.model()

    async def test_missing_required_fields_error(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """Missing required fields raise ValidationError with informative message."""
        incomplete_data = {"name": "George"}
        task_id = "test-missing-required"
        inject_llm_completion(mock_transport, task_id, validated_data=incomplete_data)

        response = await ask(
            action="Get user",
            res=SimpleModel,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )

        with pytest.raises(ValidationError) as exc_info:
            await response.model()

        # Structured context, not substring fishing: the error must name the
        # target model so a caller can tell WHICH validation failed.
        error = exc_info.value
        assert error.context is not None
        assert error.context.expected_type == "SimpleModel"

    async def test_extra_fields_ignored_by_default(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """Extra fields are dropped (Pydantic default ``extra='ignore'``)."""
        data_with_extras = {
            "name": "Henry",
            "age": 40,
            "active": True,
            "extra_field": "ignored",
        }
        task_id = "test-extra-fields"
        inject_llm_completion(mock_transport, task_id, validated_data=data_with_extras)

        response = await ask(
            action="Get user",
            res=SimpleModel,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )
        result = await response.model()

        assert isinstance(result, SimpleModel)
        assert result.name == "Henry"
        assert result.age == 40

    async def test_validation_error_includes_context(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """ValidationError carries the expected_type in its context."""
        invalid_data = {"name": 123, "age": "wrong"}
        task_id = "test-error-context"
        inject_llm_completion(mock_transport, task_id, validated_data=invalid_data)

        response = await ask(
            action="Get user",
            res=SimpleModel,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )

        with pytest.raises(ValidationError) as exc_info:
            await response.model()

        error = exc_info.value
        error_str = str(error)

        assert "SimpleModel" in error_str
        assert error.context is not None
        assert error.context.expected_type == "SimpleModel"

    def test_json_schema_cached_for_performance(self) -> None:
        """``_generate_json_schema_cached`` honours the lru_cache."""
        _generate_json_schema_cached.cache_clear()

        schema1 = _generate_json_schema_cached(SimpleModel)
        cache_info1 = _generate_json_schema_cached.cache_info()

        schema2 = _generate_json_schema_cached(SimpleModel)
        cache_info2 = _generate_json_schema_cached.cache_info()

        assert schema1 == schema2
        assert cache_info2.hits > cache_info1.hits

    async def test_model_validation_with_default_values(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """Pydantic defaults fill in for omitted fields."""
        minimal_data = {"name": "Ivan", "age": 45}
        task_id = "test-defaults-applied"
        inject_llm_completion(mock_transport, task_id, validated_data=minimal_data)

        response = await ask(
            action="Get simple data",
            res=SimpleModel,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )
        result = await response.model()

        assert result.name == "Ivan"
        assert result.age == 45
        assert result.active is True
