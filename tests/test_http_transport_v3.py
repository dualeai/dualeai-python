"""SDK-owned hpke-http/3 behavior with synthetic credentials and loopback TLS."""

import asyncio
import json
import secrets
import ssl
from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import UUID
from zlib import crc32

import aiohttp
import pytest
import trustme
from aiohttp import web
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from freezegun import freeze_time
from hpke_http.middleware._discovery import KEY_MEDIA_TYPE, encode_key_record
from hpke_http.middleware.aiohttp import DiscoveredEndpoint, HPKEClientSession, HPKEResponse
from hpke_http.protocol import Header, ProtocolError, Response, Server, generate_key_pair
from hpke_http.transport import RESPONSE_MEDIA_TYPE, TransportError
from opentelemetry.sdk.trace import TracerProvider
from tenacity import wait_none
from yarl import URL

import dualeai.events.http_transport as transport_module
from dualeai import DualeAISDK
from dualeai._platform_token import PlatformToken, derive_psk
from dualeai.config import DualeAIConfig
from dualeai.events.client import CloudEventsClient
from dualeai.events.http_transport import (
    HTTPTransport,
    HTTPTransportAuthError,
    HTTPTransportConnectionError,
    HTTPTransportResponseError,
    HTTPTransportStreamError,
)
from dualeai.models.bridge import (
    AgentDeregistrationMessage,
    AgentHeartbeatMessage,
    AgentRegistrationMessage,
    Attachment,
    BridgeSSEEvent,
    BridgeTaskContinueRequest,
    BridgeTaskCreateRequest,
    BridgeToolResultsRequest,
    BridgeToolResultSuccess,
    HeartbeatStatus,
)
from dualeai.models.capability import Capability
from dualeai.models.library import (
    LibraryCreateRequest,
    LibraryDeleteRequest,
    LibraryDocumentCreateOperationRequest,
    LibraryDocumentCreatePartRef,
    LibraryDocumentCreateRequest,
    LibraryDocumentDeleteRequest,
    LibraryDocumentGetRequest,
    LibraryDocumentListRequest,
    LibraryDocumentUploadRequest,
    LibraryGetRequest,
    LibraryPatchRequest,
    LibraryUpdateRequest,
)
from dualeai.models.response_format import JsonSchemaResponseFormat, PredefinedResponseFormat
from dualeai.models.routing_policy import RoutingPolicy
from dualeai.models.task_stop import TaskStopRequest
from dualeai.models.tool import Parameters, Tool

_TOKEN = "dualeai_test_v3_token_padded_beyond_32_bytes"
_TENANT = "tenant-test"
_LIBRARY_ID = "018f35a8-2e90-7f2c-a6bb-9426f9c1f501"
_DOCUMENT_ID = "018f35a8-2e90-7f2c-a6bb-9426f9c1f502"
_UPLOAD_ID = "018f35a8-2e90-7f2c-a6bb-9426f9c1f503"
_BASE = f"https://api.example.test/v1/hpke/tenants/{_TENANT}"
_DATE = "2026-06-12T10:00:00Z"


def _library() -> dict[str, object]:
    return {
        "id": _LIBRARY_ID,
        "path": "projects/contracts",
        "tags": {},
        "updated_by": "user:test",
        "updated_at": _DATE,
        "created_at": _DATE,
        "deleted_at": None,
    }


def _document() -> dict[str, object]:
    return {
        "document_id": _DOCUMENT_ID,
        "library_id": _LIBRARY_ID,
        "filename": "contract.pdf",
        "description": "Contract",
        "content_type": None,
        "size_bytes": 10,
        "page_count": None,
        "created_at": _DATE,
        "deleted_at": None,
        "status": "queued",
        "progress_pct": None,
        "failure": None,
        "tags": {},
    }


def _response(status: int, body: object = None) -> HPKEResponse:
    payload = b"" if body is None else json.dumps(body).encode()
    return HPKEResponse(
        status=status,
        headers=[("content-type", "application/json")],
        body=payload,
        url=URL(_BASE),
        method="GET",
    )


@dataclass
class _Call:
    method: str
    url: str
    headers: dict[str, str]
    params: dict[str, str] | None
    body: object


class _FiniteSession:
    def __init__(self, responses: Sequence[HPKEResponse | BaseException]) -> None:
        self.responses = list(responses)
        self.calls: list[_Call] = []
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def request(self, method, url, *, headers=None, params=None, json=None):
        self.calls.append(_Call(method, url, dict(headers or {}), dict(params) if params else None, json))
        result = self.responses.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


_STUB_TOKEN = PlatformToken(
    psk_id=b"\x11" * 64,
    psk=b"dualeai_" + b"0" * 64,
    valid_until=datetime(2099, 1, 1, tzinfo=timezone.utc),
)
"""One credential for every stubbed transport, far from its replacement bands."""


_REPLACEMENT_TOKEN = PlatformToken(
    psk_id=b"\x22" * 64,
    psk=b"dualeai_" + b"1" * 64,
    valid_until=datetime(2099, 1, 1, tzinfo=timezone.utc),
)
"""What the owner hands back after a service refuses the first one.

A DIFFERENT IDENTIFIER, so a test can tell a re-keyed tunnel from a reused one.
With one token the transport could skip the rebuild entirely and still pass.
"""


class _HeldToken:
    """A credential owner holding one token, and a second after a refusal.

    Stands in for `PlatformTokenProvider`, whose own replacement bands are
    proved in `test_platform_token_rotation.py`. Here only the transport's
    reaction matters: what it does when a service rejects what it presented.
    """

    def __init__(self) -> None:
        self.refusals = 0

    async def get(self) -> PlatformToken:
        return _REPLACEMENT_TOKEN if self.refusals else _STUB_TOKEN

    def invalidate(self) -> None:
        self.refusals += 1


def _as_connected(transport: HTTPTransport, *, bridge: object, library: object) -> _HeldToken:
    """Put `transport` in the state `connect()` leaves, with stub tunnels.

    `_session_for` refuses a transport that never connected, and re-keys its
    tunnels whenever the owner hands back a different token — so `_held` must be
    the very object `get()` returns, or every request would rebuild.
    """
    tokens = _HeldToken()
    transport.__dict__.update(
        _resources=AsyncExitStack(),
        _tunnels=AsyncExitStack(),
        _tokens=tokens,
        _held=_STUB_TOKEN,
        _bridge_session=bridge,
        _library_session=library,
    )
    return tokens


async def _with_session(
    responses: Sequence[HPKEResponse | BaseException], *, library: bool = True
) -> tuple[HTTPTransport, _FiniteSession]:
    transport = HTTPTransport("https://api.example.test", _TOKEN, _TENANT)
    session = _FiniteSession(responses)
    other = _FiniteSession([])
    _as_connected(
        transport,
        bridge=other if library else session,
        library=session if library else other,
    )
    return transport, session


@pytest.mark.unit
def test_gateway_base_cannot_include_an_operation_path() -> None:
    with pytest.raises(ValueError, match="HTTPS Gateway base URL"):
        HTTPTransport("https://api.example.test/hpke", _TOKEN, _TENANT)


