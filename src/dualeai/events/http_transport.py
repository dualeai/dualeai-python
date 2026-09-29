"""Protected Task, Agent, and Library metadata transport.

Task and Agent calls use the Bridge hpke-http/3 endpoint. Library metadata
uses the Library endpoint. Both authenticate with the issued platform token.
Presigned object-store uploads are separate. Transport behavior is covered by
``tests/test_http_transport_v3.py``.
"""

import asyncio
import json
from collections.abc import AsyncGenerator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import timedelta
from http import HTTPStatus
from time import monotonic
from typing import TypeVar
from urllib.parse import quote, urlsplit

import aiohttp
from hpke_http.middleware.aiohttp import DiscoveredEndpoint, HPKEClientSession, HPKEResponse
from hpke_http.protocol import ProtocolError, StateError
from hpke_http.transport import TransportError
from pydantic import BaseModel, ValidationError
from structlog import get_logger
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from dualeai._platform_token import PlatformToken, PlatformTokenIssueError, PlatformTokenProvider
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
_BRIDGE_PATH = "/http-bridge/v1/hpke"
_LIBRARY_PATH = "/libraries/v1/hpke"


def _endpoint_origin(endpoint: str) -> str:
    """Validate and return the HTTPS Gateway base origin."""
    parts = urlsplit(endpoint)
    if (
        parts.scheme != "https"
        or not parts.netloc
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or parts.path not in ("", "/")
    ):
        raise ValueError("endpoint must be an HTTPS Gateway base URL")
    return f"https://{parts.netloc}"


def _library_gateway_path(tenant_id: str, *segments: str) -> str:
    """Build the target Library route with escaped path identifiers."""
    values = ["tenants", tenant_id, *segments]
    return _LIBRARY_PATH + "/" + "/".join(quote(value, safe="") for value in values)


def _resolve_retry_method(
    method: str,
    json_data: Mapping[str, object] | None,
    *,
    stream_established: bool,
) -> tuple[str, Mapping[str, object] | None]:
    """Resume an accepted Task with read-only GET; replay its ID before START."""
    if stream_established and method == "POST":
        return "GET", None
    return method, json_data


class HTTPTransportError(Exception):
    """Base error for protected API operations."""

    def __init__(self, message: str, *, problem_details: ProblemDetails | None = None) -> None:
        super().__init__(message)
        self.problem_details = problem_details


class HTTPTransportConnectionError(HTTPTransportError):
    """Network, outer endpoint, or protected finite reply failure."""


class HTTPTransportResponseError(HTTPTransportError):
    """An authenticated logical client request was rejected."""


class HTTPTransportAuthError(HTTPTransportError):
    """An authenticated logical request was denied."""


class HTTPTransportStreamError(HTTPTransportError):
    """A protected Task stream failed."""


def _problem_details(body: bytes) -> ProblemDetails | None:
    """Parse a complete, authenticated logical error body when it has the schema."""
    try:
        value = json.loads(body)
        if isinstance(value, dict):
            return ProblemDetails.model_validate(value)
    except (ValueError, TypeError, ValidationError):
        pass
    return None


def _check_status(status: int, body: bytes, *, path: str) -> None:
    """Classify an authenticated logical status and body."""
    if HTTPStatus.OK <= status < HTTPStatus.MULTIPLE_CHOICES:
        return
    if status < HTTPStatus.BAD_REQUEST:
        raise HTTPTransportConnectionError(f"Unexpected logical HTTP {status}: {path}")
    problem = _problem_details(body)
    message = f"HTTP {status}: {path}"
    if status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
        raise HTTPTransportAuthError(message, problem_details=problem)
    if status < HTTPStatus.INTERNAL_SERVER_ERROR:
        raise HTTPTransportResponseError(message, problem_details=problem)
    raise HTTPTransportConnectionError(message, problem_details=problem)


def _is_expired_lease(error: BaseException) -> bool:
    """Whether a service's discovery lease ended before the request could start.

    `hpke_http` checks the cached key record's remaining life at the instant it
    seals the first protected record, so the window is only the gap between that
    check and the POST. The next attempt fetches a fresh record. One owner for
    the code string, because a stream and a finite request must classify this
    fault the same way.
    """
    return isinstance(error, TransportError) and error.code == "discovery_expired"


