"""Invariants behind the public hosted-Tool contract.

Publishing a Tool and running it are separate acts: ``serve()`` publishes the
manifest, while a call arrives on the stream of the Task using it. These tests
pin the implementation properties behind that public documentation.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from dualeai import DualeAIConfig, DualeAISDK, tool
from dualeai.models.bridge import (
    BridgeTaskCreateRequest,
    BridgeToolResultsRequest,
    BridgeToolUseResponse,
)
from tests.mocks.mock_http import MockHTTPTransport


def _serving_sdk(transport: MockHTTPTransport) -> DualeAISDK:
    config = DualeAIConfig.model_validate({"token": "dualeai_test_token_12345", "agent_id": "agent-dispatch"})
    return DualeAISDK(config=config, transport=transport, auto_start=False)


@pytest.mark.unit
class TestToolUseCarriesNoTaskIdentity:
    def test_tool_use_event_has_no_task_id_field(self) -> None:
        """The event cannot name its own task, so the holder of the stream owns the call.

        This is the structural reason a `serve()`-only process cannot execute a
        tool: even if it received the event, nothing in the payload would tell it
        which Task to answer.
        """
        assert "task_id" not in BridgeToolUseResponse.model_fields
        assert set(BridgeToolUseResponse.model_fields) >= {"tool_call_id", "name", "input", "deadline_at"}


@pytest.mark.unit
class TestServeOpensNoTaskStream:
    async def test_serve_makes_lifecycle_requests_only(self, mock_http_transport: MockHTTPTransport) -> None:
        """`serve()` publishes and heartbeats; it never opens a task stream.

        Publishing makes a Tool available; it is not the channel that delivers
        a call.
        """
        sdk = _serving_sdk(mock_http_transport)

        @tool(sdk=sdk, description="does nothing", timeout=timedelta(seconds=5))
        async def noop_tool() -> dict[str, str]:
            return {"state": "ok"}

        stop_event = asyncio.Event()
        serve_task = asyncio.create_task(sdk.serve(stop_event=stop_event))
        try:
            # Yield until registration has been recorded, then stop.
            for _ in range(100):
                await asyncio.sleep(0)
                if mock_http_transport.get_requests():
                    break
            stop_event.set()
            await asyncio.wait_for(serve_task, timeout=5)
        finally:
            if not serve_task.done():
                serve_task.cancel()
            await sdk.cleanup()

        recorded = mock_http_transport.get_requests()
        assert recorded, "serve() recorded no request at all"
        task_requests = [r for r in recorded if isinstance(r.get("request"), BridgeTaskCreateRequest)]
        assert task_requests == [], f"serve() opened a task stream: {task_requests}"


@pytest.mark.unit
class TestUnregisteredToolUseIsSilent:
    async def test_unknown_tool_name_posts_no_result(self, mock_http_transport: MockHTTPTransport) -> None:
        """An unknown tool name yields no result, so the platform waits out the deadline.

        Publishing a Tool set from one process does not make another process
        able to answer it. This pins the branch that produces that outcome:
        ``_schedule_tool_use`` drops the event, and nothing is sent.
        """
        sdk = _serving_sdk(mock_http_transport)
        await sdk._ensure_events_client()

        @tool(sdk=sdk, description="the only registered tool", timeout=timedelta(seconds=5))
        async def registered_tool() -> dict[str, str]:
            return {"state": "ok"}

        unknown = BridgeToolUseResponse(
            type="tool.use",
            timestamp=datetime.now(timezone.utc),
            tool_call_id="call-unknown-1",
            name="tool_this_process_never_registered",
            input={},
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=5),
        )
        try:
            sdk._schedule_tool_use("task-unknown", unknown)
            # Give any scheduled submission a chance to run before asserting absence.
            for _ in range(100):
                await asyncio.sleep(0)

            results = [
                r for r in mock_http_transport.get_requests() if isinstance(r.get("request"), BridgeToolResultsRequest)
            ]
            assert results == [], f"an unregistered tool must post nothing, got {results}"
        finally:
            await sdk.cleanup()