@pytest.mark.unit
async def test_public_library_routes_use_platform_token_without_api_token_headers() -> None:
    document_id = UUID(_DOCUMENT_ID)
    library_id = UUID(_LIBRARY_ID)
    upload = {
        "upload_id": _UPLOAD_ID,
        "parts": [{"part_number": 1, "upload_url": "https://s3.example.test/part", "offset": 0, "length": 10}],
        "expires_at": _DATE,
        "parts_expires_at": _DATE,
    }
    responses = [
        _response(201, _library()),
        _response(200, {"libraries": [_library()]}),
        _response(200, _library()),
        _response(200, _library()),
        _response(204),
        _response(200, {"documents": [_document()], "next_cursor": None}),
        _response(200, _document()),
        _response(204),
        _response(200, upload),
        _response(
            202,
            {
                "document_id": _DOCUMENT_ID,
                "library_id": _LIBRARY_ID,
                "status": "queued",
                "location": f"/v1/hpke/tenants/{_TENANT}/{_LIBRARY_ID}/documents/{_DOCUMENT_ID}",
            },
        ),
    ]
    transport, session = await _with_session(responses)
    sdk = DualeAISDK(
        config=DualeAIConfig(endpoint="https://api.example.test", token=_TOKEN, tenant_id=_TENANT),
        transport=transport,
        auto_start=False,
    )
    try:
        created = await sdk.libraries.create(LibraryCreateRequest(path="projects/contracts"))
        listed = await sdk.libraries.list()
        fetched = await sdk.libraries.get(LibraryGetRequest(library_id=library_id))
        updated = await sdk.libraries.update(
            LibraryUpdateRequest(library_id=library_id, patch=LibraryPatchRequest(tags={}))
        )
        await sdk.libraries.delete(LibraryDeleteRequest(library_id=library_id))
        page = await sdk.libraries.list_documents(
            LibraryDocumentListRequest(library_id=library_id, limit=12, cursor="opaque +/=")
        )
        document = await sdk.libraries.get_document(
            LibraryDocumentGetRequest(library_id=library_id, document_id=document_id)
        )
        await sdk.libraries.delete_document(
            LibraryDocumentDeleteRequest(library_id=library_id, document_id=document_id)
        )
        await transport.create_document_upload(LibraryDocumentUploadRequest(size_bytes=10))
        receipt = await transport.create_library_document(
            LibraryDocumentCreateOperationRequest(
                library_id=library_id,
                document=LibraryDocumentCreateRequest(
                    upload_id=UUID(_UPLOAD_ID),
                    filename="contract.pdf",
                    description="Contract",
                    content_sha256="a" * 64,
                    size_bytes=10,
                    parts=[LibraryDocumentCreatePartRef(part_number=1, etag='"etag-1"')],
                ),
            )
        )
    finally:
        await sdk.cleanup()
    assert created.id == library_id
    assert [item.id for item in listed.libraries] == [library_id]
    assert fetched.id == updated.id == library_id
    assert [item.document_id for item in page.documents] == [document_id]
    assert document.document_id == document_id
    assert receipt.location == f"/v1/hpke/tenants/{_TENANT}/{_LIBRARY_ID}/documents/{_DOCUMENT_ID}"
    assert [(call.method, urlsplit(call.url).path) for call in session.calls] == [
        ("POST", f"/v1/hpke/tenants/{_TENANT}"),
        ("GET", f"/v1/hpke/tenants/{_TENANT}"),
        ("GET", f"/v1/hpke/tenants/{_TENANT}/{_LIBRARY_ID}"),
        ("PATCH", f"/v1/hpke/tenants/{_TENANT}/{_LIBRARY_ID}"),
        ("DELETE", f"/v1/hpke/tenants/{_TENANT}/{_LIBRARY_ID}"),
        ("GET", f"/v1/hpke/tenants/{_TENANT}/{_LIBRARY_ID}/documents"),
        ("GET", f"/v1/hpke/tenants/{_TENANT}/{_LIBRARY_ID}/documents/{_DOCUMENT_ID}"),
        ("DELETE", f"/v1/hpke/tenants/{_TENANT}/{_LIBRARY_ID}/documents/{_DOCUMENT_ID}"),
        ("POST", f"/v1/hpke/tenants/{_TENANT}/document-uploads"),
        ("POST", f"/v1/hpke/tenants/{_TENANT}/{_LIBRARY_ID}/documents"),
    ]
    assert all("authorization" not in {name.lower() for name in call.headers} for call in session.calls)
    assert session.calls[3].body == {"tags": {}}
    assert session.calls[5].params == {"limit": "12", "cursor": "opaque +/="}
    assert session.calls[8].body == {"size_bytes": 10}
    document_body = session.calls[9].body
    assert isinstance(document_body, dict)
    assert document_body == {
        "upload_id": _UPLOAD_ID,
        "filename": "contract.pdf",
        "description": "Contract",
        "content_sha256": "a" * 64,
        "size_bytes": 10,
        "parts": [{"part_number": 1, "etag": '"etag-1"'}],
    }


@pytest.mark.unit
async def test_complete_logical_error_and_unchecked_failures_have_distinct_mapping() -> None:
    problem = {"title": "Denied", "status": 401, "detail": "Synthetic denial", "error_code": "DENIED"}
    transport, _ = await _with_session([_response(401, problem)])
    with pytest.raises(HTTPTransportAuthError) as raised:
        await transport.list_libraries()
    assert raised.value.problem_details is not None
    assert raised.value.problem_details.error_code == "DENIED"

    transport, _ = await _with_session([TransportError("outer_status", "synthetic outer 503", status_code=503)])
    with pytest.raises(HTTPTransportConnectionError) as raised:
        await transport.list_libraries()
    assert raised.value.problem_details is None
    assert isinstance(raised.value.__cause__, TransportError)

    transport, _ = await _with_session([ProtocolError("authentication_failed", "synthetic bad tag")])
    with pytest.raises(HTTPTransportConnectionError) as raised:
        await transport.list_libraries()
    assert raised.value.problem_details is None
    assert isinstance(raised.value.__cause__, ProtocolError)

    transport, _ = await _with_session([_response(410, {"title": "Deleted", "status": 410})])
    with pytest.raises(HTTPTransportResponseError):
        await transport.list_libraries()


@pytest.mark.unit
async def test_delete_requires_checked_204_and_empty_body() -> None:
    transport, _ = await _with_session([_response(200, {})])
    with pytest.raises(HTTPTransportConnectionError, match="HTTP 200"):
        await transport.delete_library(LibraryDeleteRequest(library_id=UUID(_LIBRARY_ID)))

    transport, _ = await _with_session([_response(204, {"unexpected": True})])
    with pytest.raises(HTTPTransportConnectionError, match="contained a body"):
        await transport.delete_library(LibraryDeleteRequest(library_id=UUID(_LIBRARY_ID)))

    transport, _ = await _with_session([_response(302, _library())])
    with pytest.raises(HTTPTransportConnectionError, match="Unexpected logical HTTP 302"):
        await transport.get_library(LibraryGetRequest(library_id=UUID(_LIBRARY_ID)))


@pytest.mark.unit
async def test_library_rejects_malformed_checked_response() -> None:
    transport, _ = await _with_session([_response(200, {"libraries": "not-a-list"})])
    with pytest.raises(HTTPTransportConnectionError, match="Library list response did not match schema"):
        await transport.list_libraries()


def _agent_messages() -> tuple[AgentRegistrationMessage, AgentHeartbeatMessage, AgentDeregistrationMessage]:
    timestamp = datetime(2026, 9, 24, tzinfo=timezone.utc)
    process_id = UUID("0192d52a-7000-7000-8000-000000000010")
    publication_id = UUID("0192d52a-7000-7000-8000-000000000011")
    return (
        AgentRegistrationMessage(
            agent_id="agent_test",
            process_id=process_id,
            tools=[],
            config_hash="a" * 64,
            manifest_publication_id=publication_id,
            time=timestamp,
            sdk_version="0.1.0",
        ),
        AgentHeartbeatMessage(
            agent_id="agent_test",
            process_id=process_id,
            status=HeartbeatStatus.healthy,
            time=timestamp,
            config_hash="a" * 64,
            heartbeat_publication_id=publication_id,
        ),
        AgentDeregistrationMessage(
            agent_id="agent_test",
            process_id=process_id,
            deregistration_publication_id=publication_id,
            reason="shutdown",
            time=timestamp,
        ),
    )