def _retryable_stream_error(error: BaseException, *, stream_established: bool) -> bool:
    """Retry pre-start discovery faults and interrupted Task streams."""
    return (
        isinstance(error, SSEChecksumError)
        or _is_expired_lease(error)
        or (isinstance(error, TransportError) and error.code in {"network_error", "discovery_network"})
        or (stream_established and isinstance(error, ProtocolError) and error.code == "malformed_envelope")
    )


def _is_refused_credential(error: BaseException) -> bool:
    """Whether this failure MIGHT be the tunnel refusing our platform token.

    A service resolves the presented identifier before it decrypts anything. An
    identifier it cannot resolve — revoked, or expired earlier than its stamped
    instant — ends the exchange at the outer HTTP layer with 400, so the client
    never sees a decrypted body to read a reason from. That status is the whole
    signal, and `TransportError.status_code` is set only for an invalid outer
    status.

    IT IS NOT A CERTAIN SIGNAL, AND CANNOT BE MADE ONE. `hpke_http`'s FastAPI
    middleware answers a malformed envelope, an over-limit body and a recipient
    key the client no longer shares with the same 400 and the byte-identical
    body `b"invalid protected request"`, so nothing on the wire separates them
    from a withdrawn credential. `HTTPTransport._refuse_held_token` carries the
    floor that makes acting on an uncertain signal safe.
    """
    return (
        isinstance(error, TransportError)
        and error.code == "outer_status"
        and error.status_code == HTTPStatus.BAD_REQUEST
    )


def make_connector() -> aiohttp.TCPConnector:
    """The one pool :meth:`HTTPTransport.connect` hands to all three sessions.

    A function rather than an inline call because it is the seam a test
    substitutes: patching it hands the transport a connector the test owns,
    which is how `test_http_transport_v3.py`'s
    `test_real_v3_tls_boundary_reuses_discovery_per_service` counts the pools
    the transport builds and proves there is exactly one.
    """
    return aiohttp.TCPConnector()


