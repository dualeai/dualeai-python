"""Structural protocol for SDK HTTP and SSE communication.

Provides a dependency injection boundary for testing.

Protocol:
- HTTPTransportProtocol: protected Bridge and Library HTTP transport

Production uses HTTPTransport, tests use MockHTTPTransport.

Protocol conformance is exercised through the production and mock transports in
``tests/test_http_transport_v3.py`` and ``tests/mocks/mock_http.py``.
"""

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Protocol, TypeAlias

from dualeai.models.bridge import BridgeTaskContinueRequest, BridgeTaskCreateRequest

BridgeTaskRequest: TypeAlias = BridgeTaskCreateRequest | BridgeTaskContinueRequest

if TYPE_CHECKING:
    from dualeai.models.bridge import (
        AgentDeregistrationMessage,
        AgentHeartbeatMessage,
        AgentHeartbeatResponse,
        AgentRegistrationMessage,
        BridgeSSEEvent,
        BridgeToolResultsRequest,
    )
    from dualeai.models.library import (
        LibraryCreateRequest,
        LibraryDeleteRequest,
        LibraryDocumentCreateOperationRequest,
        LibraryDocumentCreateResponse,
        LibraryDocumentDeleteRequest,
        LibraryDocumentGetRequest,
        LibraryDocumentListRequest,
        LibraryDocumentPage,
        LibraryDocumentUploadRequest,
        LibraryDocumentUploadResponse,
        LibraryGetRequest,
        LibraryListResponse,
        LibraryUpdateRequest,
        LibraryWithRevision,
        PublicIndexedDocument,
    )
    from dualeai.models.task_stop import TaskStopAccepted, TaskStopRequest


class HTTPTransportProtocol(Protocol):
    """Protocol implemented by production and test HTTP/SSE transports.

    Abstracts protected Bridge and Library operations for dependency injection.
    Production uses HTTPTransport, tests use MockHTTPTransport.

    See: https://peps.python.org/pep-0544/
    """

    @property
    def is_connected(self) -> bool:
        """Check if transport session is active."""
        ...

    async def connect(self) -> None:
        """Establish the Bridge and Library protected sessions."""
        ...

    async def disconnect(self) -> None:
        """Close both protected service sessions and release resources."""
        ...

    async def stop_task(self, task_id: str, request: "TaskStopRequest") -> "TaskStopAccepted":
        """Submit a Task stop request and return its acceptance receipt.

        Acceptance does not prove that the target exists, is eligible to stop,
        or will later emit ``task.stopped``.
        """
        ...

    def run_task(
        self,
        task_id: str,
        request: BridgeTaskRequest,
    ) -> AsyncIterator["BridgeSSEEvent"]:
        """Submit a root create or child continuation and stream that task.

        POST /http-bridge/v1/hpke/tasks/{task_id}. The typed body's discriminator selects
        creation or continuation. The transport-level
        retry loop in ``HTTPTransport._stream_request`` switches to GET after
        a protected SSE response starts.

        Args:
            task_id: Client-owned task ID in the URL. Root callers may select
                it; otherwise the SDK generates UUID4. Continuations use a new
                SDK-generated UUID4 child ID.
            request: Fully validated bridge request body.

        Yields:
            BridgeSSEEvent objects from SSE stream.

        Raises:
            HTTPTransportAuthError: On authentication failures (401/403).
            HTTPTransportError: On HTTP errors.
        """
        ...

    def submit_tool_results(
        self,
        task_id: str,
        request: "BridgeToolResultsRequest",
        *,
        last_event_id: str | None = None,
    ) -> AsyncIterator["BridgeSSEEvent"]:
        """Submit tool results and resume the same public task stream.

        POST /http-bridge/v1/hpke/tasks/{task_id} with type=tool_results. The bridge publishes
        an internal tool-result continuation beneath the URL task and returns
        that URL task's existing stream. ``last_event_id`` is the opaque SSE
        cursor for the triggering ``tool.use``.
        """
        ...

    async def register_agent_manifest(self, request: "AgentRegistrationMessage") -> None:
        """Register the SDK tool manifest for the token-bound agent."""
        ...

    async def send_agent_heartbeat(self, request: "AgentHeartbeatMessage") -> "AgentHeartbeatResponse":
        """Send one SDK process heartbeat and return bridge clock diagnostics."""
        ...

    async def deregister_agent_process(self, request: "AgentDeregistrationMessage") -> None:
        """Send a best-effort shutdown signal for one SDK process lease."""
        ...

    async def create_library(self, request: "LibraryCreateRequest") -> "LibraryWithRevision":
        """Create or resolve a general Library."""
        ...

    async def list_libraries(self) -> "LibraryListResponse":
        """List Libraries accessible to the caller."""
        ...

    async def get_library(self, request: "LibraryGetRequest") -> "LibraryWithRevision":
        """Get one Library by stable id."""
        ...

    async def update_library(self, request: "LibraryUpdateRequest") -> "LibraryWithRevision":
        """Update one Library's current revision."""
        ...

    async def delete_library(self, request: "LibraryDeleteRequest") -> None:
        """Send a delete request for one Library."""
        ...

    async def list_library_documents(self, request: "LibraryDocumentListRequest") -> "LibraryDocumentPage":
        """List one bounded page of live documents in one Library."""
        ...

    async def get_library_document(self, request: "LibraryDocumentGetRequest") -> "PublicIndexedDocument":
        """Get one document's current public state."""
        ...

    async def delete_library_document(self, request: "LibraryDocumentDeleteRequest") -> None:
        """Send a delete request for one document."""
        ...

    async def create_document_upload(
        self,
        request: "LibraryDocumentUploadRequest",
    ) -> "LibraryDocumentUploadResponse":
        """Request presigned URLs for document upload.

        ``POST /libraries/v1/hpke/tenants/{tenant_id}/document-uploads`` inside HPKE
        with an encrypted ``Authorization: Bearer <api_token>``. The body contains only
        ``size_bytes``.

        Args:
            request: Upload request body.

        Returns:
            Response with upload identifier and presigned part URLs.

        Raises:
            HTTPTransportAuthError: On authentication failures (401/403).
            HTTPTransportError: On HTTP errors.
        """
        ...

    async def create_library_document(
        self, request: "LibraryDocumentCreateOperationRequest"
    ) -> "LibraryDocumentCreateResponse":
        """Create a queued Library document after all parts uploaded.

        ``POST /libraries/v1/hpke/tenants/{tenant_id}/{library_id}/documents`` inside
        HPKE with an encrypted ``Authorization: Bearer <api_token>``. The body binds
        a temporary upload session to a stable Library id.

        Args:
            request: Operation request with the stable Library identifier and
                canonical document body.

        Returns:
            Queued Library document state and polling location.

        Raises:
            HTTPTransportAuthError: On authentication failures (401/403).
            HTTPTransportError: On HTTP errors.
        """
        ...