@pytest.mark.unit
async def test_agent_lifecycle_and_task_stop_use_protected_finite_routes() -> None:
    registration, heartbeat, deregistration = _agent_messages()
    transport, session = await _with_session(
        [
            _response(202),
            _response(
                202,
                {
                    "server_received_at": _DATE,
                    "server_sent_at": _DATE,
                    "client_sent_at": _DATE,
                },
            ),
            _response(202),
            _response(202, {"task_id": "task-stop-me-123456", "accepted_at": _DATE}),
        ],
        library=False,
    )
    await transport.register_agent_manifest(registration)
    await transport.send_agent_heartbeat(heartbeat)
    await transport.deregister_agent_process(deregistration)
    accepted = await transport.stop_task("task-stop-me-123456", TaskStopRequest(reason="Synthetic stop"))
    assert accepted.task_id == "task-stop-me-123456"
    assert [urlsplit(call.url).path for call in session.calls] == [
        "/v1/hpke/agent/registration",
        "/v1/hpke/agent/heartbeat",
        "/v1/hpke/agent/deregistration",
        "/v1/hpke/tasks/task-stop-me-123456/stop",
    ]
    assert all(call.method == "POST" and "Authorization" not in call.headers for call in session.calls)
    registration_body = session.calls[0].body
    heartbeat_body = session.calls[1].body
    deregistration_body = session.calls[2].body
    assert isinstance(registration_body, dict)
    assert isinstance(heartbeat_body, dict)
    assert isinstance(deregistration_body, dict)
    assert registration_body["agent_id"] == "agent_test"
    assert heartbeat_body["status"] == "healthy"
    assert deregistration_body["reason"] == "shutdown"
    assert session.calls[3].body == {"reason": "Synthetic stop"}


@pytest.mark.unit
async def test_agent_lifecycle_rejects_wrong_accepted_status_and_malformed_replies() -> None:
    registration, heartbeat, deregistration = _agent_messages()

    transport, _ = await _with_session([_response(200)], library=False)
    with pytest.raises(HTTPTransportConnectionError, match="accepted response returned HTTP 200"):
        await transport.register_agent_manifest(registration)

    transport, _ = await _with_session([_response(202, {})], library=False)
    with pytest.raises(HTTPTransportConnectionError, match="accepted response contained an unexpected body"):
        await transport.deregister_agent_process(deregistration)

    transport, _ = await _with_session([_response(200, {})], library=False)
    with pytest.raises(HTTPTransportConnectionError, match="heartbeat response returned HTTP 200"):
        await transport.send_agent_heartbeat(heartbeat)

    transport, _ = await _with_session([_response(202, {})], library=False)
    with pytest.raises(HTTPTransportConnectionError, match="Heartbeat response did not match schema"):
        await transport.send_agent_heartbeat(heartbeat)


def _task_request() -> BridgeTaskCreateRequest:
    return BridgeTaskCreateRequest(
        type="create",
        action_prompt="Synthetic action",
        deadline=datetime(2026, 12, 31, tzinfo=timezone.utc),
    )


def _completed_block(event_id: str = "1:1") -> bytes:
    data = (
        '{"type":"task.completed","timestamp":"2026-09-24T00:00:00Z","result":{"completion":"done","cache_hit":false}}'
    )
    checksum = crc32(f"task.completed:{data}".encode()) & 0xFFFFFFFF
    return f": crc={checksum:08x}\nid: {event_id}\nevent: task.completed\ndata: {data}\n\n".encode()


def _delta_block(event_id: str, delta: str) -> bytes:
    data = json.dumps(
        {"type": "content.delta", "timestamp": "2026-09-24T00:00:00Z", "delta": delta},
        separators=(",", ":"),
    )
    checksum = crc32(f"content.delta:{data}".encode()) & 0xFFFFFFFF
    return f": crc={checksum:08x}\nid: {event_id}\nevent: content.delta\ndata: {data}\n\n".encode()


class _StreamReply:
    def __init__(self, blocks: list[bytes | BaseException], *, status: int = 200, mode: str = "sse") -> None:
        self.blocks = blocks
        self.status = status
        self.mode = mode
        self.closed = False

    async def read(self) -> bytes:
        return b"".join(block for block in self.blocks if isinstance(block, bytes))

    async def iter_sse(self) -> AsyncIterator[bytes]:
        for block in self.blocks:
            if isinstance(block, BaseException):
                raise block
            yield block

    async def aclose(self) -> None:
        self.closed = True


class _StreamContext:
    def __init__(self, result: _StreamReply | BaseException) -> None:
        self.result = result

    async def __aenter__(self) -> _StreamReply:
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result

    async def __aexit__(self, *_args: object) -> None:
        if isinstance(self.result, _StreamReply):
            await self.result.aclose()


class _StreamSession(_FiniteSession):
    def __init__(self, results: list[_StreamReply | BaseException]) -> None:
        super().__init__([])
        self.results = results

    def stream(self, method, url, *, headers=None, json=None, timeout=None):
        self.calls.append(_Call(method, url, dict(headers or {}), None, json))
        return _StreamContext(self.results.pop(0))


async def _with_stream_session(results: list[_StreamReply | BaseException]) -> tuple[HTTPTransport, _StreamSession]:
    transport = HTTPTransport("https://api.example.test", _TOKEN)
    session = _StreamSession(results)
    _as_connected(transport, bridge=session, library=_FiniteSession([]))
    return transport, session


@pytest.mark.unit
async def test_task_create_fields_reach_the_hpke_session_with_wire_aliases() -> None:
    tool = Tool(
        name="reserve",
        description="Reserve units.",
        parameters=Parameters.model_validate(
            {
                "type": "object",
                "properties": {"sku": {"type": "string"}},
                "required": ["sku"],
                "additionalProperties": False,
            }
        ),
    )
    request = BridgeTaskCreateRequest(
        type="create",
        action_prompt="do it",
        deadline=datetime(2026, 12, 31, tzinfo=timezone.utc),
        tools=[tool],
        routing_policy=RoutingPolicy(target_accuracy=0.9, required_capabilities=[Capability.analysis]),
        response_format=JsonSchemaResponseFormat(json_schema={"type": "object"}),
        response_stream=True,
        attachments=[Attachment(key="attachment-key", filename="report.pdf", description="Quarterly report")],
    )
    transport, session = await _with_stream_session([_StreamReply([_completed_block()])])

    assert [event.data.type async for event in transport.run_task("task-wire-fields", request)] == ["task.completed"]

    [call] = session.calls
    assert (call.method, urlsplit(call.url).path) == ("POST", "/v1/hpke/tasks/task-wire-fields")
    body = call.body
    assert isinstance(body, dict)
    assert body["response_stream"] is True
    assert body["routing_policy"] == {"target_accuracy": 0.9, "required_capabilities": ["analysis"]}
    assert body["response_format"] == {"json_schema": {"type": "object"}}
    assert body["attachments"] == [
        {"key": "attachment-key", "filename": "report.pdf", "description": "Quarterly report"}
    ]
    tools = body["tools"]
    assert isinstance(tools, list)
    parameters = tools[0]["parameters"]
    assert parameters["additionalProperties"] is False
    assert "additional_properties" not in parameters


