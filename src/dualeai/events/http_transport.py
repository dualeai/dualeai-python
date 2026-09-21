"""HTTP/SSE transport for SDK communication (RFC-051, RFC-113).

Uses aiohttp for async HTTP with custom SSE parser.
Implements HTTPTransportProtocol for dependency injection.

Bridge SSE (``/v1/tasks/...``): HPKE E2E encryption (RFC-065) handled by
``HPKEClientSession``. PSK authentication via ``X-HPKE-PSK-ID`` header
(hpke-http ~=1.6).

Library calls (``/libraries/v1/...``): plain HTTPS over the public gateway
with ``Authorization: Bearer <api_token>`` (RFC-113). End-to-end body
encryption for Library is deferred (RFC-113) — the dashboard cannot
exercise HPKE today and S3 cannot terminate it for part uploads. Library and
Bridge use SEPARATE aiohttp sessions so the HPKE middleware never touches
Library bytes.
"""

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Mapping
from http import HTTPStatus
from typing import TypeVar
from urllib.parse import quote, urlsplit

import aiohttp
from hpke_http.middleware.aiohttp import DecryptedResponse, HPKEClientSession
from pydantic import BaseModel, ValidationError
from structlog import get_logger
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from dualeai._wire import dump_wire_model
from dualeai.constants import HTTPDefaults
from dualeai.events.sse_parser import SSEChecksumError, SSEParseError, parse_sse_stream
from dualeai.events.transport import BridgeTaskRequest
from dualeai.exceptions import ConfigurationError
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
from dualeai.models.problem_details import ProblemDetails
from dualeai.models.task_stop import TaskStopAccepted, TaskStopRequest
from dualeai.observability import inject_trace_context

logger = get_logger(__name__)

_LibraryModelT = TypeVar("_LibraryModelT", bound=BaseModel)

_LIBRARY_GATEWAY_PREFIX = "/libraries"


def _endpoint_origin(endpoint: str) -> str:
    """Return ``scheme://host`` of an endpoint, dropping any path prefix.

    aiohttp rejects a path-bearing ``base_url`` without a trailing slash, and
    host-root-absolute request paths (``/libraries/...``) replace the base path
    anyway — so the Library session must bind to the origin, not the full
    (possibly path-prefixed) bridge endpoint.
    """
    parts = urlsplit(endpoint)
    if not parts.scheme or not parts.netloc:
        raise ValueError(f"endpoint must be an absolute http(s) URL with a host, got {endpoint!r}")
    return f"{parts.scheme}://{parts.netloc}"


def _discovery_url(endpoint: str) -> str:
    """Return the HPKE key-discovery URL, preserving any endpoint path prefix.

    Unlike the Library origin, discovery lives under the bridge prefix (e.g.
    ``.../http-bridge/.well-known/hpke-keys``), so the path is kept. Any query or
    fragment is dropped — the discovery path must not be appended after ``?…``/``#…``.
    """
    parts = urlsplit(endpoint)
    if not parts.scheme or not parts.netloc:
        raise ValueError(f"endpoint must be an absolute http(s) URL with a host, got {endpoint!r}")
    base = f"{parts.scheme}://{parts.netloc}{parts.path}".rstrip("/")
    return f"{base}/.well-known/hpke-keys"


def _library_gateway_path(tenant_id: str, *segments: str) -> str:
    """Build a public Library gateway path with service-local tenant routes."""
    escaped_segments = [quote(segment.strip("/"), safe="") for segment in segments]
    suffix = "/".join(["v1", "tenants", quote(tenant_id, safe=""), *escaped_segments])
    return f"{_LIBRARY_GATEWAY_PREFIX}/{suffix}"


def _resolve_retry_method(
    method: str,
    json_data: Mapping[str, object] | None,
    *,
    stream_established: bool,
) -> tuple[str, Mapping[str, object] | None]:
    """Choose POST replay or read-only GET for the next stream attempt.

    After the SDK observes a 2xx, Bridge has published the POST, so later
    attempts use GET. Before an observed 2xx, server receipt is ambiguous and
    the SDK repeats the original method with the same client-selected task ID.
    That repeat may republish, but Router admission converges on one graph task.
    """
    if stream_established and method == "POST":
        return "GET", None
    return method, json_data


