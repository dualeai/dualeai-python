"""Test helpers that exercise the production bridge round-trip.

The right testing layer for ``AgentResponse`` is HTTP-transport-level
mocking via ``MockHTTPTransport``: the SDK runs unchanged, only the
network seam is replaced. These helpers let tests inject the
canonical bridge events for a given task_id so the SDK's real
``events_client.run_task`` callback dispatch and ``_extract_result``
validation paths execute.

Never drive ``response.task`` directly with a Future + raw value:
that would bypass the production extraction path and exercise a
test-only branch instead of the real code.
"""

from collections.abc import Mapping
from datetime import datetime, timezone

from dualeai.models.bridge import BridgeSSEEvent, BridgeTaskCompletedResponse
from dualeai.models.json_value import JsonValue
from dualeai.models.llm_result import LLMResult
from tests.mocks.mock_http import MockHTTPTransport


def inject_llm_completion(
    transport: MockHTTPTransport,
    task_id: str,
    *,
    completion: str | None = None,
    validated_data: Mapping[str, JsonValue] | None = None,
    cache_hit: bool = False,
) -> None:
    """Queue a completion event for a task.

    Call BEFORE invoking the SDK (use ``request_id=task_id`` on
    ``ask()``) so the queued event is ready when the bridge stream
    opens.

    The injected event is the canonical bridge response shape:
    ``BridgeTaskCompletedResponse(result=LLMResult(...))``. Real SDK
    code (``events_client.run_task`` callback dispatch,
    ``AgentResponse._extract_result``) runs against it.
    """
    timestamp = datetime.now(timezone.utc)
    transport.inject_event(
        task_id,
        BridgeSSEEvent(
            id="1:1",
            data=BridgeTaskCompletedResponse(
                type="task.completed",
                timestamp=timestamp,
                result=LLMResult(
                    completion=completion,
                    validated_data=None if validated_data is None else dict(validated_data),
                    cache_hit=cache_hit,
                ),
            ),
            timestamp=timestamp,
        ),
    )