@pytest.mark.unit
async def test_continuation_and_tool_results_send_literal_child_bodies_and_cursor() -> None:
    continuation = BridgeTaskContinueRequest(
        type="continue",
        parent_task_id="parent-task-123",
        message="Continue with é漢🙂",
        response_format=PredefinedResponseFormat.json,
        deadline=datetime(2026, 8, 3, 12, tzinfo=timezone.utc),
    )
    results = BridgeToolResultsRequest(
        type="tool_results",
        tool_results=[
            BridgeToolResultSuccess(type="success", tool_call_id="call-123456789", output={"state": "closed"})
        ],
    )
    transport, session = await _with_stream_session(
        [_StreamReply([_completed_block()]), _StreamReply([_completed_block()])]
    )

    assert [event.data.type async for event in transport.run_task("child-task", continuation)] == ["task.completed"]
    assert [
        event.data.type async for event in transport.submit_tool_results("child-task", results, last_event_id="42:3")
    ] == ["task.completed"]

    assert [(call.method, urlsplit(call.url).path) for call in session.calls] == [
        ("POST", "/v1/hpke/tasks/child-task"),
        ("POST", "/v1/hpke/tasks/child-task"),
    ]
    assert session.calls[0].body == {
        "type": "continue",
        "parent_task_id": "parent-task-123",
        "message": "Continue with é漢🙂",
        "response_format": "json",
        "deadline": "2026-08-03T12:00:00Z",
    }
    assert "Last-Event-ID" not in session.calls[0].headers
    assert session.calls[1].body == {
        "type": "tool_results",
        "tool_results": [{"type": "success", "tool_call_id": "call-123456789", "output": {"state": "closed"}}],
    }
    assert session.calls[1].headers["Last-Event-ID"] == "42:3"


@pytest.mark.unit
async def test_task_rejected_finite_reply_keeps_problem_and_does_not_retry(monkeypatch) -> None:
    monkeypatch.setattr(transport_module, "wait_exponential_jitter", lambda **_kwargs: wait_none())
    problem = {
        "title": "Invalid continuation",
        "status": 422,
        "detail": "The parent cannot be continued",
        "error_code": "INVALID_CONTINUATION",
        "retryable": False,
    }
    transport, session = await _with_stream_session(
        [_StreamReply([json.dumps(problem).encode()], status=422, mode="finite")]
    )
    continuation = BridgeTaskContinueRequest(
        type="continue",
        parent_task_id="parent-task-123",
        message="Continue",
        deadline=datetime(2026, 8, 3, 12, tzinfo=timezone.utc),
    )

    with pytest.raises(HTTPTransportResponseError) as raised:
        _ = [event async for event in transport.run_task("child-task", continuation)]

    assert len(session.calls) == 1
    assert raised.value.problem_details is not None
    assert raised.value.problem_details.error_code == "INVALID_CONTINUATION"
    assert raised.value.problem_details.retryable is False


@pytest.mark.unit
async def test_active_trace_context_reaches_protected_logical_request() -> None:
    transport, session = await _with_stream_session([_StreamReply([_completed_block()])])
    tracer = TracerProvider().get_tracer("sdk-transport-test")

    with tracer.start_as_current_span("task") as span:
        assert [event.data.type async for event in transport.run_task("task-1", _task_request())] == ["task.completed"]
        context = span.get_span_context()

    assert session.calls[0].headers["traceparent"] == (
        f"00-{context.trace_id:032x}-{context.span_id:016x}-{context.trace_flags:02x}"
    )


@pytest.mark.unit
async def test_task_resumes_after_nonterminal_event_and_closes_each_stream(monkeypatch) -> None:
    monkeypatch.setattr(transport_module, "wait_exponential_jitter", lambda **_kwargs: wait_none())
    first = _StreamReply([_delta_block("5:1", "first"), TransportError("network_error", "synthetic disconnect")])
    second = _StreamReply([_completed_block("5:2")])
    transport, session = await _with_stream_session([first, second])
    client = CloudEventsClient(DualeAIConfig(token=_TOKEN), transport=transport)
    await client.connect()
    deltas: list[str] = []
    try:
        result = await client.run_task(
            task_id="task-1",
            request=_task_request(),
            delta_callback=lambda event: deltas.append(event.delta),
        )
    finally:
        await client.disconnect()
    assert result.type == "task.completed"
    assert deltas == ["first"]
    assert [call.method for call in session.calls] == ["POST", "GET"]
    assert all(urlsplit(call.url).path == "/v1/hpke/tasks/task-1" for call in session.calls)
    assert session.calls[1].headers["Last-Event-ID"] == "5:1"
    assert "Authorization" not in session.calls[0].headers
    assert session.calls[0].headers["Accept"] == "text/event-stream"
    assert first.closed and second.closed


@pytest.mark.unit
@pytest.mark.parametrize("discovery_error", ["discovery_network", "discovery_expired"])
async def test_task_replays_pre_start_post_but_never_retries_tampering(monkeypatch, discovery_error: str) -> None:
    monkeypatch.setattr(transport_module, "wait_exponential_jitter", lambda **_kwargs: wait_none())
    transport, session = await _with_stream_session(
        [
            TransportError(discovery_error, "synthetic pre-start discovery failure"),
            _StreamReply([_completed_block()]),
        ]
    )
    assert len([event async for event in transport.run_task("task-1", _task_request())]) == 1
    assert [call.method for call in session.calls] == ["POST", "POST"]

    transport, session = await _with_stream_session([ProtocolError("authentication_failed", "synthetic bad tag")])
    with pytest.raises(HTTPTransportStreamError) as raised:
        _ = [event async for event in transport.run_task("task-1", _task_request())]
    assert isinstance(raised.value.__cause__, ProtocolError)
    assert len(session.calls) == 1


@pytest.mark.unit
async def test_task_recovers_from_checked_crc_failure_and_missing_end(monkeypatch) -> None:
    monkeypatch.setattr(transport_module, "wait_exponential_jitter", lambda **_kwargs: wait_none())
    bad_crc = b": crc=00000000" + _delta_block("9:2", "ignored")[14:]
    first = _StreamReply([_delta_block("9:1", "first"), bad_crc])
    second = _StreamReply([_completed_block("9:2")])
    transport, session = await _with_stream_session([first, second])
    events = [event async for event in transport.run_task("task-1", _task_request())]
    assert [event.id for event in events] == ["9:1", "9:2"]
    assert [call.method for call in session.calls] == ["POST", "GET"]
    assert session.calls[1].headers["Last-Event-ID"] == "9:1"

    first = _StreamReply([_delta_block("10:1", "first"), ProtocolError("malformed_envelope", "missing END")])
    second = _StreamReply([_completed_block("10:2")])
    transport, session = await _with_stream_session([first, second])
    events = [event async for event in transport.run_task("task-1", _task_request())]
    assert [event.id for event in events] == ["10:1", "10:2"]
    assert [call.method for call in session.calls] == ["POST", "GET"]


