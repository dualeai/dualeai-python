"""Protected Task, Agent, and Library metadata transport.

Task and Agent calls use the Bridge hpke-http/3 endpoint. Library metadata
uses the Library endpoint with bearer credentials inside the protected request.
Presigned object-store uploads are separate. Transport behavior is covered by
``tests/test_http_transport_v3.py``.
"""

import hashlib
import json
from collections.abc import AsyncGenerator, Mapping
from contextlib import AsyncExitStack
from http import HTTPStatus
from typing import TypeVar
from urllib.parse import quote, urlsplit

import aiohttp
from hpke_http.middleware.aiohttp import DiscoveredEndpoint, HPKEClientSession, HPKEResponse
from hpke_http.protocol import ProtocolError, StateError
from hpke_http.transport import TransportError
from pydantic import BaseModel, ValidationError
from structlog import get_logger
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

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


def _retryable_stream_error(error: BaseException, *, stream_established: bool) -> bool:
    """Retry pre-start discovery faults and interrupted Task streams."""
    return (
        isinstance(error, SSEChecksumError)
        or (
            isinstance(error, TransportError)
            and error.code in {"network_error", "discovery_network", "discovery_expired"}
        )
        or (stream_established and isinstance(error, ProtocolError) and error.code == "malformed_envelope")
    )


class HTTPTransport:
    """Protected Bridge and Library sessions with per-service discovery leases."""

    _MAX_RETRIES = 5
    _CONNECT_TIMEOUT_SECONDS = 5.0
    _SOCK_READ_TIMEOUT_SECONDS = 60.0
    _FINITE_TIMEOUT_SECONDS = 60.0

    def __init__(self, endpoint: str, token: str, tenant_id: str | None = None) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._origin = _endpoint_origin(self._endpoint)
        self._token = token
        self._tenant_id = tenant_id
        # This public identity is the service's existing token lookup key.
        self._psk_id = hashlib.sha512(token.encode("utf-8")).digest()
        self._bridge_session: HPKEClientSession | None = None
        self._library_session: HPKEClientSession | None = None
        self._resources: AsyncExitStack | None = None

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
        """Create one shared discovery source and protected client per service."""
        if self.is_connected:
            return
        if self._resources is not None:
            await self.disconnect()

        # Closing the stack releases each client before its key source and HTTP pool.
        resources = AsyncExitStack()
        timeout = aiohttp.ClientTimeout(
            total=self._FINITE_TIMEOUT_SECONDS,
            connect=self._CONNECT_TIMEOUT_SECONDS,
            sock_read=self._SOCK_READ_TIMEOUT_SECONDS,
        )
        psk = self._token.encode("utf-8")

        async def session(path: str) -> HPKEClientSession:
            source = await resources.enter_async_context(DiscoveredEndpoint(f"{self._endpoint}{path}", timeout=timeout))
            return await resources.enter_async_context(HPKEClientSession(source, psk, self._psk_id))

        try:
            bridge = await session(_BRIDGE_PATH)
            library = await session(_LIBRARY_PATH)
        except BaseException:
            await resources.aclose()
            raise
        self._resources = resources
        self._bridge_session = bridge
        self._library_session = library
        logger.debug("Protected HTTP transport initialized", endpoint=self._endpoint)

    async def disconnect(self) -> None:
        resources = self._resources
        self._bridge_session = None
        self._library_session = None
        self._resources = None
        if resources is not None:
            await resources.aclose()
        logger.debug("Protected HTTP transport disconnected")

    def _ensure_connected(self, *, library: bool = False) -> HPKEClientSession:
        if not self.is_connected:
            raise HTTPTransportConnectionError("HTTP transport not connected. Call connect() first.")
        session = self._library_session if library else self._bridge_session
        assert session is not None
        return session

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
        session = self._ensure_connected(library=library)
        headers: dict[str, str] = {}
        if library:
            headers["Authorization"] = f"Bearer {self._token}"
        inject_trace_context(headers)
        try:
            response = await session.request(
                method,
                self._url(path),
                params=params,
                headers=headers,
                json=dump_wire_model(request) if request is not None else None,
            )
        except (TransportError, ProtocolError, StateError, aiohttp.ClientError, TimeoutError) as error:
            raise HTTPTransportConnectionError(f"Protected request failed: {method} {path}") from error
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
        session = self._ensure_connected()
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
                    async with session.stream(
                        effective_method,
                        self._url(path),
                        headers=headers,
                        json=effective_body,
                        timeout=aiohttp.ClientTimeout(
                            total=None,
                            connect=self._CONNECT_TIMEOUT_SECONDS,
                            sock_read=self._SOCK_READ_TIMEOUT_SECONDS,
                        ),
                    ) as response:
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