class HTTPTransport:
    """Protected Bridge and Library sessions with per-service discovery leases."""

    _MAX_RETRIES = 5
    _CONNECT_TIMEOUT = timedelta(seconds=5)
    _SOCK_READ_TIMEOUT = timedelta(minutes=1)
    _FINITE_TIMEOUT = timedelta(minutes=1)
    _REISSUE_FLOOR = timedelta(minutes=1)
    """Shortest interval between two issuances a refused request may trigger.

    A floor on the rate, not a cap on the count: a month-long process is
    entitled to every replacement a revocation actually calls for, and a count
    would eventually refuse one. See `_refuse_held_token` for what it buys.
    """

    def __init__(self, endpoint: str, token: str, tenant_id: str | None = None) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._origin = _endpoint_origin(self._endpoint)
        self._token = token
        self._tenant_id = tenant_id
        # THE CREDENTIAL IS OBTAINED, NOT DERIVED FROM THE API TOKEN. The API
        # token identifies this agent to the token service; the derived platform
        # token opens every tunnel without transmitting that key during issuance.
        self._tokens: PlatformTokenProvider | None = None
        self._held: PlatformToken | None = None
        self._bridge_source: DiscoveredEndpoint | None = None
        self._library_source: DiscoveredEndpoint | None = None
        self._bridge_session: HPKEClientSession | None = None
        self._library_session: HPKEClientSession | None = None
        # TWO LIFETIMES, TWO STACKS. The connector, the issuance session and each
        # service's discovery lease live on `_resources`, as long as the transport.
        # The tunnels live on `_tunnels`, as long as one token, because
        # `HPKEClientSession` binds the key at construction, so rotation rebuilds
        # `_tunnels` alone.
        self._resources: AsyncExitStack | None = None
        self._tunnels: AsyncExitStack | None = None
        # A replaced client remains open only while a request still uses it.
        self._retired: set[AsyncExitStack] = set()
        self._tunnel_users: dict[AsyncExitStack, int] = {}
        self._reissued_at: float | None = None
        self._rotating = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        return (
            self._bridge_session is not None
            and not self._bridge_session.closed
            and self._library_session is not None
            and not self._library_session.closed
        )

    @property
    def endpoint(self) -> str:
        return self._endpoint

    def _url(self, path: str) -> str:
        return f"{self._origin}{path}"

    async def connect(self) -> None:
        """Open the transport: one pool, one credential owner, one lease per service."""
        if self.is_connected:
            return
        if self._resources is not None:
            await self.disconnect()

        resources = AsyncExitStack()
        timeout = aiohttp.ClientTimeout(
            total=self._FINITE_TIMEOUT.total_seconds(),
            connect=self._CONNECT_TIMEOUT.total_seconds(),
            sock_read=self._SOCK_READ_TIMEOUT.total_seconds(),
        )
        # ONE CONNECTOR FOR EVERY CALL TO THIS ORIGIN, issuance included. Each
        # session would otherwise open its own pool and its own TLS handshakes to
        # the same host.
        #
        # `connector_owner=False` is what makes sharing safe: `aiohttp` has a
        # session close the connector it was given unless told otherwise, so the
        # first session to exit would take the pool away from the others. The
        # stack closes the connector itself, after every session that uses it.
        connector = make_connector()
        resources.push_async_callback(connector.close)
        pooled: dict[str, object] = {"connector": connector, "connector_owner": False}

        try:
            # Issuance is TLS-only and outside every tunnel, so it uses a plain
            # session. Its own, because the protected clients must not share a
            # session with a pre-credential call.
            issuance = await resources.enter_async_context(
                aiohttp.ClientSession(timeout=timeout, **pooled)  # ty: ignore[invalid-argument-type]
            )
            self._tokens = PlatformTokenProvider(issuance, self._endpoint, self._token)
            # THE DISCOVERY LEASE OUTLIVES THE TOKEN. It caches the service's own
            # HPKE key, which has nothing to do with our credential, so keeping it
            # on this stack means a rotation costs no re-discovery.
            self._bridge_source = await resources.enter_async_context(
                DiscoveredEndpoint(f"{self._endpoint}{_BRIDGE_PATH}", timeout=timeout, **pooled)  # ty: ignore[invalid-argument-type]
            )
            self._library_source = await resources.enter_async_context(
                DiscoveredEndpoint(f"{self._endpoint}{_LIBRARY_PATH}", timeout=timeout, **pooled)  # ty: ignore[invalid-argument-type]
            )
        except BaseException:
            await resources.aclose()
            raise

        self._resources = resources
        try:
            await self._bind_tunnels()
        except BaseException:
            await self.disconnect()
            raise
        logger.debug("Protected HTTP transport initialized", endpoint=self._endpoint)

    async def _bind_tunnels(self) -> None:
        """Make both tunnels speak for the current token, rebuilding if it rotated.

        Called before every request, and free in the ordinary case: nothing is
        retired, the provider answers from the token it holds, and the identity
        check below returns at once. Only a replaced token reaches the rebuild.
        """
        if await self._current_token() is self._held and self._tunnels is not None:
            return

        async with self._rotating:
            token = await self._current_token()
            if token is self._held and self._tunnels is not None:
                return

            resources, bridge_source, library_source = self._resources, self._bridge_source, self._library_source
            assert resources is not None and bridge_source is not None and library_source is not None

            tunnels = AsyncExitStack()
            try:
                # NO CONNECTOR HERE, AND THAT IS HOW THE POOL STAYS SHARED. A
                # client built on a `DiscoveredEndpoint` speaks over that
                # source's own session, so it already holds the connector the
                # source was given; `hpke_http` refuses transport options here
                # outright — "the shared source owns the outer transport
                # options".
                bridge = await tunnels.enter_async_context(HPKEClientSession(bridge_source, token.psk, token.psk_id))
                library = await tunnels.enter_async_context(HPKEClientSession(library_source, token.psk, token.psk_id))
            except BaseException:
                await tunnels.aclose()
                raise

            retired = self._tunnels
            self._tunnels = tunnels
            self._bridge_session = bridge
            self._library_session = library
            self._held = token
            if retired is not None:
                self._retired.add(retired)
            logger.debug("Platform token rotated", endpoint=self._endpoint)
        await self._close_idle_tunnels()

    async def _current_token(self) -> PlatformToken:
        """Translate issuance failures before binding a tunnel.

        Initial and mandatory renewal errors share the public contract enforced
        by ``test_platform_token_errors.py``.
        """
        assert self._tokens is not None, "connect() did not run"
        try:
            return await self._tokens.get()
        except PlatformTokenIssueError as error:
            error_type = (
                HTTPTransportAuthError
                if error.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN)
                else HTTPTransportConnectionError
            )
            raise error_type(str(error), problem_details=error.problem_details) from error
        except (aiohttp.ClientError, TimeoutError, ValueError) as error:
            raise HTTPTransportConnectionError("Platform token issuance failed") from error

    async def _close_idle_tunnels(self) -> None:
        """Close replaced clients after their last request or stream releases them.

        Token expiry constrains admission, not an admitted stream's lifetime.
        Closing a client also closes its active responses, so `_session_for`
        retains it until completion, iterator close, or cancellation. Idle
        replacements close immediately, without retaining credential history.
        Covered by `test_http_transport_v3.py`'s
        `test_retired_tunnels_keep_admitted_streams_until_the_last_response_finishes`.
        """
        idle = self._retired.difference(self._tunnel_users)
        self._retired.difference_update(idle)
        async with AsyncExitStack() as closing:
            for stack in idle:
                closing.push_async_callback(stack.aclose)

    async def disconnect(self) -> None:
        """Close every tunnel, live and retired, then the resources under them.

        Explicit disconnect ends active responses too. Clients must close
        before the discovery sources and shared connector they use.
        """
        tunnels, resources = self._tunnels, self._resources
        retired = self._retired
        self._bridge_session = None
        self._library_session = None
        self._bridge_source = None
        self._library_source = None
        self._tunnels = None
        self._tokens = None
        self._held = None
        self._retired = set()
        self._resources = None
        try:
            # One stack, so a tunnel that fails to close still leaves the others
            # closed. The live tunnels close before the retired ones.
            async with AsyncExitStack() as closing:
                for stack in retired:
                    closing.push_async_callback(stack.aclose)
                if tunnels is not None:
                    closing.push_async_callback(tunnels.aclose)
        finally:
            if resources is not None:
                await resources.aclose()
        logger.debug("Protected HTTP transport disconnected")

    @asynccontextmanager
    async def _session_for(self, *, library: bool = False) -> AsyncGenerator[HPKEClientSession, None]:
        """Hold a current tunnel through one request's complete response lifetime."""
        if self._resources is None:
            raise HTTPTransportConnectionError("HTTP transport not connected. Call connect() first.")
        await self._bind_tunnels()
        session = self._library_session if library else self._bridge_session
        tunnels = self._tunnels
        assert session is not None and tunnels is not None
        self._tunnel_users[tunnels] = self._tunnel_users.get(tunnels, 0) + 1
        try:
            yield session
        finally:
            remaining = self._tunnel_users[tunnels] - 1
            if remaining:
                self._tunnel_users[tunnels] = remaining
            else:
                del self._tunnel_users[tunnels]
                await self._close_idle_tunnels()

    def _refuse_held_token(self) -> None:
        """Invalidate a possibly refused credential, at most once a minute.

        The provider owns the reusable credential; `_held` identifies the token
        bound to the current clients. When the floor permits invalidation, the
        next `_session_for` asks for a fresh token. `_bind_tunnels` replaces the
        clients when that token object changes; clearing `_held` also marks the
        current binding for replacement. Admitted responses retain their clients
        until release, as `_close_idle_tunnels` describes.

        THE FLOOR IS WHAT KEEPS THAT FROM BEING A LOOP. The signal it acts on is
        uncertain: `_is_refused_credential` reads an outer 400, which the service
        also returns for a malformed envelope and for an over-limit body. Without
        a floor, a caller posting one oversized body per request mints one
        platform token per request — one issuance, one record at the issuer and a
        full tunnel rebuild each time — in a loop no new token can break.
        The floor bounds refusal-driven issuance by time instead of by request,
        and costs a genuine revocation nothing, because the first refusal after a
        quiet minute always reissues.

        A successful request does not reset the floor. Resetting would let a
        caller alternating one good request with one malformed one reopen the
        very loop this closes.

        `_request_finite` retries once whether or not the floor let an issuance
        through. That retry is what lets a burst of concurrent requests refused
        on the same withdrawn token finish on the single replacement the first
        of them obtained.
        """
        now = monotonic()
        if self._reissued_at is not None and now - self._reissued_at < self._REISSUE_FLOOR.total_seconds():
            return
        self._reissued_at = now
        if self._tokens is not None:
            self._tokens.invalidate()
        self._held = None

    def _require_library_tenant_id(self) -> str:
        if not self._tenant_id:
            raise ConfigurationError(
                "Library operations require tenant_id in DualeAIConfig or DUALEAI_TENANT_ID",
                config_key="tenant_id",
            )
        return self._tenant_id

    async def _request_finite(
        self,
        method: str,
        path: str,
        *,
        request: BaseModel | None = None,
        params: Mapping[str, str] | None = None,
        library: bool = False,
    ) -> HPKEResponse:
        """Return a complete authenticated response, including its checked body."""
        headers: dict[str, str] = {}
        inject_trace_context(headers)
        body = dump_wire_model(request) if request is not None else None

        # ONE FURTHER ATTEMPT PER FAULT, NEVER A LOOP. Proactive replacement
        # covers expiry, so a refusal here means the credential was withdrawn,
        # the clocks disagree, or the request itself is malformed; one fresh
        # token either resolves or does not, and a third would only make the
        # refusal slower. An expired discovery lease is retried for the same
        # reason `_retryable_stream_error` retries it on a stream: the window is
        # the gap between the lease check and the POST, and the next attempt
        # fetches a fresh key record. A finite call that raised instead made one
        # transient fault behave two ways.
        retried_credential = False
        retried_lease = False
        while True:
            try:
                async with self._session_for(library=library) as session:
                    response = await session.request(
                        method,
                        self._url(path),
                        params=params,
                        headers=headers,
                        json=body,
                    )
            except (TransportError, ProtocolError, StateError, aiohttp.ClientError, TimeoutError) as error:
                if not retried_credential and _is_refused_credential(error):
                    retried_credential = True
                    self._refuse_held_token()
                    continue
                if not retried_lease and _is_expired_lease(error):
                    retried_lease = True
                    continue
                raise HTTPTransportConnectionError(f"Protected request failed: {method} {path}") from error
            break
        _check_status(response.status, await response.read(), path=path)
        return response

    async def _request_model(
        self,
        method: str,
        path: str,
        response_model: type[_LibraryModelT],
        *,
        request: BaseModel | None = None,
        params: Mapping[str, str] | None = None,
        library: bool = False,
        schema_error: str,
    ) -> _LibraryModelT:
        response = await self._request_finite(method, path, request=request, params=params, library=library)
        try:
            return response_model.model_validate_json(await response.read())
        except ValidationError as error:
            raise HTTPTransportConnectionError(schema_error) from error

    def run_task(self, task_id: str, request: BridgeTaskRequest) -> AsyncGenerator[BridgeSSEEvent, None]:
        return self._stream_request(
            method="POST",
            path=f"{_BRIDGE_PATH}/tasks/{quote(task_id, safe='')}",
            task_id=task_id,
            json_data=dump_wire_model(request),
        )

    async def stop_task(self, task_id: str, request: TaskStopRequest) -> TaskStopAccepted:
        return await self._request_model(
            "POST",
            f"{_BRIDGE_PATH}/tasks/{quote(task_id, safe='')}/stop",
            TaskStopAccepted,
            request=request,
            schema_error="Task stop response did not match its schema",
        )

    def submit_tool_results(
        self,
        task_id: str,
        request: BridgeToolResultsRequest,
        *,
        last_event_id: str | None = None,
    ) -> AsyncGenerator[BridgeSSEEvent, None]:
        return self._stream_request(
            method="POST",
            path=f"{_BRIDGE_PATH}/tasks/{quote(task_id, safe='')}",
            task_id=task_id,
            json_data=dump_wire_model(request),
            last_event_id=last_event_id,
        )

    async def _post_bridge_accepted(
        self, path: str, request: AgentRegistrationMessage | AgentDeregistrationMessage
    ) -> None:
        response = await self._request_finite("POST", path, request=request)
        if response.status != HTTPStatus.ACCEPTED:
            raise HTTPTransportConnectionError(f"Bridge accepted response returned HTTP {response.status}: POST {path}")
        if await response.read():
            raise HTTPTransportConnectionError(f"Bridge accepted response contained an unexpected body: POST {path}")

    async def register_agent_manifest(self, request: AgentRegistrationMessage) -> None:
        await self._post_bridge_accepted(f"{_BRIDGE_PATH}/agent/registration", request)

    async def send_agent_heartbeat(self, request: AgentHeartbeatMessage) -> AgentHeartbeatResponse:
        response = await self._request_finite("POST", f"{_BRIDGE_PATH}/agent/heartbeat", request=request)
        if response.status != HTTPStatus.ACCEPTED:
            raise HTTPTransportConnectionError(f"Bridge heartbeat response returned HTTP {response.status}")
        try:
            return AgentHeartbeatResponse.model_validate_json(await response.read())
        except ValidationError as error:
            raise HTTPTransportConnectionError("Heartbeat response did not match schema") from error

    async def deregister_agent_process(self, request: AgentDeregistrationMessage) -> None:
        await self._post_bridge_accepted(f"{_BRIDGE_PATH}/agent/deregistration", request)

    async def create_library(self, request: LibraryCreateRequest) -> LibraryWithRevision:
        return await self._request_model(
            "POST",
            _library_gateway_path(self._require_library_tenant_id()),
            LibraryWithRevision,
            request=request,
            library=True,
            schema_error="Library create response did not match schema",
        )

    async def list_libraries(self) -> LibraryListResponse:
        return await self._request_model(
            "GET",
            _library_gateway_path(self._require_library_tenant_id()),
            LibraryListResponse,
            library=True,
            schema_error="Library list response did not match schema",
        )

    async def get_library(self, request: LibraryGetRequest) -> LibraryWithRevision:
        path = _library_gateway_path(self._require_library_tenant_id(), str(request.library_id))
        return await self._request_model(
            "GET",
            path,
            LibraryWithRevision,
            library=True,
            schema_error="Library get response did not match schema",
        )

    async def update_library(self, request: LibraryUpdateRequest) -> LibraryWithRevision:
        path = _library_gateway_path(self._require_library_tenant_id(), str(request.library_id))
        return await self._request_model(
            "PATCH",
            path,
            LibraryWithRevision,
            request=request.patch,
            library=True,
            schema_error="Library update response did not match schema",
        )

    async def _request_no_content(self, method: str, path: str) -> None:
        response = await self._request_finite(method, path, library=True)
        if response.status != HTTPStatus.NO_CONTENT:
            raise HTTPTransportConnectionError(
                f"Library no-content response returned HTTP {response.status}: {method} {path}"
            )
        if await response.read():
            raise HTTPTransportConnectionError(f"Library no-content response contained a body: {method} {path}")

    async def delete_library(self, request: LibraryDeleteRequest) -> None:
        await self._request_no_content(
            "DELETE", _library_gateway_path(self._require_library_tenant_id(), str(request.library_id))
        )

    async def list_library_documents(self, request: LibraryDocumentListRequest) -> LibraryDocumentPage:
        params = {"limit": str(request.limit)}
        if request.cursor is not None:
            params["cursor"] = request.cursor
        path = _library_gateway_path(self._require_library_tenant_id(), str(request.library_id), "documents")
        return await self._request_model(
            "GET",
            path,
            LibraryDocumentPage,
            params=params,
            library=True,
            schema_error="Library document page response did not match schema",
        )

    async def get_library_document(self, request: LibraryDocumentGetRequest) -> PublicIndexedDocument:
        path = _library_gateway_path(
            self._require_library_tenant_id(), str(request.library_id), "documents", str(request.document_id)
        )
        return await self._request_model(
            "GET",
            path,
            PublicIndexedDocument,
            library=True,
            schema_error="Library document get response did not match schema",
        )

    async def delete_library_document(self, request: LibraryDocumentDeleteRequest) -> None:
        path = _library_gateway_path(
            self._require_library_tenant_id(), str(request.library_id), "documents", str(request.document_id)
        )
        await self._request_no_content("DELETE", path)

    async def create_document_upload(self, request: LibraryDocumentUploadRequest) -> LibraryDocumentUploadResponse:
        path = _library_gateway_path(self._require_library_tenant_id(), "document-uploads")
        return await self._request_model(
            "POST",
            path,
            LibraryDocumentUploadResponse,
            request=request,
            library=True,
            schema_error="Document upload response did not match schema",
        )

    async def create_library_document(
        self, request: LibraryDocumentCreateOperationRequest
    ) -> LibraryDocumentCreateResponse:
        path = _library_gateway_path(self._require_library_tenant_id(), str(request.library_id), "documents")
        return await self._request_model(
            "POST",
            path,
            LibraryDocumentCreateResponse,
            request=request.document,
            library=True,
            schema_error="Document create response did not match schema",
        )

    async def _stream_request(
        self,
        method: str,
        path: str,
        task_id: str,
        json_data: Mapping[str, object] | None = None,
        last_event_id: str | None = None,
    ) -> AsyncGenerator[BridgeSSEEvent, None]:
        """Run or resume the Task stream with the service's stable-ID contract."""
        current_last_event_id = last_event_id
        stream_established = False

        def retry_after_failure(error: BaseException) -> bool:
            return _retryable_stream_error(error, stream_established=stream_established)

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._MAX_RETRIES),
                wait=wait_exponential_jitter(initial=1.0, max=10.0),
                retry=retry_if_exception(retry_after_failure),
                reraise=True,
            ):
                with attempt:
                    effective_method, effective_body = _resolve_retry_method(
                        method, json_data, stream_established=stream_established
                    )
                    headers = {"Accept": HTTPDefaults.ACCEPT_SSE}
                    if current_last_event_id is not None:
                        headers["Last-Event-ID"] = current_last_event_id
                    inject_trace_context(headers)
                    async with (
                        self._session_for() as session,
                        session.stream(
                            effective_method,
                            self._url(path),
                            headers=headers,
                            json=effective_body,
                            timeout=aiohttp.ClientTimeout(
                                total=None,
                                connect=self._CONNECT_TIMEOUT.total_seconds(),
                                sock_read=self._SOCK_READ_TIMEOUT.total_seconds(),
                            ),
                        ) as response,
                    ):
                        if response.mode == "finite":
                            body = await response.read()
                            _check_status(response.status, body, path=path)
                            raise HTTPTransportStreamError(
                                f"Task stream returned finite HTTP {response.status}: {path}"
                            )
                        if response.mode != "sse" or response.status != HTTPStatus.OK:
                            raise HTTPTransportStreamError(
                                f"Task stream returned HTTP {response.status} in mode {response.mode}: {path}"
                            )
                        stream_established = True
                        try:
                            async for event in parse_sse_stream(response.iter_sse()):
                                current_last_event_id = event.id
                                yield event
                        except SSEChecksumError:
                            raise
                        except SSEParseError as error:
                            raise HTTPTransportStreamError(f"SSE parse error: {error}") from error
        except (TransportError, ProtocolError, StateError, aiohttp.ClientError, TimeoutError) as error:
            raise HTTPTransportStreamError(f"Protected Task stream failed: {task_id}") from error
        except SSEChecksumError as error:
            raise HTTPTransportStreamError(f"Task stream checksum failed: {task_id}") from error