@pytest.mark.unit
async def test_public_terminal_consumer_closes_live_iterator() -> None:
    event = BridgeSSEEvent.model_validate(
        {
            "id": "1:1",
            "timestamp": datetime(2026, 9, 24, tzinfo=timezone.utc),
            "data": {
                "type": "task.completed",
                "timestamp": "2026-09-24T00:00:00Z",
                "result": {"completion": "done", "cache_hit": False},
            },
        }
    )
    closed = False

    async def stream() -> AsyncIterator[BridgeSSEEvent]:
        nonlocal closed
        try:
            yield event
            await asyncio.Future()  # A live connection would otherwise remain open.
        finally:
            closed = True

    client = CloudEventsClient(DualeAIConfig(token=_TOKEN))
    result = await client._consume_terminal_stream(
        task_id="task-1",
        stream=stream(),
        operation="Task run",
        delta_callback=None,
        reset_callback=None,
        tool_use_callback=None,
    )
    assert result.type == "task.completed"
    assert closed


@pytest.mark.unit
@pytest.mark.parametrize("submit_results", [False, True])
async def test_closing_task_iterator_closes_protected_stream(submit_results: bool) -> None:
    reply = _StreamReply([_completed_block()])
    transport, _ = await _with_stream_session([reply])
    stream = (
        transport.submit_tool_results(
            "task-1",
            BridgeToolResultsRequest(
                type="tool_results",
                tool_results=[BridgeToolResultSuccess(type="success", tool_call_id="call_1", output="done")],
            ),
        )
        if submit_results
        else transport.run_task("task-1", _task_request())
    )

    assert (await anext(stream)).data.type == "task.completed"
    await stream.aclose()

    assert reply.closed


@pytest.mark.integration
async def test_real_v3_tls_boundary_reuses_discovery_per_service(  # noqa: PLR0915  # One TLS fixture and complete service exchanges.
    monkeypatch,
) -> None:
    """Real adapter reuses each service key, with platform-token auth and live SSE.

    The network endpoints include each service name; encrypted operations use
    /v1/hpke paths without that name. Discovery is leased per service, while
    issuance and both tunnels share the connector `make_connector` returns.
    """
    keypair = generate_key_pair()
    recipient_id = b"synthetic-key-1"
    server = Server(keypair.private_key, recipient_id)
    # The issuance key, fixed for the test. `endpoint` derives the same secret the
    # SDK did, from the client public key `platform_token` recorded.
    issuance_private = X25519PrivateKey.from_private_bytes(bytes.fromhex("02" * 32))
    issuance_public_hex = issuance_private.public_key().public_bytes_raw().hex()
    issued_psk_id = b""
    client_public_hex = ""
    ca = trustme.CA()
    cert = ca.issue_cert("localhost")
    server_ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    cert.configure_cert(server_ssl)
    client_ssl = ssl.create_default_context()
    ca.configure_trust(client_ssl)
    observed: list[tuple[str, str, dict[str, str], bytes]] = []
    outer_headers: list[dict[str, str]] = []
    calls: list[tuple[str, str]] = []
    release_stream = asyncio.Event()
    stream_done = asyncio.Event()

    async def endpoint(request: web.Request) -> web.StreamResponse:
        calls.append((request.method, request.path))
        outer_headers.append(dict(request.headers))
        if request.method == "GET":
            return web.Response(
                body=encode_key_record(recipient_id, server.public_key, 60), content_type=KEY_MEDIA_TYPE
            )
        raw = await request.read()
        start_length = server.stream_start_length(raw)
        assert start_length is not None
        preparsed = server.preparse_stream(raw[:start_length])
        # THE STUB IS THE PLATFORM, so it authenticates what it issued: the
        # identifier it minted below, and the key both sides derived.
        assert preparsed.psk_id == issued_psk_id
        opened = preparsed.authenticate(derive_psk(issuance_private, client_public_hex).encode()).admit(accepted=True)
        try:
            offset = start_length
            clear_body = bytearray()
            while offset < len(raw):
                consumed, record = opened.feed(raw, offset)
                assert consumed > 0
                offset += consumed
                if record is not None and record[0] == "data":
                    clear_body.extend(record[1])
            response_right = opened.finish_eof()
            try:
                logical = opened.head
                observed.append(
                    (logical.method.value, logical.path, {h.name: h.value for h in logical.headers}, bytes(clear_body))
                )
                if request.path == "/libraries/v1/hpke":
                    body = json.dumps({"libraries": []}).encode()
                    envelope = response_right.protect_response(
                        Response(200, (Header("content-type", "application/json"),), body)
                    )
                    return web.Response(body=envelope, content_type=RESPONSE_MEDIA_TYPE)
                if logical.path.endswith("/stop"):
                    body = json.dumps({"task_id": "task-stop-me-123456", "accepted_at": _DATE}).encode()
                    envelope = response_right.protect_response(
                        Response(202, (Header("content-type", "application/json"),), body)
                    )
                    return web.Response(body=envelope, content_type=RESPONSE_MEDIA_TYPE)
                sealer, first = response_right.into_sealer(200, (Header("content-type", "text/event-stream"),))
                response = web.StreamResponse(status=200, headers={"Content-Type": RESPONSE_MEDIA_TYPE})
                await response.prepare(request)
                try:
                    await response.write(first)
                    await response.write(sealer.seal_sse_block(_completed_block()))
                    await release_stream.wait()
                    await response.write(sealer.finish())
                    await response.write_eof()
                    return response
                finally:
                    sealer.close()
                    stream_done.set()
            finally:
                response_right.close()
        finally:
            opened.close()

    async def platform_token(request: web.Request) -> web.Response:
        """Issue like the token service does: derive against the caller's ephemeral key.

        Records the identifier and the caller's public key so `endpoint` above can
        authenticate exactly what this handed out.
        """
        nonlocal issued_psk_id, client_public_hex
        body = await request.json()
        assert request.headers["Authorization"] == f"Bearer {_TOKEN}"
        client_public_hex = body["client_public_key"]
        issued_psk_id = secrets.token_bytes(64)
        return web.json_response(
            {
                "psk_id": issued_psk_id.hex(),
                "issuer_public_key": issuance_public_hex,
                "identity": "agent:test-agent",
                "tenant_id": _TENANT,
                "assurance_level": "aal2",
                "valid_until": "2099-01-01T00:00:00Z",
            },
            status=201,
        )

    app = web.Application()
    app.router.add_route("POST", "/profile/platform-token", platform_token)
    app.router.add_route("*", "/http-bridge/v1/hpke", endpoint)
    app.router.add_route("*", "/libraries/v1/hpke", endpoint)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=server_ssl)
    await site.start()
    port = runner.addresses[0][1]
    real_source = DiscoveredEndpoint
    pools: list[aiohttp.TCPConnector] = []
    handed: list[object] = []

    def local_source(endpoint: str, **options: object) -> DiscoveredEndpoint:
        handed.append(options.get("connector"))
        return real_source(endpoint, **options)  # ty: ignore[invalid-argument-type]

    def one_pool() -> aiohttp.TCPConnector:
        # The loopback server presents a private CA, so the pool the transport
        # builds has to trust it. Patching the factory rather than the call keeps
        # the real issuance request, and counting what it returns is how this
        # test sees whether a second pool was ever created.
        pools.append(aiohttp.TCPConnector(ssl=client_ssl))
        return pools[-1]

    monkeypatch.setattr(transport_module, "DiscoveredEndpoint", local_source)
    monkeypatch.setattr(transport_module, "make_connector", one_pool)
    sdk = DualeAISDK(
        config=DualeAIConfig(endpoint=f"https://localhost:{port}", token=_TOKEN, tenant_id=_TENANT),
        auto_start=False,
    )
    try:
        assert (await sdk.libraries.list()).libraries == []
        assert (await sdk.libraries.list()).libraries == []
        tracer = TracerProvider().get_tracer("sdk-tls-test")
        with tracer.start_as_current_span("task") as span:
            trace_id = span.get_span_context().trace_id
            task = await sdk.submit_task("Synthetic action", capabilities=[], request_id="task-1")
            terminal = await asyncio.wait_for(task.task, timeout=5)
        assert terminal.type == "task.completed"
        assert not stream_done.is_set()  # Live DATA was available before END/outer EOF.
        release_stream.set()
        await asyncio.wait_for(stream_done.wait(), timeout=5)
        assert (await sdk.stop_task("task-stop-me-123456", "Synthetic stop")).task_id == "task-stop-me-123456"
    finally:
        release_stream.set()
        try:
            await sdk.cleanup()
        finally:
            await runner.cleanup()
            server.close()
    # ONE POOL, NOT ONE PER SERVICE. Both discovery sources were handed the very
    # connector the transport built, and nothing built a second one — so issuance
    # and both tunnels reached this origin over the same sockets and the same TLS
    # policy. The transport closes it, which a session given `connector_owner`
    # would otherwise have done first, under the sessions still using it.
    assert len(pools) == 1, "the SDK opened more than one connection pool"
    assert handed == [pools[0], pools[0]]
    assert pools[0].closed
    assert calls == [
        ("GET", "/libraries/v1/hpke"),
        ("POST", "/libraries/v1/hpke"),
        ("POST", "/libraries/v1/hpke"),
        ("GET", "/http-bridge/v1/hpke"),
        ("POST", "/http-bridge/v1/hpke"),
        ("POST", "/http-bridge/v1/hpke"),
    ]
    assert [item[:2] for item in observed] == [
        ("GET", f"/v1/hpke/tenants/{_TENANT}"),
        ("GET", f"/v1/hpke/tenants/{_TENANT}"),
        ("POST", "/v1/hpke/tasks/task-1"),
        ("POST", "/v1/hpke/tasks/task-stop-me-123456/stop"),
    ]
    assert all("authorization" not in headers for _, _, headers, _ in observed)
    assert observed[2][2]["accept"] == "text/event-stream"
    assert observed[2][2]["traceparent"].split("-")[1] == f"{trace_id:032x}"
    assert json.loads(observed[2][3])["action_prompt"] == "Synthetic action"
    assert all("authorization" not in {name.lower() for name in headers} for headers in outer_headers)
    assert all("traceparent" not in {name.lower() for name in headers} for headers in outer_headers)


