"""MockHTTPTransport - Mock implementation of HTTPTransportProtocol.

Implements HTTPTransportProtocol for clean dependency injection in tests.

Key features:
1. Implements HTTPTransportProtocol exactly
2. Guaranteed fast cleanup via stop_event
3. Test helpers for event injection and request inspection
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Mapping
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Annotated, TypedDict
from uuid import UUID

from pydantic import Field

from dualeai._wire import dump_wire_model
from dualeai.events.http_transport import HTTPTransportError
from dualeai.events.transport import BridgeTaskRequest, HTTPTransportProtocol
from dualeai.models.bridge import (
    AgentDeregistrationMessage,
    AgentHeartbeatMessage,
    AgentHeartbeatResponse,
    AgentRegistrationMessage,
    BridgeContentDeltaResponse,
    BridgeSSEEvent,
    BridgeTaskCompletedResponse,
    BridgeTaskErrorResponse,
    BridgeToolResultError,
    BridgeToolResultsRequest,
    BridgeToolResultSuccess,
    BridgeToolUseResponse,
)
from dualeai.models.json_value import JsonValue
from dualeai.models.library import (
    LibraryCreateRequest,
    LibraryDeleteRequest,
    LibraryDocumentCreateOperationRequest,
    LibraryDocumentCreateResponse,
    LibraryDocumentDeleteRequest,
    LibraryDocumentGetRequest,
    LibraryDocumentListRequest,
    LibraryDocumentPage,
    LibraryDocumentUploadPart,
    LibraryDocumentUploadRequest,
    LibraryDocumentUploadResponse,
    LibraryGetRequest,
    LibraryListResponse,
    LibraryResponseDocumentStatus,
    LibraryUpdateRequest,
    LibraryWithRevision,
    PublicIndexedDocument,
)
from dualeai.models.llm_result import LLMResult
from dualeai.models.problem_details import ProblemDetails
from dualeai.models.task_stop import TaskStopAccepted, TaskStopRequest

_MOCK_LIBRARY_ID = "018f35a8-2e90-7f2c-a6bb-9426f9c1f401"
_MOCK_UPLOAD_ID = "018f35a8-2e90-7f2c-a6bb-9426f9c1f402"
_MOCK_DOCUMENT_ID = "018f35a8-2e90-7f2c-a6bb-9426f9c1f403"
_MOCK_TENANT_ID = "tenant-default-001"

if TYPE_CHECKING:
    from dualeai.sdk import DualeAISDK

# Union type for tool results
ToolResult = Annotated[
    BridgeToolResultSuccess | BridgeToolResultError,
    Field(discriminator="type"),
]


class RequestRecord(TypedDict, total=False):
    """Recorded HTTP request made through the mock transport."""

    method: str
    path: str
    task_id: str
    body: Mapping[str, object]
    request: object
    query: Mapping[str, object]
    last_event_id: str | None


def require_mock_http_transport(sdk: "DualeAISDK") -> "MockHTTPTransport":
    """Return the SDK test transport with runtime type validation."""
    transport = sdk._transport
    if not isinstance(transport, MockHTTPTransport):
        raise TypeError(f"Expected MockHTTPTransport, got {type(transport).__name__}")
    return transport


class MockHTTPTransport(HTTPTransportProtocol):
    """Mock HTTP transport implementing HTTPTransportProtocol.

    Usage:
    1. Create MockHTTPTransport
    2. Inject into SDK via dependency injection
    3. SDK runs unchanged, all operations go to mock
    4. On cleanup, call disconnect() or set stop_event

    Test helpers:
    - inject_event(): Add event to task queue
    - inject_events(): Add multiple events
    - get_requests(): All HTTP requests made
    - stop_event: Set to make all streams return immediately
    """

    def __init__(self, tenant_id: str = _MOCK_TENANT_ID) -> None:
        self._tenant_id = tenant_id
        self._stop_event = asyncio.Event()
        self._connected = False
        self._requests: list[RequestRecord] = []
        self._event_queues: dict[str, asyncio.Queue[BridgeSSEEvent]] = {}
        self._tool_result_event_queues: dict[str, asyncio.Queue[BridgeSSEEvent]] = {}
        self._event_counter = 0
        self._next_task_error: HTTPTransportError | None = None
        self._next_heartbeat_error: Exception | None = None
        self._next_tool_results_error: Exception | None = None
        now = datetime.now(timezone.utc)
        self._mock_library = LibraryWithRevision(
            id=UUID(_MOCK_LIBRARY_ID),
            path="agent/mock-agent/task/task-1234567890",
            tags={},
            updated_by="agent:mock-agent",
            updated_at=now,
            created_at=now,
            deleted_at=None,
        )
        self._mock_document = PublicIndexedDocument(
            document_id=UUID(_MOCK_DOCUMENT_ID),
            library_id=UUID(_MOCK_LIBRARY_ID),
            filename="mock-document.txt",
            description=None,
            content_type=None,
            size_bytes=1,
            page_count=None,
            created_at=now,
            deleted_at=None,
            status=LibraryResponseDocumentStatus.queued,
            progress_pct=None,
            failure=None,
            tags={},
        )

    @property
    def is_connected(self) -> bool:
        """Check if transport session is active."""
        return self._connected

    @property
    def endpoint(self) -> str:
        """Get mock endpoint."""
        return "http://mock-bridge:8080"

    async def connect(self) -> None:
        """Establish mock HTTP session."""
        self._connected = True

    async def disconnect(self) -> None:
        """Close mock session and signal stop."""
        self._stop_event.set()
        self._connected = False

    async def run_task(
        self,
        task_id: str,
        request: BridgeTaskRequest,
    ) -> AsyncIterator[BridgeSSEEvent]:
        """Record one typed task request and stream events to terminal.

        Iterates the test-injected queue (terminating on
        ``task.completed``, ``task.error``, or ``task.stopped``). Tests that want a
        specific outcome should call ``inject_events()`` before
        invoking the SDK; tests that drive the future manually can
        leave the queue empty — the generator then suspends until
        ``signal_stop`` fires.

        Yields:
            BridgeSSEEvent objects from the injected queue.
        """
        self._requests.append(
            {
                "method": "POST",
                "path": f"/v1/tasks/{task_id}",
                "task_id": task_id,
                "body": dump_wire_model(request),
                "request": request,
            }
        )

        if self._next_task_error is not None:
            error = self._next_task_error
            self._next_task_error = None
            raise error

        if task_id not in self._event_queues:
            self._event_queues[task_id] = asyncio.Queue()

        async for event in self._stream_events(task_id):
            yield event

    async def stop_task(self, task_id: str, request: TaskStopRequest) -> TaskStopAccepted:
        """Record one stop request and accept it."""
        self._requests.append(
            {
                "method": "POST",
                "path": f"/v1/tasks/{task_id}/stop",
                "task_id": task_id,
                "body": dump_wire_model(request),
                "request": request,
            }
        )
        return TaskStopAccepted(task_id=task_id, accepted_at=datetime.now(timezone.utc))

    async def submit_tool_results(
        self,
        task_id: str,
        request: BridgeToolResultsRequest,
        *,
        last_event_id: str | None = None,
    ) -> AsyncIterator[BridgeSSEEvent]:
        """Mock tool-results continuation stream."""
        self._requests.append(
            {
                "method": "POST",
                "path": f"/v1/tasks/{task_id}",
                "task_id": task_id,
                "body": dump_wire_model(request),
                "request": request,
                "last_event_id": last_event_id,
            }
        )
        if self._next_tool_results_error is not None:
            error = self._next_tool_results_error
            self._next_tool_results_error = None
            raise error
        if task_id not in self._tool_result_event_queues:
            yield self.create_task_completed_event()
            return
        async for event in self._stream_events_from_queue(self._tool_result_event_queues[task_id]):
            yield event

    async def register_agent_manifest(self, request: AgentRegistrationMessage) -> None:
        """Mock agent manifest registration."""
        self._requests.append(
            {
                "method": "POST",
                "path": "/v1/agent/registration",
                "body": dump_wire_model(request),
            }
        )

    async def send_agent_heartbeat(self, request: AgentHeartbeatMessage) -> AgentHeartbeatResponse:
        """Mock agent heartbeat with bridge clock diagnostics."""
        now = datetime.now(timezone.utc)
        self._requests.append(
            {
                "method": "POST",
                "path": "/v1/agent/heartbeat",
                "body": dump_wire_model(request),
            }
        )
        if self._next_heartbeat_error is not None:
            error = self._next_heartbeat_error
            self._next_heartbeat_error = None
            raise error
        return AgentHeartbeatResponse(
            server_received_at=now,
            server_sent_at=now,
            client_sent_at=request.time,
        )

    async def deregister_agent_process(self, request: AgentDeregistrationMessage) -> None:
        """Mock agent process deregistration."""
        self._requests.append(
            {
                "method": "POST",
                "path": "/v1/agent/deregistration",
                "body": dump_wire_model(request),
            }
        )

    async def create_library(self, request: LibraryCreateRequest) -> LibraryWithRevision:
        """Mock general Library creation."""
        self._requests.append(
            {
                "method": "POST",
                "path": f"/libraries/v1/tenants/{self._tenant_id}",
                "body": dump_wire_model(request),
                "request": request,
            }
        )
        # The server owns the Library id; the mock keeps its own and only takes the requested path.
        self._mock_library = self._mock_library.model_copy(update={"path": request.path})
        return self._mock_library

    async def list_libraries(self) -> LibraryListResponse:
        """Mock accessible Library listing."""
        self._requests.append(
            {
                "method": "GET",
                "path": f"/libraries/v1/tenants/{self._tenant_id}",
            }
        )
        return LibraryListResponse(libraries=[self._mock_library])

    async def get_library(self, request: LibraryGetRequest) -> LibraryWithRevision:
        """Mock one Library read."""
        self._requests.append(
            {
                "method": "GET",
                "path": f"/libraries/v1/tenants/{self._tenant_id}/{request.library_id}",
                "request": request,
            }
        )
        return self._mock_library

    async def update_library(self, request: LibraryUpdateRequest) -> LibraryWithRevision:
        """Mock one Library revision update."""
        self._requests.append(
            {
                "method": "PATCH",
                "path": f"/libraries/v1/tenants/{self._tenant_id}/{request.library_id}",
                "body": dump_wire_model(request.patch),
                "request": request,
            }
        )
        changes: dict[str, object] = {}
        if request.patch.path is not None:
            changes["path"] = request.patch.path
        if request.patch.tags is not None:
            changes["tags"] = request.patch.tags
        self._mock_library = self._mock_library.model_copy(update=changes)
        return self._mock_library

    async def delete_library(self, request: LibraryDeleteRequest) -> None:
        """Mock one Library delete."""
        self._requests.append(
            {
                "method": "DELETE",
                "path": f"/libraries/v1/tenants/{self._tenant_id}/{request.library_id}",
                "request": request,
            }
        )

    async def list_library_documents(
        self,
        request: LibraryDocumentListRequest,
    ) -> LibraryDocumentPage:
        """Mock one Library document page."""
        self._requests.append(
            {
                "method": "GET",
                "path": (f"/libraries/v1/tenants/{self._tenant_id}/{request.library_id}/documents"),
                "query": {"limit": request.limit, "cursor": request.cursor},
                "request": request,
            }
        )
        return LibraryDocumentPage(documents=[self._mock_document], next_cursor=None)

    async def get_library_document(
        self,
        request: LibraryDocumentGetRequest,
    ) -> PublicIndexedDocument:
        """Mock one document poll."""
        self._requests.append(
            {
                "method": "GET",
                "path": (
                    f"/libraries/v1/tenants/{self._tenant_id}/{request.library_id}/documents/{request.document_id}"
                ),
                "request": request,
            }
        )
        return self._mock_document

    async def delete_library_document(self, request: LibraryDocumentDeleteRequest) -> None:
        """Mock one document delete."""
        self._requests.append(
            {
                "method": "DELETE",
                "path": (
                    f"/libraries/v1/tenants/{self._tenant_id}/{request.library_id}/documents/{request.document_id}"
                ),
                "request": request,
            }
        )

    async def create_document_upload(
        self,
        request: LibraryDocumentUploadRequest,
    ) -> LibraryDocumentUploadResponse:
        """Mock upload session creation with a fake presigned URL."""
        self._requests.append(
            {
                "method": "POST",
                "path": f"/libraries/v1/tenants/{self._tenant_id}/document-uploads",
                "body": dump_wire_model(request),
                "request": request,
            }
        )
        now = datetime.now(timezone.utc)
        return LibraryDocumentUploadResponse(
            upload_id=UUID(_MOCK_UPLOAD_ID),
            parts=[
                LibraryDocumentUploadPart.model_validate(
                    {
                        "part_number": 1,
                        "upload_url": "http://mock-s3:9000/test-bucket/mock-upload?partNumber=1",
                        "offset": 0,
                        "length": request.size_bytes,
                    }
                ),
            ],
            expires_at=now,
            parts_expires_at=now,
        )

    async def create_library_document(
        self,
        request: LibraryDocumentCreateOperationRequest,
    ) -> LibraryDocumentCreateResponse:
        """Mock Library document creation."""
        self._requests.append(
            {
                "method": "POST",
                "path": (f"/libraries/v1/tenants/{self._tenant_id}/{request.library_id}/documents"),
                "body": dump_wire_model(request.document),
                "request": request,
            }
        )
        return LibraryDocumentCreateResponse(
            document_id=UUID(_MOCK_DOCUMENT_ID),
            library_id=request.library_id,
            status="queued",
            location=(f"/v1/tenants/{self._tenant_id}/{request.library_id}/documents/{_MOCK_DOCUMENT_ID}"),
        )

    async def _stream_events_from_queue(
        self,
        queue: asyncio.Queue[BridgeSSEEvent],
    ) -> AsyncIterator[BridgeSSEEvent]:
        """Stream events from a queue with stop signal support.

        CRITICAL: Returns immediately when stop_event is set.
        This prevents test hangs from background tasks waiting forever.
        """
        while not self._stop_event.is_set():
            event_task = asyncio.create_task(queue.get())
            stop_task = asyncio.create_task(self._stop_event.wait())
            done, pending = await asyncio.wait(
                {event_task, stop_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            for task in pending:
                task.cancel()
            for task in pending:
                with contextlib.suppress(asyncio.CancelledError):
                    await task

            if stop_task in done:
                return

            event = event_task.result()
            yield event
            if event.data.type in ("task.completed", "task.error", "task.stopped"):
                return

    async def _stream_events(self, task_id: str) -> AsyncIterator[BridgeSSEEvent]:
        """Stream events from the initial task queue with stop signal support."""
        queue = self._event_queues.get(task_id)
        if queue is None:
            return
        async for event in self._stream_events_from_queue(queue):
            yield event

    # ==========================================================================
    # Test helper methods
    # ==========================================================================

    def inject_event(self, task_id: str, event: BridgeSSEEvent) -> None:
        """Inject single event to be streamed for task.

        Args:
            task_id: Task ID to inject event for.
            event: BridgeSSEEvent to inject.
        """
        if task_id not in self._event_queues:
            self._event_queues[task_id] = asyncio.Queue()
        self._event_queues[task_id].put_nowait(event)

    def inject_events(self, task_id: str, events: list[BridgeSSEEvent]) -> None:
        """Inject multiple events to be streamed for task.

        Events are yielded in order.

        Args:
            task_id: Task ID to inject events for.
            events: List of BridgeSSEEvent objects.
        """
        if task_id not in self._event_queues:
            self._event_queues[task_id] = asyncio.Queue()
        for event in events:
            self._event_queues[task_id].put_nowait(event)

    def inject_tool_result_event(self, task_id: str, event: BridgeSSEEvent) -> None:
        """Inject one event for the tool-results continuation stream."""
        if task_id not in self._tool_result_event_queues:
            self._tool_result_event_queues[task_id] = asyncio.Queue()
        self._tool_result_event_queues[task_id].put_nowait(event)

    def fail_next_heartbeat(self, error: Exception) -> None:
        """Fail the next mock heartbeat after recording the request."""
        self._next_heartbeat_error = error

    def fail_next_task(self, error: HTTPTransportError) -> None:
        """Fail the next task request after recording it."""
        self._next_task_error = error

    def fail_next_tool_results(self, error: Exception) -> None:
        """Fail the next mock tool-results request after recording it."""
        self._next_tool_results_error = error

    def create_content_delta_event(self, delta: str) -> BridgeSSEEvent:
        """Create content delta event.

        Args:
            delta: Content chunk to append.

        Returns:
            BridgeSSEEvent with content delta payload.
        """
        self._event_counter += 1
        return BridgeSSEEvent(
            id=f"{self._event_counter}:1",
            data=BridgeContentDeltaResponse(
                type="content.delta",
                timestamp=datetime.now(timezone.utc),
                delta=delta,
            ),
            timestamp=datetime.now(timezone.utc),
        )

    def create_task_completed_event(self, result: LLMResult | None = None) -> BridgeSSEEvent:
        """Create task completed event.

        Args:
            result: LLMResult with completion data. If None, creates default.

        Returns:
            BridgeSSEEvent with task completed payload.
        """
        self._event_counter += 1
        if result is None:
            result = LLMResult(
                completion="Task completed",
                cache_hit=False,
            )
        return BridgeSSEEvent(
            id=f"{self._event_counter}:1",
            data=BridgeTaskCompletedResponse(
                type="task.completed",
                timestamp=datetime.now(timezone.utc),
                result=result,
            ),
            timestamp=datetime.now(timezone.utc),
        )

    def create_task_error_event(self, message: str, error_code: str = "INTERNAL_ERROR") -> BridgeSSEEvent:
        """Create task error event.

        Args:
            message: Human-readable error message.
            error_code: Machine-readable error code.

        Returns:
            BridgeSSEEvent with task error payload.
        """
        self._event_counter += 1
        return BridgeSSEEvent(
            id=f"{self._event_counter}:1",
            data=BridgeTaskErrorResponse(
                type="task.error",
                timestamp=datetime.now(timezone.utc),
                data=ProblemDetails(
                    title=error_code.replace("_", " ").title(),
                    status=500,
                    detail=message,
                    error_code=error_code,
                ),
            ),
            timestamp=datetime.now(timezone.utc),
        )

    def create_tool_use_event(
        self,
        tool_call_id: str,
        name: str,
        tool_input: dict[str, JsonValue | None],
    ) -> BridgeSSEEvent:
        """Create tool use event.

        Args:
            tool_call_id: Unique ID for this tool call.
            name: Name of the tool to invoke.
            tool_input: Input parameters for the tool.

        Returns:
            BridgeSSEEvent with tool use payload.
        """
        self._event_counter += 1
        return BridgeSSEEvent(
            id=f"{self._event_counter}:1",
            data=BridgeToolUseResponse(
                type="tool.use",
                timestamp=datetime.now(timezone.utc),
                tool_call_id=tool_call_id,
                name=name,
                input=tool_input,
                deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30),
            ),
            timestamp=datetime.now(timezone.utc),
        )

    def get_requests(self) -> list[RequestRecord]:
        """Get all HTTP requests made through transport.

        Returns:
            List of request dicts with method, path, task_id, body.
        """
        return list(self._requests)

    def get_requests_by_method(self, method: str) -> list[RequestRecord]:
        """Get requests filtered by HTTP method.

        Args:
            method: HTTP method (GET, POST).

        Returns:
            Filtered list of request dicts.
        """
        return [r for r in self._requests if r["method"] == method]

    def get_requests_for_task(self, task_id: str) -> list[RequestRecord]:
        """Get requests filtered by task ID.

        Args:
            task_id: Task ID to filter by.

        Returns:
            Filtered list of request dicts.
        """
        return [r for r in self._requests if r.get("task_id") == task_id]

    def clear_requests(self) -> None:
        """Clear recorded requests (for multi-phase tests)."""
        self._requests.clear()

    @property
    def stop_event(self) -> asyncio.Event:
        """Access stop event for test coordination."""
        return self._stop_event

    def signal_stop(self) -> None:
        """Signal all streams to stop immediately."""
        self._stop_event.set()