class HTTPTransportError(Exception):
    """Base error for HTTP transport operations."""

    def __init__(self, message: str, *, problem_details: ProblemDetails | None = None) -> None:
        super().__init__(message)
        # RFC 9457 body parsed from the bridge error response when it carried
        # one; None for transport/connection failures with no ProblemDetails
        # body. Callers read error_code, ai_hints, and retry hints from here.
        self.problem_details = problem_details


class HTTPTransportConnectionError(HTTPTransportError):
    """Connection to HTTP bridge failed."""


class HTTPTransportResponseError(HTTPTransportError):
    """A non-authentication client request was rejected by the platform."""


class HTTPTransportAuthError(HTTPTransportError):
    """Authentication failed (invalid/expired API key, missing credentials)."""


class HTTPTransportStreamError(HTTPTransportError):
    """Error during SSE streaming."""


async def _read_problem_details(
    response: aiohttp.ClientResponse | DecryptedResponse,
) -> ProblemDetails | None:
    """Parse an RFC 9457 ProblemDetails body, tolerating non-conforming bodies.

    Reads raw bytes so an ``application/problem+json`` content type (RFC 9457)
    is accepted alongside ``application/json``. Any parse or schema mismatch
    yields None — a missing body must never mask the underlying HTTP error.
    """
    try:
        raw = await response.read()
    except Exception:  # noqa: BLE001
        # Best-effort read: a transport failure (aiohttp.ClientError) OR an
        # HPKE decrypt failure (hpke_http DecryptionError, not a ClientError)
        # must yield None, never mask the underlying HTTP status error.
        return None
    try:
        body = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(body, dict):
        return None
    try:
        return ProblemDetails.model_validate(body)
    except ValidationError:
        return None


async def _raise_for_http_status(response: aiohttp.ClientResponse | DecryptedResponse, *, path: str) -> None:
    """Map aiohttp response status errors to SDK transport errors.

    On error the RFC 9457 ProblemDetails body, when present, is parsed and
    attached to the raised error's ``problem_details`` so callers read the
    typed ``error_code`` and retry hints instead of only an HTTP status line.
    """
    if response.status < HTTPStatus.BAD_REQUEST:
        return
    # Read the ProblemDetails body BEFORE raising: some transports (and the
    # aioresponses test mock) close the stream once raise_for_status fires,
    # leaving the body unreadable afterward. On the error path no caller reads
    # the body, so consuming it here is safe.
    problem = await _read_problem_details(response)
    try:
        response.raise_for_status()
    except aiohttp.ClientResponseError as exc:
        if exc.status in (401, 403):
            logger.warning(
                "Authentication failed",
                path=path,
                status=exc.status,
                message=exc.message,
            )
            raise HTTPTransportAuthError(f"HTTP {exc.status}: {exc.message}", problem_details=problem) from exc
        error_type = (
            HTTPTransportResponseError
            if exc.status < HTTPStatus.INTERNAL_SERVER_ERROR
            else HTTPTransportConnectionError
        )
        logger.warning(
            "HTTP request failed",
            path=path,
            status=exc.status,
            message=exc.message,
        )
        raise error_type(f"HTTP {exc.status}: {exc.message}", problem_details=problem) from exc


# Exceptions the SSE stream loop retries transparently (with Last-Event-ID).
RETRYABLE_STREAM_ERRORS: tuple[type[BaseException], ...] = (
    aiohttp.ClientConnectionError,
    aiohttp.ServerTimeoutError,
    # ClientPayloadError covers chunked-transfer cut mid-stream — the dominant
    # SSE failure mode through cloud LBs and proxies. It is a SIBLING of
    # ClientConnectionError under ClientError, not a subclass, so it must be
    # listed explicitly.
    aiohttp.ClientPayloadError,
    # ServerDisconnectedError already inherits from ClientConnectionError;
    # listed explicitly to defend against future aiohttp hierarchy shuffles
    # and to match the existing parity with ServerTimeoutError above.
    aiohttp.ServerDisconnectedError,
    SSEChecksumError,  # Retry on checksum mismatch with Last-Event-ID
)