# ---------------------------------------------------------------------------
# A withdrawn credential is replaced under the caller, exactly once
# ---------------------------------------------------------------------------


def _refused_credential() -> TransportError:
    """An outer 400 that may indicate a refused platform token.

    Resolution happens before anything is decrypted, so the exchange ends at the
    outer HTTP layer if the identifier cannot resolve. Malformed and oversized
    requests share this status, so it does not establish the cause.
    """
    return TransportError("outer_status", "protected endpoint returned outer status 400", status_code=400)


def _refusing_transport(
    monkeypatch: pytest.MonkeyPatch,
    first: Sequence[HPKEResponse | BaseException],
    replacement: Sequence[HPKEResponse | BaseException] = (),
) -> tuple[HTTPTransport, _FiniteSession, _FiniteSession, _HeldToken, list[bytes]]:
    """A connected transport whose tunnels can actually be rebuilt.

    A refused credential re-keys the tunnels, because `HPKEClientSession` binds
    the key when it is constructed. The cases below therefore have to stand in
    for the session class as well as the sessions: `keyed_with` records the
    identifier each rebuild was given, which is how a re-keyed tunnel is told
    apart from a reused one.
    """
    transport = HTTPTransport("https://api.example.test", _TOKEN, _TENANT)
    live = _FiniteSession(first)
    rebuilt = _FiniteSession(replacement)
    tokens = _as_connected(transport, bridge=_FiniteSession([]), library=live)
    transport.__dict__.update(_bridge_source=object(), _library_source=object())

    keyed_with: list[bytes] = []
    queue = [_FiniteSession([]), rebuilt]

    def _rebuild(_source: object, _psk: bytes, psk_id: bytes) -> _FiniteSession:
        keyed_with.append(psk_id)
        return queue.pop(0)

    monkeypatch.setattr(transport_module, "HPKEClientSession", _rebuild)
    return transport, live, rebuilt, tokens, keyed_with


@pytest.mark.unit
async def test_a_refused_credential_is_replaced_and_the_call_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One outer 400 can recover through a successful credential replacement.

    This case allows reissuance and supplies a successful replacement response.
    The SDK re-keys both tunnels and reissues the request. It does not establish
    why the first request failed; a repeated refusal or the reissuance floor can
    still leave the caller with an error.
    """
    transport, live, rebuilt, tokens, keyed_with = _refusing_transport(
        monkeypatch,
        [_refused_credential()],
        [_response(200, {"libraries": []})],
    )

    result = await transport.list_libraries()

    assert result.libraries == []
    assert tokens.refusals == 1, "the withdrawn credential was reused"
    assert len(live.calls) == 1, "the refused tunnel was asked twice"
    assert len(rebuilt.calls) == 1, "the request was not replayed on the new tunnel"
    # BOTH tunnels re-keyed, and on the replacement. Leaving the Bridge on the
    # withdrawn credential would move the failure to the next Task instead.
    assert keyed_with == [_REPLACEMENT_TOKEN.psk_id, _REPLACEMENT_TOKEN.psk_id]


@pytest.mark.unit
async def test_a_credential_refused_twice_reaches_the_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One replacement, then the refusal is real and must not become a loop.

    A second fresh token would only turn a refusal into a slower refusal, and a
    client retrying an unresolvable identifier forever is how one revoked agent
    becomes load on its issuer.
    """
    transport, live, rebuilt, tokens, _keyed = _refusing_transport(
        monkeypatch,
        [_refused_credential()],
        [_refused_credential()],
    )

    with pytest.raises(HTTPTransportConnectionError, match="Protected request failed"):
        await transport.list_libraries()

    assert tokens.refusals == 1, "the transport asked for a third credential"
    assert len(live.calls) == 1
    assert len(rebuilt.calls) == 1


