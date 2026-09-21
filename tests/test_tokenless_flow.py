"""Unit tests for task results that omit token counts."""

import asyncio

import pytest
from uuid_utils import uuid7

from dualeai.models.bridge import BridgeContentDeltaResponse
from dualeai.models.llm_result import LLMResult
from dualeai.orchestrator import ask
from dualeai.sdk import DualeAISDK
from tests.mocks.mock_http import MockHTTPTransport


@pytest.mark.unit
class TestTokenlessTaskFlow:
    """Exercise tokenless task flows through the in-memory transport."""

    def create_tokenless_llm_result(self) -> LLMResult:
        """Create an LLM result without token properties."""
        return LLMResult(
            completion="Tokenless test completion",
            validated_data={"test": "data", "confidence": 0.95},
            cache_hit=False,
        )

    async def test_tokenless_task_submission_and_completion(
        self,
        minimal_mock_sdk: DualeAISDK,
        mock_transport: MockHTTPTransport,
    ) -> None:
        """A tokenless result reports cache status through the public response."""
        llm_result = self.create_tokenless_llm_result()
        task_id = str(uuid7())
        mock_transport.inject_event(task_id, mock_transport.create_task_completed_event(llm_result))

        response = await ask(
            action="Test tokenless task",
            streaming=False,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )

        assert await response.cache_hit() is False
        assert len(mock_transport.get_requests_for_task(task_id)) == 1

    async def test_tokenless_streaming_flow(
        self,
        minimal_mock_sdk: DualeAISDK,
        mock_transport: MockHTTPTransport,
    ) -> None:
        """A tokenless stream emits each content update and the final model."""
        task_id = str(uuid7())
        final_result = self.create_tokenless_llm_result()
        mock_transport.inject_events(
            task_id,
            [
                mock_transport.create_content_delta_event("Processing started"),
                mock_transport.create_content_delta_event("Halfway done"),
                mock_transport.create_content_delta_event("Partial completion"),
                mock_transport.create_task_completed_event(final_result),
            ],
        )

        response = await ask(
            action="Test tokenless streaming",
            streaming=True,
            request_id=task_id,
            sdk=minimal_mock_sdk,
        )

        collected_updates = [update async for update in response.stream()]
        assert len(collected_updates) == 3
        started, halfway, partial = collected_updates
        assert isinstance(started, BridgeContentDeltaResponse)
        assert started.delta == "Processing started"
        assert isinstance(halfway, BridgeContentDeltaResponse)
        assert halfway.delta == "Halfway done"
        assert isinstance(partial, BridgeContentDeltaResponse)
        assert partial.delta == "Partial completion"
        assert await response.model() == final_result.validated_data

    async def test_multiple_concurrent_tokenless_tasks(
        self,
        minimal_mock_sdk: DualeAISDK,
        mock_transport: MockHTTPTransport,
    ) -> None:
        """Concurrent tokenless tasks keep their results separate."""
        results = [
            LLMResult(
                completion=f"Concurrent result {i}",
                cache_hit=False,
            )
            for i in range(3)
        ]
        task_ids = [str(uuid7()) for _ in range(3)]
        for task_id, result in zip(task_ids, results, strict=True):
            mock_transport.inject_event(task_id, mock_transport.create_task_completed_event(result))

        responses = await asyncio.gather(
            *(
                ask(
                    action=f"Concurrent task {index}",
                    request_id=task_id,
                    sdk=minimal_mock_sdk,
                )
                for index, task_id in enumerate(task_ids)
            )
        )
        completions = await asyncio.gather(*(response.model() for response in responses))

        assert completions == ["Concurrent result 0", "Concurrent result 1", "Concurrent result 2"]