class HTTPTransport:
    """HTTP/SSE transport using aiohttp (RFC-051, RFC-065, RFC-113).

    Two sessions, two trust profiles:

    | Session              | Used for                       | Auth                          | Encryption |
    |----------------------|--------------------------------|-------------------------------|------------|
    | ``_session``         | Bridge SSE ``/v1/tasks/...``   | PSK via ``X-HPKE-PSK-ID``     | HPKE       |
    | ``_library_session`` | Library ``/libraries/v1/...``  | ``Authorization: Bearer ...`` | TLS only   |

    Features:
    - POST /v1/tasks/{task_id} → SSE stream (run task, type=create)
    - POST /v1/tasks/{child_task_id} → SSE stream (user continuation, type=continue)
    - POST /v1/tasks/{task_id} → SSE stream (tool results, type=tool_results)
    - Auto-reconnect with Last-Event-ID; POST→GET switch on retry
      after the bridge has accepted the original request (see
      _stream_request docstring for the bridge invariant)
    - HPKE E2E encryption (RFC-065) via HPKEClientSession for Bridge only.
      Library transport stays plain HTTPS (RFC-113): end-to-end body
      encryption for upload sessions/document creation is a future RFC; the
      dashboard has no browser HPKE and S3 cannot decrypt HPKE for parts.

    Implements HTTPTransportProtocol.

    Note: zstd Content-Encoding is handled automatically by aiohttp 3.11+.
    Uses compression.zstd (Python 3.14+, PEP 784) or backports.zstd (<3.14).
    See RFC 8878 for zstd content-encoding specification.
    """

    # Retry config — see _stream_request docstring for design
    # rationale. Budget sized so a long-running task can survive
    # several mid-stream disconnects (each closes the SSE TCP
    # connection when an upstream proxy or load balancer reconciles
    # its backend pool). 10 attempts of wait_exponential_jitter(1, 10)
    # give ~65-69 s of cumulative backoff (9 waits, base sum 65 s + jitter),
    # enough to cover the typical backend re-registration window.
    _MAX_RETRIES = 10
    _RETRY_INITIAL_SECONDS = 1.0
    _RETRY_MAX_SECONDS = 10.0

    # Timeout config
    _CONNECT_TIMEOUT_SECONDS = 5.0
    _SOCK_READ_TIMEOUT_SECONDS = 60.0
    """Detect dead connections (relies on server heartbeats)."""
    _LIBRARY_TOTAL_TIMEOUT_SECONDS = 60.0
    """Overall deadline for Library metadata JSON calls. Unlike the SSE bridge
    stream (total=None, unbounded), these are ordinary request/response calls
    that must not hang forever on a dribbling upstream."""

    def __init__(
        self,
        endpoint: str,
        token: str,
        tenant_id: str | None = None,
    ) -> None:
        """Initialize HTTP transport.

        Args:
            endpoint: Bridge API endpoint (e.g., https://api.duale.ai)
            token: API token for authentication (dualeai_xxx)
            tenant_id: Tenant path segment for Library management and uploads.
        """
        self._endpoint = endpoint.rstrip("/")
        self._token = token
        self._tenant_id = tenant_id
        # PSK identity is SHA-512 hash of token (hpke-http v1.3.0)
        # Server resolves this hash to lookup the raw token for HPKE decryption
        self._psk_id = hashlib.sha512(token.encode()).digest()
        self._session: HPKEClientSession | None = None
        # Library calls go through plain HTTPS + Authorization: Bearer
        # (RFC-113). Kept on a SEPARATE aiohttp.ClientSession so the
        # HPKE middleware never touches Library bytes.
        self._library_session: aiohttp.ClientSession | None = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        """Check if HTTP sessions (bridge + library) are active."""
        return self._session is not None and self._library_session is not None and self._connected

    @property
    def endpoint(self) -> str:
        """Get configured endpoint."""
        return self._endpoint

    async def connect(self) -> None:
        """Establish HTTP session to bridge endpoint with HPKE encryption."""
        if self._session is not None:
            return

        timeout = aiohttp.ClientTimeout(
            total=None,  # No limit - SSE can run indefinitely
            connect=self._CONNECT_TIMEOUT_SECONDS,
            sock_read=self._SOCK_READ_TIMEOUT_SECONDS,
        )

        # HPKEClientSession handles E2E encryption transparently (RFC-065)
        # - Auto-fetches platform public keys from discovery endpoint
        # - Encrypts request bodies with HPKE
        # - Uses token as PSK for authenticated encryption
        # - Compresses with zstd when compress=True
        # - psk_id sent via X-HPKE-PSK-ID header (hpke-http v1.3.0)
        #
        # Pure PSK auth: NO Authorization header - avoids MITM token exposure
        # Server resolves psk_id (hash) to raw token via Profile service
        # discovery_url explicit: hpke-http defaults to host-level /.well-known/hpke-keys
        # (per RFC 8615), but the bridge may be behind a path prefix (e.g., /http-bridge).
        discovery_url = _discovery_url(self._endpoint)

        # No session-level Content-Type: the HPKE middleware rewrites it to
        # application/octet-stream for encrypted POST bodies (stashing the
        # original in X-HPKE-Content-Type), and bridge POSTs pass json= so
        # aiohttp sets application/json per-request anyway. A session default
        # only mislabels the bodyless GET replay.
        self._session = HPKEClientSession(
            base_url=self._endpoint,
            psk=self._token.encode(),
            psk_id=self._psk_id,
            discovery_url=discovery_url,
            compress=True,
            timeout=timeout,
        )
        await self._session.__aenter__()

        # Library session: plain HTTPS + Authorization: Bearer (RFC-113).
        # Library does not terminate HPKE; the SDK token rides in a standard
        # bearer header so the public gateway and Library service can resolve
        # ``token:{sha512(api_token)}`` to the functional identity via Profile.
        # Kept on a separate session so the HPKE middleware never wraps
        # Library bytes, and so Library can use its own JSON+Content-Type
        # defaults without bleeding into the Bridge SSE pipeline.
        # Library gateway routes are host-root absolute (``/libraries/v1/...``),
        # NOT under the bridge path prefix. Use the endpoint's ORIGIN as the base
        # URL: the ``/http-bridge`` prefix would be dropped by the absolute path
        # anyway, and aiohttp rejects a path-bearing base_url without a trailing
        # slash ("base_url must have a trailing '/'"). Origin is host-only, so it
        # is always valid and yields the correct ``https://host/libraries/...``.
        library_base_url = _endpoint_origin(self._endpoint)
        # Library calls are ordinary JSON request/response, not an SSE stream:
        # give them a bounded total timeout instead of reusing the bridge's
        # total=None (which would let a slow-loris upstream hang forever).
        library_timeout = aiohttp.ClientTimeout(
            total=self._LIBRARY_TOTAL_TIMEOUT_SECONDS,
            connect=self._CONNECT_TIMEOUT_SECONDS,
            sock_read=self._SOCK_READ_TIMEOUT_SECONDS,
        )
        self._library_session = aiohttp.ClientSession(
            base_url=library_base_url,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._token}",
            },
            timeout=library_timeout,
        )

        self._connected = True

        logger.debug(
            "HTTP transport connected (HPKE for bridge, plain HTTPS+Bearer for library)",
            endpoint=self._endpoint,
        )

    async def disconnect(self) -> None:
        """Close HTTP sessions (bridge HPKE + library plain HTTPS) and cleanup."""
        if self._session is None and self._library_session is None:
            return
        self._connected = False
        if self._library_session is not None:
            await self._library_session.close()
            self._library_session = None
        if self._session is not None:
            await self._session.__aexit__(None, None, None)
            self._session = None
        logger.debug("HTTP transport disconnected")

    async def run_task(
        self,
        task_id: str,
        request: BridgeTaskRequest,
    ) -> AsyncIterator[BridgeSSEEvent]:
        """Submit a validated create or continuation request.

        POST /v1/tasks/{task_id}. The body discriminator selects a root create
        or child continuation. After the SDK observes a successful POST, the
        retry loop uses GET to resume the same task stream.

        Args:
            task_id: Client-owned task ID in the URL. The SDK generates a UUID4
                when the caller does not select a root ID; continuations always
                use a new SDK-generated UUID4 child ID.
            request: Fully validated body, including its discriminator.

        Yields:
            BridgeSSEEvent objects from SSE stream.

        Raises:
            HTTPTransportAuthError: On authentication failures (401/403).
            HTTPTransportError: On connection or stream errors.
        """
        async for event in self._stream_request(
            method="POST",
            path=f"/v1/tasks/{task_id}",
            # by_alias=True to match every other bridge write path: the embedded
            # tool schema (Tool.parameters) is alias-only (additionalProperties),
            # so an unaliased dump emits additional_properties and a bridge
            # Parameters(extra=forbid) rejects the create.
            json_data=dump_wire_model(request),
            task_id=task_id,
        ):
            yield event

    async def stop_task(self, task_id: str, request: TaskStopRequest) -> TaskStopAccepted:
        """Ask the platform to stop a running task.

        POST /v1/tasks/{task_id}/stop. The call returns as soon as the platform
        accepts the request; the task ends with `task.stopped` on its event
        stream, carrying the reason supplied here.
        """
        self._ensure_connected()
        assert self._session is not None
        path = f"/v1/tasks/{task_id}/stop"
        try:
            # The HPKE session returns the response directly, unlike the plain
            # aiohttp session the Library calls use.
            response = await self._session.request("POST", path, json=dump_wire_model(request))
            await _raise_for_http_status(response, path=path)
            response_body = await response.read()
        except HTTPTransportError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError) as exc:
            raise HTTPTransportConnectionError(f"Task stop request failed: {path}") from exc

        try:
            return TaskStopAccepted.model_validate_json(response_body)
        except ValidationError as exc:
            raise HTTPTransportConnectionError("Task stop response did not match its schema") from exc

    async def submit_tool_results(
        self,
        task_id: str,
        request: BridgeToolResultsRequest,
        *,
        last_event_id: str | None = None,
    ) -> AsyncIterator[BridgeSSEEvent]:
        """Submit tool execution results and stream continuation events."""
        async for event in self._stream_request(
            method="POST",
            path=f"/v1/tasks/{task_id}",
            json_data=dump_wire_model(request),
            task_id=task_id,
            last_event_id=last_event_id,
        ):
            yield event

    async def register_agent_manifest(self, request: AgentRegistrationMessage) -> None:
        """Register the current SDK tool manifest with the bridge."""
        await self._post_bridge_accepted("/v1/agent/registration", request)

    async def send_agent_heartbeat(self, request: AgentHeartbeatMessage) -> AgentHeartbeatResponse:
        """Send one heartbeat and parse bridge clock diagnostics."""
        self._ensure_connected()
        assert self._session is not None
        path = "/v1/agent/heartbeat"
        response = await self._session.post(path, json=dump_wire_model(request))
        await _raise_for_http_status(response, path=path)
        if response.status != HTTPStatus.ACCEPTED:
            raise HTTPTransportConnectionError(f"Bridge heartbeat response returned HTTP {response.status}")
        response_body = await response.read()
        try:
            return AgentHeartbeatResponse.model_validate_json(response_body)
        except ValidationError as exc:
            raise HTTPTransportConnectionError("Heartbeat response did not match schema") from exc

    async def deregister_agent_process(self, request: AgentDeregistrationMessage) -> None:
        """Deregister the current SDK process lease."""
        await self._post_bridge_accepted("/v1/agent/deregistration", request)

    async def create_library(self, request: LibraryCreateRequest) -> LibraryWithRevision:
        """Create or resolve a general Library through the collection route."""
        self._ensure_connected()
        tenant_id = self._require_library_tenant_id()
        return await self._request_library_model(
            "POST",
            _library_gateway_path(tenant_id),
            LibraryWithRevision,
            request=request,
            params=None,
            schema_error="Library create response did not match schema",
        )

    async def list_libraries(self) -> LibraryListResponse:
        """List Libraries accessible to the configured token."""
        self._ensure_connected()
        tenant_id = self._require_library_tenant_id()
        return await self._request_library_model(
            "GET",
            _library_gateway_path(tenant_id),
            LibraryListResponse,
            request=None,
            params=None,
            schema_error="Library list response did not match schema",
        )

    async def get_library(self, request: LibraryGetRequest) -> LibraryWithRevision:
        """Get one Library by stable id."""
        self._ensure_connected()
        tenant_id = self._require_library_tenant_id()
        return await self._request_library_model(
            "GET",
            _library_gateway_path(tenant_id, str(request.library_id)),
            LibraryWithRevision,
            request=None,
            params=None,
            schema_error="Library get response did not match schema",
        )

    async def update_library(self, request: LibraryUpdateRequest) -> LibraryWithRevision:
        """Append a Library path or tags revision."""
        self._ensure_connected()
        tenant_id = self._require_library_tenant_id()
        return await self._request_library_model(
            "PATCH",
            _library_gateway_path(tenant_id, str(request.library_id)),
            LibraryWithRevision,
            request=request.patch,
            params=None,
            schema_error="Library update response did not match schema",
        )

    async def delete_library(self, request: LibraryDeleteRequest) -> None:
        """Soft-delete one Library."""
        self._ensure_connected()
        tenant_id = self._require_library_tenant_id()
        await self._request_library_no_content(
            "DELETE",
            _library_gateway_path(tenant_id, str(request.library_id)),
        )

    async def list_library_documents(self, request: LibraryDocumentListRequest) -> LibraryDocumentPage:
        """List one bounded page of live documents in one Library."""
        self._ensure_connected()
        tenant_id = self._require_library_tenant_id()
        params = {"limit": str(request.limit)}
        if request.cursor is not None:
            params["cursor"] = request.cursor
        return await self._request_library_model(
            "GET",
            _library_gateway_path(tenant_id, str(request.library_id), "documents"),
            LibraryDocumentPage,
            request=None,
            params=params,
            schema_error="Library document page response did not match schema",
        )

    async def get_library_document(self, request: LibraryDocumentGetRequest) -> PublicIndexedDocument:
        """Get one document's current public state."""
        self._ensure_connected()
        tenant_id = self._require_library_tenant_id()
        return await self._request_library_model(
            "GET",
            _library_gateway_path(
                tenant_id,
                str(request.library_id),
                "documents",
                str(request.document_id),
            ),
            PublicIndexedDocument,
            request=None,
            params=None,
            schema_error="Library document get response did not match schema",
        )

    async def delete_library_document(self, request: LibraryDocumentDeleteRequest) -> None:
        """Soft-delete one document."""
        self._ensure_connected()
        tenant_id = self._require_library_tenant_id()
        await self._request_library_no_content(
            "DELETE",
            _library_gateway_path(
                tenant_id,
                str(request.library_id),
                "documents",
                str(request.document_id),
            ),
        )

    async def create_document_upload(
        self,
        request: LibraryDocumentUploadRequest,
    ) -> LibraryDocumentUploadResponse:
        """Request presigned URLs for document upload (RFC-113).

        ``POST /libraries/v1/tenants/{tenant_id}/document-uploads`` over plain
        HTTPS with ``Authorization: Bearer <api_token>``. Body is
        ``{size_bytes}``; caller identity is resolved server-side from the
        token hash (RFC-113).
        """
        self._ensure_connected()
        tenant_id = self._require_library_tenant_id()
        path = _library_gateway_path(tenant_id, "document-uploads")
        return await self._request_library_model(
            "POST",
            path,
            LibraryDocumentUploadResponse,
            request=request,
            params=None,
            schema_error="Document upload response did not match schema",
        )

    async def create_library_document(
        self, request: LibraryDocumentCreateOperationRequest
    ) -> LibraryDocumentCreateResponse:
        """Create a queued Library document after all parts uploaded.

        ``POST /libraries/v1/tenants/{tenant_id}/{library_id}/documents`` over
        plain HTTPS with ``Authorization: Bearer <api_token>`` (RFC-113,
        §5).
        """
        self._ensure_connected()
        tenant_id = self._require_library_tenant_id()
        path = _library_gateway_path(tenant_id, str(request.library_id), "documents")
        return await self._request_library_model(
            "POST",
            path,
            LibraryDocumentCreateResponse,
            request=request.document,
            params=None,
            schema_error="Document create response did not match schema",
        )

    def _ensure_connected(self) -> None:
        """Raise if not connected."""
        if not self.is_connected:
            raise HTTPTransportConnectionError("HTTP transport not connected. Call connect() first.")

    async def _post_bridge_accepted(
        self,
        path: str,
        request: AgentRegistrationMessage | AgentDeregistrationMessage,
    ) -> None:
        """POST one typed lifecycle message and require an empty HTTP 202 response."""
        self._ensure_connected()
        assert self._session is not None
        response = await self._session.post(path, json=dump_wire_model(request))
        await _raise_for_http_status(response, path=path)
        if response.status != HTTPStatus.ACCEPTED:
            raise HTTPTransportConnectionError(f"Bridge accepted response returned HTTP {response.status}: POST {path}")
        if await response.read():
            raise HTTPTransportConnectionError(f"Bridge accepted response contained an unexpected body: POST {path}")

    async def _request_library_model(
        self,
        method: str,
        path: str,
        response_model: type[_LibraryModelT],
        *,
        request: BaseModel | None,
        params: Mapping[str, str] | None,
        schema_error: str,
    ) -> _LibraryModelT:
        """Send one Library request and validate its response from raw JSON bytes."""
        assert self._library_session is not None
        try:
            if request is None:
                response_context = self._library_session.request(method, path, params=params)
            else:
                response_context = self._library_session.request(
                    method,
                    path,
                    json=dump_wire_model(request),
                    params=params,
                )
            async with response_context as response:
                await _raise_for_http_status(response, path=path)
                response_body = await response.read()
        except HTTPTransportError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError) as exc:
            raise HTTPTransportConnectionError(f"Library request failed: {method} {path}") from exc

        try:
            return response_model.model_validate_json(response_body)
        except ValidationError as exc:
            raise HTTPTransportConnectionError(schema_error) from exc

    async def _request_library_no_content(self, method: str, path: str) -> None:
        """Send one Library request whose public contract requires HTTP 204."""
        assert self._library_session is not None
        try:
            async with self._library_session.request(method, path) as response:
                await _raise_for_http_status(response, path=path)
                if response.status != HTTPStatus.NO_CONTENT:
                    raise HTTPTransportConnectionError(
                        f"Library no-content response returned HTTP {response.status}: {method} {path}"
                    )
        except HTTPTransportError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError) as exc:
            raise HTTPTransportConnectionError(f"Library request failed: {method} {path}") from exc

    def _require_library_tenant_id(self) -> str:
        """Return the tenant path segment required by Library routes."""
        if not self._tenant_id:
            raise ConfigurationError(
                "Library operations require tenant_id in DualeAIConfig or DUALEAI_TENANT_ID",
                config_key="tenant_id",
            )
        return self._tenant_id

    async def _stream_request(
        self,
        method: str,
        path: str,
        task_id: str,
        json_data: Mapping[str, object] | None = None,
        last_event_id: str | None = None,
    ) -> AsyncIterator[BridgeSSEEvent]:
        """Execute an SSE request with mid-stream-disconnect resilience.

        ## What this defends against

        Long-running tasks routinely outlive the lifetime of the
        underlying TCP connection between the SDK and the bridge.
        Cloud load balancers and reverse proxies in front of the
        bridge often close in-flight connections when their backend
        target pool reconciles (e.g. after a backend autoscale
        event). The bridge process serving the request stays alive
        throughout — only the connection dies. This surfaces to
        aiohttp as ``ClientPayloadError`` (chunked transfer ended
        without a final 0-length chunk).

        ## Why retries are safe — and when they aren't

        The bridge's POST handler publishes before returning ``200 OK``.
        Repeating the POST can therefore publish the same logical request more
        than once. The stable client-selected task ID makes Router task and
        route admission idempotent; switching to GET after an observed 2xx
        avoids needless republishing while the SDK resumes the stream.

        The bridge's GET handler is read-only. ``Last-Event-ID`` is the exact
        ``NATS-sequence:event-index`` position last yielded to the caller. The
        Bridge starts the typed JetStream consumer at that NATS sequence and
        suppresses events through the recorded index before yielding later
        events. Without a cursor it reads the task stream from its beginning.

        Decision rule, encoded in ``_resolve_retry_method``:

            - A retryable connection failure before the SDK reads a 2xx retries
              the original method with the same task ID. Server receipt is
              ambiguous. HTTP error responses do not retry in this transport.
            - Failure after ``response.raise_for_status()`` returns
              2xx (chunked transfer cut, server disconnect mid-body)
              means Bridge has already published; retry switches to GET for
              replay. The ``stream_established`` flag tracks the observed 2xx.

        Both paths use the same ``Last-Event-ID`` header. On the
        pre-flight path it stays at the caller-supplied value
        (``None`` on a fresh call); on the post-flight path it
        carries the last yielded event id so the bridge replays from
        the right point.

        ## Retry budget

        ``_MAX_RETRIES = 10`` with
        ``wait_exponential_jitter(initial=1.0, max=10.0)`` gives a
        cumulative backoff of roughly 65-69 seconds (9 inter-attempt
        waits 1+2+4+8+10+10+10+10+10 = 65s, plus up to ~4s jitter) —
        sized to cover the typical re-registration window of a
        cloud load balancer adding/removing a backend (a few tens of
        seconds) with margin. Bounded by attempt count rather than
        deadline so failure modes are predictable: if the bridge is
        genuinely down, ten retries surface the failure to the
        caller without hammering the endpoint for the full task
        deadline.

        ## Bridge invariant we depend on

        GET ``/v1/tasks/{task_id}`` MUST stay read-only and
        idempotent. If the bridge's GET handler ever starts
        publishing to its message bus, the POST→GET method-switching
        logic below silently breaks (it would duplicate-publish on
        retry). When changing the bridge GET path, re-verify that
        the GET handler only streams events from the existing per-task
        event source and does not enqueue new work.

        Args:
            method: HTTP method (GET, POST). For POST, the body is
                sent on the first attempt only; subsequent retries
                (after the stream was established) switch to GET to
                avoid republishing.
            path: Request path.
            task_id: Task ID for logging.
            json_data: Optional JSON body (POST). Dropped on GET
                retries.
            last_event_id: Caller-supplied initial replay position.
                Used on the very first attempt; thereafter the
                function tracks its own ``current_last_event_id`` from
                the events it yields.

        Yields:
            ``BridgeSSEEvent`` objects after the exact composite cursor. A
            successful resume does not yield an event at or before the
            caller's last acknowledged event index.

        Raises:
            HTTPTransportAuthError: 401/403 — non-retryable.
            HTTPTransportResponseError: non-authentication 4xx — non-retryable.
            HTTPTransportConnectionError: 5xx — non-retryable.
            HTTPTransportStreamError: malformed SSE payload — unrecoverable.
            aiohttp.ClientError (subclass): retry budget exhausted.
        """
        self._ensure_connected()
        assert self._session is not None

        current_last_event_id = last_event_id
        # Once raise_for_status() returns 2xx, the SDK knows that Bridge
        # published the POST. Later attempts use GET; before this point receipt
        # is ambiguous and the same task ID makes POST replay converge in Router.
        stream_established = False

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._MAX_RETRIES),
            wait=wait_exponential_jitter(
                initial=self._RETRY_INITIAL_SECONDS,
                max=self._RETRY_MAX_SECONDS,
            ),
            retry=retry_if_exception_type(RETRYABLE_STREAM_ERRORS),
            reraise=True,
        ):
            with attempt:
                # Pure PSK auth: NO Authorization header - avoids MITM
                # token exposure. psk_id is sent automatically via
                # X-HPKE-PSK-ID header by HPKEClientSession. Server
                # resolves psk_id (hash) to raw token via Profile.
                effective_method, effective_body = _resolve_retry_method(
                    method,
                    json_data,
                    stream_established=stream_established,
                )
                headers: dict[str, str] = {
                    "Accept": HTTPDefaults.ACCEPT_SSE,
                    "Accept-Encoding": HTTPDefaults.ACCEPT_ENCODING,
                }
                if current_last_event_id is not None:
                    headers["Last-Event-ID"] = str(current_last_event_id)
                # Propagate the active span's W3C trace context so the platform
                # can stitch SDK spans to bridge/router spans (RFC-115). No-op
                # when no span is active (e.g. a task submitted outside any host
                # span).
                inject_trace_context(headers)

                response = None
                try:
                    # Use json= for automatic serialization by HPKEClientSession
                    response = await self._session.request(
                        effective_method,
                        path,
                        headers=headers,
                        json=effective_body,
                    )
                    await _raise_for_http_status(response, path=path)
                    # The observed 2xx proves that Bridge published the POST.
                    # Later attempts use GET to avoid republishing it.
                    stream_established = True

                    # Use iter_sse for HPKE-encrypted SSE streams
                    async for event in parse_sse_stream(self._session.iter_sse(response)):
                        current_last_event_id = event.id
                        yield event

                except SSEChecksumError as e:
                    # Resume after the last event the parser yielded. The failed
                    # event's composite cursor cannot be decremented safely: it
                    # may be the first or a later event in one NATS message.
                    logger.warning(
                        "SSE checksum mismatch, retrying",
                        task_id=task_id,
                        event_id=e.event_id,
                        event_type=e.event_type,
                        expected=e.expected,
                        actual=e.actual,
                        retry_from=current_last_event_id,
                        attempt=attempt.retry_state.attempt_number,
                    )
                    raise  # Let tenacity retry with updated Last-Event-ID

                except SSEParseError as e:
                    logger.error(
                        "SSE parse error",
                        task_id=task_id,
                        error=str(e),
                    )
                    raise HTTPTransportStreamError(f"SSE parse error: {e}") from e

                except aiohttp.ClientError as e:
                    logger.warning(
                        "HTTP connection error, retrying",
                        task_id=task_id,
                        method=method,  # original (caller-facing)
                        effective_method=effective_method,  # post-switch
                        path=path,
                        stream_established=stream_established,
                        last_event_id=current_last_event_id,
                        error=str(e),
                        error_type=type(e).__name__,
                        attempt=attempt.retry_state.attempt_number,
                    )
                    raise

                finally:
                    if response is not None:
                        response.close()
