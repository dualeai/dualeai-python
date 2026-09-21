"""Transport Protocol for SDK communication (RFC-051).

Provides clean dependency injection boundary for testing.

Protocol:
- HTTPTransportProtocol: HTTP/SSE bridge transport

Production uses HTTPTransport, tests use MockHTTPTransport.
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
    """Protocol for HTTP/SSE bridge transport (RFC-051).

    Abstracts HTTP bridge operations for clean dependency injection.
    Production uses HTTPTransport, tests use MockHTTPTransport.

    See: https://peps.python.org/pep-0544/
    """

    @property
    def is_connected(self) -> bool:
        """Check if transport session is active."""
        ...

    async def connect(self) -> None:
        """Establish HTTP session to bridge endpoint."""
        ...

    async def disconnect(self) -> None:
        """Close HTTP session and cleanup resources."""
        ...

    async def stop_task(self, task_id: str, request: "TaskStopRequest") -> "TaskStopAccepted":
        """Ask the platform to stop a running task and everything under it.

        POST /v1/tasks/{task_id}/stop. Returns once the platform accepts the
        request; the task ends with `task.stopped` on its event stream.
        """
        ...

    def run_task(
        self,
        task_id: str,
        request: BridgeTaskRequest,
    ) -> AsyncIterator["BridgeSSEEvent"]:
        """Submit a root create or child continuation and stream that task.

        POST /v1/tasks/{task_id}. The typed body's discriminator selects
        creation or continuation. The transport-level
        retry loop in ``HTTPTransport._stream_request`` may
        transparently switch to GET on retry once the bridge has
        accepted the original POST (see that docstring).

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

        POST /v1/tasks/{task_id} with type=tool_results. The bridge publishes
        an internal tool-result continuation beneath the URL task and returns
        that URL task's existing stream. ``last_event_id`` is the exact
        ``NATS-sequence:event-index`` cursor for the triggering ``tool.use``.
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
        """Soft-delete one Library."""
        ...

    async def list_library_documents(self, request: "LibraryDocumentListRequest") -> "LibraryDocumentPage":
        """List one bounded page of live documents in one Library."""
        ...

    async def get_library_document(self, request: "LibraryDocumentGetRequest") -> "PublicIndexedDocument":
        """Get one document's current public state."""
        ...

    async def delete_library_document(self, request: "LibraryDocumentDeleteRequest") -> None:
        """Soft-delete one document."""
        ...

    async def create_document_upload(
        self,
        request: "LibraryDocumentUploadRequest",
    ) -> "LibraryDocumentUploadResponse":
        """Request presigned URLs for document upload (RFC-113).

        ``POST /libraries/v1/tenants/{tenant_id}/document-uploads`` over plain
        HTTPS with ``Authorization: Bearer <api_token>``. Caller identity is
        carried by the bearer token (the server resolves
        ``token:{sha512(api_token)}`` via Profile); the body is just
        ``{size_bytes}``.

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

        ``POST /libraries/v1/tenants/{tenant_id}/{library_id}/documents`` over
        plain HTTPS with ``Authorization: Bearer <api_token>`` (RFC-113,
        §5). The body binds a temporary upload session to a stable Library id.

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
