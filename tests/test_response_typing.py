"""Tests for AgentResponse[T] Generic typing.

Two layers:
1. Static type assertions (``assert_type``) caught by pyright at build time.
2. Runtime validation caught by pytest at test time.

All tests run REAL SDK code via ``minimal_mock_sdk``; only the HTTP
transport is mocked. The injected SSE event is the canonical bridge
shape, so ``AgentResponse._extract_result`` and the generic type
parameter both flow through the production path.
"""

import pytest
from pydantic import BaseModel
from typing_extensions import assert_type

from dualeai.orchestrator import ask
from dualeai.response import AgentResponse
from dualeai.sdk import DualeAISDK
from tests.helpers.lifecycle import inject_llm_completion
from tests.mocks.mock_http import MockHTTPTransport


class Invoice(BaseModel):
    id: str
    amount: float


class Result(BaseModel):
    value: int


class NestedData(BaseModel):
    items: list[Invoice]


@pytest.mark.unit
class TestAgentResponseTyping:
    """``AgentResponse[T]`` generic typing exercised through real ``ask()``."""

    async def test_response_typed_model(self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport) -> None:
        """``AgentResponse[Invoice]`` resolves to ``Invoice`` from ``model()``."""
        task_id = "test-typed-123"
        inject_llm_completion(
            mock_transport,
            task_id,
            validated_data={"id": "inv-001", "amount": 99.99},
        )

        response = await ask(
            action="Get invoice",
            res=Invoice,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )
        assert_type(response, AgentResponse[Invoice])

        result = await response.model()
        assert_type(result, Invoice)
        assert isinstance(result, Invoice)
        assert result.id == "inv-001"
        assert result.amount == 99.99

    async def test_model_validates_nested_pydantic(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """Nested Pydantic models validate end-to-end with the generic."""
        task_id = "test-nested"
        inject_llm_completion(
            mock_transport,
            task_id,
            validated_data={
                "items": [
                    {"id": "inv-001", "amount": 10.0},
                    {"id": "inv-002", "amount": 20.0},
                ]
            },
        )

        response = await ask(
            action="Get nested",
            res=NestedData,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )
        result = await response.model()
        assert_type(result, NestedData)
        assert isinstance(result, NestedData)
        assert len(result.items) == 2
        assert all(isinstance(item, Invoice) for item in result.items)

    async def test_model_validates_basic_types_str(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """``res=str`` exercises basic-type validation."""
        task_id = "test-str"
        inject_llm_completion(mock_transport, task_id, completion="hello world")

        response = await ask(
            action="Get string",
            res=str,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )
        result = await response.model()
        assert_type(result, str)
        assert isinstance(result, str)
        assert result == "hello world"

    async def test_model_validates_basic_types_int(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """``res=int`` exercises basic-type validation."""
        task_id = "test-int"
        inject_llm_completion(mock_transport, task_id, completion="42")

        response = await ask(
            action="Get int",
            res=int,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )
        result = await response.model()
        assert_type(result, int)
        assert isinstance(result, int)
        assert result == 42

    async def test_response_streaming_flag(self, minimal_mock_sdk: DualeAISDK) -> None:
        """``streaming=True`` is preserved on the typed response."""
        response = await ask(
            action="Stream",
            res=Invoice,
            streaming=True,
            sdk=minimal_mock_sdk,
        )
        assert_type(response, AgentResponse[Invoice])
        assert response.streaming is True

    async def test_multiple_generic_types_coexist(
        self, minimal_mock_sdk: DualeAISDK, mock_transport: MockHTTPTransport
    ) -> None:
        """Multiple ``AgentResponse[T]`` instances with different ``T`` coexist."""
        invoice_task_id = "test-invoice-id"
        result_task_id = "test-result-id"
        inject_llm_completion(
            mock_transport,
            invoice_task_id,
            validated_data={"id": "inv-001", "amount": 50.0},
        )
        inject_llm_completion(mock_transport, result_task_id, validated_data={"value": 100})

        response_invoice = await ask(
            action="Get invoice",
            res=Invoice,
            request_id=invoice_task_id,
            sdk=minimal_mock_sdk,
        )
        response_result = await ask(
            action="Get result",
            res=Result,
            request_id=result_task_id,
            sdk=minimal_mock_sdk,
        )

        assert_type(response_invoice, AgentResponse[Invoice])
        assert_type(response_result, AgentResponse[Result])

        invoice = await response_invoice.model()
        result = await response_result.model()

        assert isinstance(invoice, Invoice)
        assert isinstance(result, Result)
        assert invoice.id == "inv-001"
        assert result.value == 100