@pytest.mark.unit
async def test_a_failure_that_is_not_a_refusal_never_touches_the_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A network error does not trigger the outer-400 replacement heuristic.

    Replacing on any other fault would discard a working credential on every
    network blip, mint a record for each one, and hide the real error behind a
    second attempt.
    """
    transport, live, _rebuilt, tokens, keyed_with = _refusing_transport(
        monkeypatch,
        [TransportError("network_error", "connection reset")],
    )

    with pytest.raises(HTTPTransportConnectionError):
        await transport.list_libraries()

    assert tokens.refusals == 0
    assert len(live.calls) == 1
    assert keyed_with == [], "the tunnels were rebuilt for a fault that was not the credential"


# ---------------------------------------------------------------------------
# A retired tunnel stops holding its credential
# ---------------------------------------------------------------------------


_EXPIRED_TOKEN = PlatformToken(
    psk_id=b"\x33" * 64,
    psk=b"dualeai_" + b"2" * 64,
    valid_until=datetime(2020, 1, 1, tzinfo=timezone.utc),
)
"""An expired credential that must not be retained by an idle retired client."""


class _SwitchableTokens:
    """A credential owner whose held token the test replaces by hand.

    `PlatformTokenProvider` decides when a token is replaced, and its bands are
    proved in `test_platform_token_rotation.py`. What matters here is only what
    the transport does with the tunnels the replaced token leaves behind.
    """

    def __init__(self, token: PlatformToken) -> None:
        self.current = token

    async def get(self) -> PlatformToken:
        return self.current

    def invalidate(self) -> None:
        """Part of the owner interface `_refuse_held_token` calls; no case here refuses."""
        self.current = _REPLACEMENT_TOKEN


@asynccontextmanager
async def _real_tunnels(token: PlatformToken) -> AsyncIterator[tuple[HTTPTransport, _SwitchableTokens]]:
    """A connected transport whose tunnels are real `HPKEClientSession`s.

    Nothing reaches the network: a `DiscoveredEndpoint` fetches the service key
    only when a request needs it, and these cases send none. Real sessions are
    the point — the credential whose residency is under test is the one
    `hpke_http` binds at construction, not one a stub could pretend to hold.
    """
    transport = HTTPTransport("https://api.example.test", _TOKEN, _TENANT)
    resources = AsyncExitStack()
    tokens = _SwitchableTokens(token)
    try:
        transport.__dict__.update(
            _resources=resources,
            _tokens=tokens,
            _bridge_source=await resources.enter_async_context(
                DiscoveredEndpoint("https://api.example.test/http-bridge/v1/hpke")
            ),
            _library_source=await resources.enter_async_context(
                DiscoveredEndpoint("https://api.example.test/libraries/v1/hpke")
            ),
        )
        await transport._bind_tunnels()
        yield transport, tokens
    finally:
        await transport.disconnect()
        await resources.aclose()


@pytest.mark.unit
@pytest.mark.parametrize("token", [_EXPIRED_TOKEN, _STUB_TOKEN])
async def test_idle_retired_tunnels_close_at_replacement(token: PlatformToken) -> None:
    """A replaced client with no active response has no remaining lifetime."""
    async with _real_tunnels(token) as (transport, tokens):
        retired = [transport._bridge_session, transport._library_session]
        assert all(session is not None for session in retired)

        tokens.current = _REPLACEMENT_TOKEN
        await transport._bind_tunnels()

        assert transport._bridge_session not in retired, "the tunnels were not re-keyed at all"
        for session in retired:
            assert session is not None
            assert session.closed, "the retired tunnel was left open"


@dataclass
class _PausedResponse:
    started: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass
class _LifetimeEndpoint:
    transport: HTTPTransport
    tokens: _SwitchableTokens
    sessions: list[HPKEClientSession]
    streams: dict[str, _PausedResponse] = field(default_factory=dict)
    finite: _PausedResponse | None = None


@pytest.fixture
async def lifetime_endpoint(  # noqa: PLR0915  # One loopback TLS owner includes protocol handling and teardown.
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[_LifetimeEndpoint]:
    """Pause real protected responses; credential scheduling is tested separately."""
    keypair = generate_key_pair()
    recipient_id = b"lifetime-key"
    server = Server(keypair.private_key, recipient_id)
    ca = trustme.CA()
    server_ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ca.issue_cert("localhost").configure_cert(server_ssl)
    client_ssl = ssl.create_default_context()
    ca.configure_trust(client_ssl)
    tokens = _SwitchableTokens(_STUB_TOKEN)
    sessions: list[HPKEClientSession] = []

    def client(source: DiscoveredEndpoint, psk: bytes, psk_id: bytes) -> HPKEClientSession:
        session = HPKEClientSession(source, psk, psk_id)
        sessions.append(session)
        return session

    monkeypatch.setattr(transport_module, "PlatformTokenProvider", lambda *_args: tokens)
    monkeypatch.setattr(transport_module, "HPKEClientSession", client)
    monkeypatch.setattr(transport_module, "make_connector", lambda: aiohttp.TCPConnector(ssl=client_ssl))

    async def endpoint(request: web.Request) -> web.StreamResponse:
        if request.method == "GET":
            return web.Response(
                body=encode_key_record(recipient_id, server.public_key, 60), content_type=KEY_MEDIA_TYPE
            )
        raw = await request.read()
        length = server.stream_start_length(raw)
        assert length is not None
        preparsed = server.preparse_stream(raw[:length])
        token = _STUB_TOKEN if preparsed.psk_id == _STUB_TOKEN.psk_id else _REPLACEMENT_TOKEN
        opened = preparsed.authenticate(token.psk).admit(accepted=True)
        try:
            offset = length
            while offset < len(raw):
                consumed, _record = opened.feed(raw, offset)
                assert consumed > 0
                offset += consumed
            right = opened.finish_eof()
            try:
                if request.path == "/libraries/v1/hpke":
                    pending, harness.finite = harness.finite, None
                    if pending is not None:
                        pending.started.set()
                        await pending.release.wait()
                    body = right.protect_response(Response(200, (), b'{"libraries": []}'))
                    return web.Response(body=body, content_type=RESPONSE_MEDIA_TYPE)
                paused = harness.streams[opened.head.path.rsplit("/", 1)[-1]]
                sealer, first = right.into_sealer(200, (Header("content-type", "text/event-stream"),))
                response = web.StreamResponse(headers={"Content-Type": RESPONSE_MEDIA_TYPE})
                try:
                    await response.prepare(request)
                    await response.write(first)
                    await response.write(sealer.seal_sse_block(_delta_block("1:1", "first")))
                    paused.started.set()
                    await paused.release.wait()
                    await response.write(sealer.seal_sse_block(_completed_block("1:2")))
                    await response.write(sealer.finish())
                    await response.write_eof()
                except ConnectionResetError:
                    # Iterator close and cancellation intentionally disconnect the peer.
                    pass
                finally:
                    sealer.close()
                return response
            finally:
                right.close()
        finally:
            opened.close()

    app = web.Application()
    app.router.add_route("*", "/http-bridge/v1/hpke", endpoint)
    app.router.add_route("*", "/libraries/v1/hpke", endpoint)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=server_ssl)
    await site.start()
    transport = HTTPTransport(f"https://localhost:{runner.addresses[0][1]}", _TOKEN, _TENANT)
    harness = _LifetimeEndpoint(transport, tokens, sessions)
    try:
        await transport.connect()
        yield harness
    finally:
        for paused in harness.streams.values():
            paused.release.set()
        if harness.finite is not None:
            harness.finite.release.set()
        await transport.disconnect()
        await runner.cleanup()
        server.close()


@pytest.mark.integration
@pytest.mark.timeout(10)
@pytest.mark.parametrize("finish", ["complete", "close", "cancel"])
async def test_retired_tunnels_keep_admitted_streams_until_the_last_response_finishes(
    lifetime_endpoint: _LifetimeEndpoint, finish: str
) -> None:
    """Replacement and expiry do not revoke a stream that was already admitted."""
    harness = lifetime_endpoint
    with freeze_time("2098-12-31T23:59:00Z", real_asyncio=True) as clock:
        first, second = _PausedResponse(), _PausedResponse()
        harness.streams.update(first=first, second=second)
        stream = harness.transport.run_task("first", _task_request())
        other = harness.transport.run_task("second", _task_request())
        async with AsyncExitStack() as cleanup:
            cleanup.push_async_callback(stream.aclose)
            cleanup.push_async_callback(other.aclose)
            assert (await anext(stream)).id == "1:1"
            assert (await anext(other)).id == "1:1"
            retired = list(harness.sessions)
            harness.tokens.current = _REPLACEMENT_TOKEN._replace(valid_until=datetime(2100, 1, 1, tzinfo=timezone.utc))
            assert (await harness.transport.list_libraries()).libraries == []
            clock.move_to("2099-01-01T00:00:01Z")
            assert (await harness.transport.list_libraries()).libraries == []

            first.release.set()
            assert [event.id async for event in stream] == ["1:2"]
            assert [session.closed for session in retired] == [False, False]
            if finish == "complete":
                second.release.set()
                assert [event.id async for event in other] == ["1:2"]
            elif finish == "close":
                await other.aclose()
            else:
                waiting = asyncio.Event()

                async def consume() -> None:
                    waiting.set()
                    await anext(other)

                pending = asyncio.create_task(consume())
                await waiting.wait()
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
            assert [session.closed for session in retired] == [True, True]
            assert harness.transport.is_connected


@pytest.mark.integration
@pytest.mark.timeout(10)
@pytest.mark.parametrize("cancel", [False, True])
async def test_retired_tunnels_close_after_a_pending_finite_request_settles(
    lifetime_endpoint: _LifetimeEndpoint, cancel: bool
) -> None:
    harness = lifetime_endpoint
    response = _PausedResponse()
    harness.finite = response
    pending = asyncio.create_task(harness.transport.list_libraries())
    try:
        await asyncio.wait_for(response.started.wait(), timeout=5)
        retired = list(harness.sessions)
        harness.tokens.current = _REPLACEMENT_TOKEN
        assert (await harness.transport.list_libraries()).libraries == []
        assert [session.closed for session in retired] == [False, False]
        if cancel:
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        else:
            response.release.set()
            assert (await pending).libraries == []
        assert [session.closed for session in retired] == [True, True]
        assert harness.transport.is_connected
    finally:
        response.release.set()
        if not pending.done():
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending


# ---------------------------------------------------------------------------
# An ambiguous outer 400 buys at most one issuance per floor
# ---------------------------------------------------------------------------


class _RefusingSession:
    """A tunnel that answers every request with the ambiguous outer 400."""

    def __init__(self, *, arrivals: int = 1) -> None:
        self.calls = 0
        self.closed = False
        self._arrivals = arrivals
        self._all_here = asyncio.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        self.closed = True
        return False

    async def request(self, *_args: object, **_kwargs: object) -> HPKEResponse:
        self.calls += 1
        if self.calls >= self._arrivals:
            self._all_here.set()
        # Holds every caller until the whole burst has been refused, so the
        # concurrent case below is really concurrent: with no await here the
        # stubs would run one after another and never overlap.
        await self._all_here.wait()
        raise _refused_credential()


class _MonotonicClock:
    """A settable reading for `http_transport.monotonic`."""

    def __init__(self) -> None:
        self.at = 0.0

    def __call__(self) -> float:
        return self.at


def _rebuilding_transport(
    monkeypatch: pytest.MonkeyPatch,
    library: object,
    rebuilds: list[object],
) -> tuple[HTTPTransport, list[bytes], _HeldToken]:
    """A connected transport that hands out `rebuilds` as its tunnels are re-keyed."""
    transport = HTTPTransport("https://api.example.test", _TOKEN, _TENANT)
    tokens = _as_connected(transport, bridge=_FiniteSession([]), library=library)
    transport.__dict__.update(_bridge_source=object(), _library_source=object())

    keyed_with: list[bytes] = []
    queue = list(rebuilds)

    def _rebuild(_source: object, _psk: bytes, psk_id: bytes) -> object:
        keyed_with.append(psk_id)
        return queue.pop(0)

    monkeypatch.setattr(transport_module, "HPKEClientSession", _rebuild)
    return transport, keyed_with, tokens


@pytest.mark.unit
async def test_a_repeated_non_credential_400_does_not_mint_a_token_per_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The outer 400 is ambiguous, so acting on it has to be bounded by time.

    `hpke_http`'s FastAPI middleware answers a malformed envelope, an over-limit
    body and a recipient key the client no longer shares with the same 400 and
    the byte-identical `b"invalid protected request"` a withdrawn credential
    gets, so nothing on the wire tells them apart. Without a floor a caller
    posting one oversized body per request burned one issuance, one record at
    the issuer and one full tunnel rebuild per request, in a loop no new token
    could break.
    """
    clock = _MonotonicClock()
    monkeypatch.setattr(transport_module, "monotonic", clock)
    transport, keyed_with, tokens = _rebuilding_transport(
        monkeypatch,
        _RefusingSession(),
        [_FiniteSession([]), _RefusingSession(), _FiniteSession([]), _RefusingSession()],
    )

    # The refusal policy permits one replacement per minute. Use elapsed times
    # from that contract so a shorter production interval cannot move the oracle.
    for elapsed in (0.0, 1.0, 30.0, 59.999):
        clock.at = elapsed
        with pytest.raises(HTTPTransportConnectionError, match="Protected request failed"):
            await transport.list_libraries()

        assert tokens.refusals == 1, f"the ambiguous 400 minted another platform token after {elapsed}s"
        assert keyed_with == [_REPLACEMENT_TOKEN.psk_id] * 2, "the tunnels were rebuilt more than once"

    # A FLOOR, NOT A LATCH. A credential genuinely withdrawn later must still be
    # replaced, so the next refusal past the floor reissues exactly once more.
    clock.at = 60.0
    with pytest.raises(HTTPTransportConnectionError, match="Protected request failed"):
        await transport.list_libraries()

    assert tokens.refusals == 2
    assert len(keyed_with) == 4


