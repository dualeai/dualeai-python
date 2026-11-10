"""Structural protocol for SDK HTTP and SSE communication.

Provides a dependency injection boundary.

Protocol:
- HTTPTransportProtocol: protected Bridge and Library HTTP transport

HTTPTransport is the production implementation.
"""

from collections.abc import AsyncIterator, Callable
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
    """Protocol implemented by every HTTP/SSE transport.

    Abstracts protected Bridge and Library operations for dependency injection.
    HTTPTransport is the production implementation.

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

        The receipt confirms that the caller may stop the Task. It proves
        neither that the Task exists nor that it stopped: a permitted stop of a
        Task that already ended, or that the Platform does not know, is also
        accepted. Nothing in this SDK enforces this Platform behavior.
        """
        ...

    def run_task(
        self,
        request: BridgeTaskRequest,
        *,
        accepted_callback: Callable[[], None] | None = None,
    ) -> AsyncIterator["BridgeSSEEvent"]:
        """Submit a root create or child continuation and stream that task.

        Encrypted POST /v1/hpke/tasks with the typed body, whose discriminator
        selects creation or continuation; the Platform accepts it with an empty
        202. The stream is then read with encrypted GET
        /v1/hpke/tasks/{task_id}, from the Task's first event; reconnects use
        the same GET with the last event ID.

        Args:
            request: Fully validated bridge request body. Its ``task_id`` is
                the client-owned Task ID: a caller selects it with the
                ``task_id`` argument of ``ask``, ``submit_task`` or
                ``AgentResponse.next``; otherwise the SDK generates UUID4.
            accepted_callback: Called once when the Platform accepts the
                request with its 202, before the stream is read; never called
                for a refused request.

        Yields:
            BridgeSSEEvent objects from SSE stream.

        Raises:
            HTTPTransportAuthError: On authentication failures (401/403).
            HTTPTransportError: On HTTP errors.
        """
        ...

    def submit_tool_results(
        self,
        request: "BridgeToolResultsRequest",
        *,
        last_event_id: str | None = None,
    ) -> AsyncIterator["BridgeSSEEvent"]:
        """Submit tool results and resume the same public task stream.

        Encrypted POST /v1/hpke/tasks with type=tool_results; the body's
        ``task_id`` names the Task that emitted the ``tool.use`` events. After
        the 202, encrypted GET /v1/hpke/tasks/{task_id} reads that Task's
        stream after ``last_event_id``, the opaque SSE cursor of the triggering
        ``tool.use``.
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

        ``POST /v1/hpke/tenants/{tenant_id}/document-uploads`` runs inside
        the HPKE tunnel authenticated by the issued platform token. The body
        contains only ``size_bytes``.

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

        ``POST /v1/hpke/tenants/{tenant_id}/{library_id}/documents`` runs
        inside the HPKE tunnel authenticated by the issued platform token. The body
        binds a temporary upload session to a stable Library id.

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
