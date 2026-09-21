"""AgentResponse.model() extraction + validation tests.

Right-layer testing: every test exercises the production bridge
round-trip via ``MockHTTPTransport``. The injected SSE event is the
canonical bridge response shape (``BridgeTaskCompletedResponse``
wrapping an ``LLMResult``); the SDK's real
``events_client.run_task`` callback dispatch and
``AgentResponse._extract_result`` paths run end-to-end.

Tests should never set ``response.task = future`` directly — that
exercises a test-only branch and bypasses the production extraction
path.
"""

import pytest
from pydantic import BaseModel, Field

from dualeai import DualeAISDK
from dualeai.exceptions import ValidationError
from dualeai.models.json_value import JsonValue
from dualeai.models.llm_result import LLMResult
from dualeai.orchestrator import ask
from tests.helpers.lifecycle import inject_llm_completion
from tests.mocks.mock_http import MockHTTPTransport


class UserProfile(BaseModel):
    """Test model for structured output validation."""

    name: str
    age: int = Field(ge=0, le=150)
    email: str


class Product(BaseModel):
    """Test model for complex validation."""

    product_id: str
    price: float = Field(gt=0)
    in_stock: bool


@pytest.mark.unit
class TestUnitModelExtraction:
    """``model()`` against real SDK + real bridge mock at the wire."""

    async def test_model_awaits_and_returns_completion(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """``completion`` is surfaced when ``validated_data`` is None."""
        task_id = "test-completion-only"
        inject_llm_completion(mock_transport, task_id, completion="User profile retrieved")

        response = await ask(action="Get user profile", request_id=task_id, sdk=minimal_mock_sdk)
        assert response.task_id == task_id

        result = await response.model()
        assert result == "User profile retrieved"

    async def test_model_extracts_validated_data_from_llmresult(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """``validated_data`` takes precedence over ``completion``."""
        validated_data = {"name": "Alice", "age": 30, "email": "alice@example.com"}
        task_id = "test-validated-data"
        inject_llm_completion(mock_transport, task_id, validated_data=validated_data)

        response = await ask(action="Extract structured data", request_id=task_id, sdk=minimal_mock_sdk)

        result = await response.model()
        assert result == validated_data

    async def test_model_validates_against_expected_type(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """``expected_type`` triggers Pydantic validation of the dict."""
        validated_data = {"name": "Bob", "age": 25, "email": "bob@example.com"}
        task_id = "test-pydantic-validation"
        inject_llm_completion(mock_transport, task_id, validated_data=validated_data)

        response = await ask(
            action="Get user profile",
            res=UserProfile,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )

        result = await response.model()
        assert isinstance(result, UserProfile)
        assert result.name == "Bob"
        assert result.age == 25
        assert result.email == "bob@example.com"

    async def test_model_raises_on_validation_error(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """Schema mismatch → ValidationError."""
        invalid_data = {"name": "Charlie", "age": 200, "email": "charlie@example.com"}
        task_id = "test-validation-error"
        inject_llm_completion(mock_transport, task_id, validated_data=invalid_data)

        response = await ask(
            action="Get user profile",
            res=UserProfile,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )

        with pytest.raises(ValidationError):
            await response.model()

    async def test_model_validates_json_string(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """JSON string in ``completion`` is parsed and validated."""
        json_string = '{"product_id": "prod-123", "price": 29.99, "in_stock": true}'
        task_id = "test-json-string"
        inject_llm_completion(mock_transport, task_id, completion=json_string)

        response = await ask(
            action="Get product data",
            res=Product,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )

        result = await response.model()
        assert isinstance(result, Product)
        assert result.product_id == "prod-123"
        assert result.price == 29.99
        assert result.in_stock is True

    async def test_model_handles_missing_validated_data_and_completion(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """Both fields None → returns the LLMResult itself (warns)."""
        task_id = "test-empty-llm-result"
        inject_llm_completion(mock_transport, task_id)  # completion=None, validated_data=None

        response = await ask(action="Empty result", request_id=task_id, sdk=minimal_mock_sdk)

        result = await response.model()
        assert isinstance(result, LLMResult)
        # Fallback contract: with nothing extracted, model() returns the raw LLMResult
        # whose completion and validated_data are both None (not a coerced/empty stand-in).
        assert result.completion is None
        assert result.validated_data is None

    async def test_model_with_basic_type_validation_int(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """``res=int`` converts numeric string completion to int."""
        task_id = "test-basic-int"
        inject_llm_completion(mock_transport, task_id, completion="42")

        response = await ask(
            action="Count items",
            res=int,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )

        result = await response.model()
        assert isinstance(result, int)
        assert result == 42

    async def test_model_with_basic_type_validation_float(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """``res=float`` converts numeric string completion to float."""
        task_id = "test-basic-float"
        inject_llm_completion(mock_transport, task_id, completion="3.14159")

        response = await ask(
            action="Calculate average",
            res=float,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )

        result = await response.model()
        assert isinstance(result, float)
        assert abs(result - 3.14159) < 0.0001

    async def test_model_with_basic_type_validation_bool(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """``res=bool`` with dict ``validated_data`` coerces via ``bool(...)``."""
        task_id = "test-basic-bool"
        inject_llm_completion(mock_transport, task_id, validated_data={"value": True})

        response = await ask(
            action="Is available?",
            res=bool,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )

        result = await response.model()
        assert isinstance(result, bool)
        assert result is True

    async def test_model_with_basic_type_validation_str(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """``res=str`` with dict ``validated_data`` stringifies."""
        task_id = "test-basic-str"
        inject_llm_completion(mock_transport, task_id, validated_data={"summary": "Test summary"})

        response = await ask(
            action="Generate summary",
            res=str,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )

        result = await response.model()
        # _validate_basic_type stringifies a dict result via str(result) exactly
        # (response.py: `result = ... str(result)` for expected_type=str).
        assert result == "{'summary': 'Test summary'}"

    async def test_model_without_expected_type_returns_raw(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """No ``res=`` → raw ``validated_data`` returned untouched."""
        validated_data = {"key1": "value1", "key2": 123}
        task_id = "test-no-expected-type"
        inject_llm_completion(mock_transport, task_id, validated_data=validated_data)

        response = await ask(action="Free-form task", request_id=task_id, sdk=minimal_mock_sdk)

        result = await response.model()
        assert result == validated_data

    async def test_model_validates_nested_pydantic_models(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """Nested Pydantic models validate recursively."""

        class Address(BaseModel):
            street: str
            city: str

        class UserWithAddress(BaseModel):
            name: str
            address: Address

        validated_data: dict[str, JsonValue] = {
            "name": "Dave",
            "address": {"street": "123 Main St", "city": "Springfield"},
        }
        task_id = "test-nested-pydantic"
        inject_llm_completion(mock_transport, task_id, validated_data=validated_data)

        response = await ask(
            action="Get user with address",
            res=UserWithAddress,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )

        result = await response.model()
        assert isinstance(result, UserWithAddress)
        assert result.name == "Dave"
        assert isinstance(result.address, Address)
        assert result.address.street == "123 Main St"
        assert result.address.city == "Springfield"

    async def test_model_raises_on_invalid_json_string(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """Pydantic constraint violation in JSON string → ValidationError."""
        invalid_json = '{"product_id": "prod-123", "price": -10, "in_stock": true}'
        task_id = "test-invalid-json"
        inject_llm_completion(mock_transport, task_id, completion=invalid_json)

        response = await ask(
            action="Get product data",
            res=Product,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )

        with pytest.raises(ValidationError):
            await response.model()

    async def test_model_type_mismatch_raises_validation_error(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """Wrong-shape dict → ValidationError."""
        validated_data = {"wrong_field": "value", "another_wrong": 123}
        task_id = "test-type-mismatch"
        inject_llm_completion(mock_transport, task_id, validated_data=validated_data)

        response = await ask(
            action="Get user",
            res=UserProfile,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )

        with pytest.raises(ValidationError):
            await response.model()