@pytest.mark.unit
async def test_a_burst_refused_on_one_withdrawn_token_finishes_on_one_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The floor bounds issuance, never recovery.

    Eight requests in flight when a credential is withdrawn are all refused on
    it. Only the first may reissue; the other seven retry anyway, and the retry
    runs on the tunnel that one replacement already re-keyed. Raising inside the
    floor instead would fail seven requests a valid credential could serve.
    """
    monkeypatch.setattr(transport_module, "monotonic", _MonotonicClock())
    transport, keyed_with, tokens = _rebuilding_transport(
        monkeypatch,
        _RefusingSession(arrivals=8),
        [_FiniteSession([]), _FiniteSession([_response(200, {"libraries": []})] * 8)],
    )

    results = await asyncio.gather(*(transport.list_libraries() for _ in range(8)))

    assert [result.libraries for result in results] == [[]] * 8
    assert tokens.refusals == 1, "a burst on one withdrawn token asked for more than one replacement"
    assert keyed_with == [_REPLACEMENT_TOKEN.psk_id] * 2


# ---------------------------------------------------------------------------
# One transient fault, one behaviour
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_a_finite_request_survives_one_expired_discovery_lease() -> None:
    """A stream retries this fault, so a finite call must not hard-fail on it.

    `hpke_http` raises `discovery_expired` when a cached key record's lifetime
    ends between the check and the POST it was checked for — a window the next
    attempt refetches its way out of, which is why
    `test_task_replays_pre_start_post_but_never_retries_tampering` already pins
    the stream side. The fault never touches the credential: the lease caches a
    service's own HPKE key, which has nothing to do with the token we hold.
    """
    expired = TransportError("discovery_expired", "key lifetime ended before protected POST")
    transport, session = await _with_session([expired, _response(200, {"libraries": []})])

    assert (await transport.list_libraries()).libraries == []
    assert [call.method for call in session.calls] == ["GET", "GET"]
    tokens = transport._tokens
    assert isinstance(tokens, _HeldToken)
    assert tokens.refusals == 0, "an expired lease was mistaken for a refused credential"

    # One further attempt, not a loop: a lease that keeps ending reaches the caller.
    transport, session = await _with_session([expired, expired, expired])
    with pytest.raises(HTTPTransportConnectionError, match="Protected request failed"):
        await transport.list_libraries()
    assert len(session.calls) == 2
