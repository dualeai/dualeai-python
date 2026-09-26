"""SDK-owned hpke-http/3 behavior with synthetic credentials and loopback TLS."""

import asyncio
import hashlib
import json
import ssl
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import UUID
from zlib import crc32

import aiohttp
import pytest
import trustme
from aiohttp import web
from hpke_http.middleware._discovery import KEY_MEDIA_TYPE, encode_key_record
from hpke_http.middleware.aiohttp import DiscoveredEndpoint, HPKEResponse
from hpke_http.protocol import Header, ProtocolError, Response, Server, generate_key_pair
from hpke_http.transport import RESPONSE_MEDIA_TYPE, TransportError
from opentelemetry.sdk.trace import TracerProvider
from tenacity import wait_none
from yarl import URL

import dualeai.events.http_transport as transport_module
from dualeai import DualeAISDK
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
_BASE = f"https://api.example.test/libraries/v1/hpke/tenants/{_TENANT}"
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

    async def request(self, method, url, *, headers=None, params=None, json=None):
        self.calls.append(_Call(method, url, dict(headers or {}), dict(params) if params else None, json))
        result = self.responses.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


async def _with_session(
    responses: Sequence[HPKEResponse | BaseException], *, library: bool = True
) -> tuple[HTTPTransport, _FiniteSession]:
    transport = HTTPTransport("https://api.example.test", _TOKEN, _TENANT)
    session = _FiniteSession(responses)
    other = _FiniteSession([])
    transport.__dict__["_bridge_session"] = other if library else session
    transport.__dict__["_library_session"] = session if library else other
    return transport, session


@pytest.mark.unit
def test_gateway_base_cannot_include_an_operation_path() -> None:
    with pytest.raises(ValueError, match="HTTPS Gateway base URL"):
        HTTPTransport("https://api.example.test/hpke", _TOKEN, _TENANT)


@pytest.mark.unit
async def test_public_library_routes_and_bearer_stay_in_protected_logical_requests() -> None:
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
        ("POST", f"/libraries/v1/hpke/tenants/{_TENANT}"),
        ("GET", f"/libraries/v1/hpke/tenants/{_TENANT}"),
        ("GET", f"/libraries/v1/hpke/tenants/{_TENANT}/{_LIBRARY_ID}"),
        ("PATCH", f"/libraries/v1/hpke/tenants/{_TENANT}/{_LIBRARY_ID}"),
        ("DELETE", f"/libraries/v1/hpke/tenants/{_TENANT}/{_LIBRARY_ID}"),
        ("GET", f"/libraries/v1/hpke/tenants/{_TENANT}/{_LIBRARY_ID}/documents"),
        ("GET", f"/libraries/v1/hpke/tenants/{_TENANT}/{_LIBRARY_ID}/documents/{_DOCUMENT_ID}"),
        ("DELETE", f"/libraries/v1/hpke/tenants/{_TENANT}/{_LIBRARY_ID}/documents/{_DOCUMENT_ID}"),
        ("POST", f"/libraries/v1/hpke/tenants/{_TENANT}/document-uploads"),
        ("POST", f"/libraries/v1/hpke/tenants/{_TENANT}/{_LIBRARY_ID}/documents"),
    ]
    assert all(call.headers["Authorization"] == f"Bearer {_TOKEN}" for call in session.calls)
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
        "/http-bridge/v1/hpke/agent/registration",
        "/http-bridge/v1/hpke/agent/heartbeat",
        "/http-bridge/v1/hpke/agent/deregistration",
        "/http-bridge/v1/hpke/tasks/task-stop-me-123456/stop",
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


def _with_stream_session(results: list[_StreamReply | BaseException]) -> tuple[HTTPTransport, _StreamSession]:
    transport = HTTPTransport("https://api.example.test", _TOKEN)
    session = _StreamSession(results)
    transport.__dict__["_bridge_session"] = session
    transport.__dict__["_library_session"] = _FiniteSession([])
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
    transport, session = _with_stream_session([_StreamReply([_completed_block()])])

    assert [event.data.type async for event in transport.run_task("task-wire-fields", request)] == ["task.completed"]

    [call] = session.calls
    assert (call.method, urlsplit(call.url).path) == ("POST", "/http-bridge/v1/hpke/tasks/task-wire-fields")
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
    transport, session = _with_stream_session([_StreamReply([_completed_block()]), _StreamReply([_completed_block()])])

    assert [event.data.type async for event in transport.run_task("child-task", continuation)] == ["task.completed"]
    assert [
        event.data.type async for event in transport.submit_tool_results("child-task", results, last_event_id="42:3")
    ] == ["task.completed"]

    assert [(call.method, urlsplit(call.url).path) for call in session.calls] == [
        ("POST", "/http-bridge/v1/hpke/tasks/child-task"),
        ("POST", "/http-bridge/v1/hpke/tasks/child-task"),
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
    transport, session = _with_stream_session([_StreamReply([json.dumps(problem).encode()], status=422, mode="finite")])
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
    transport, session = _with_stream_session([_StreamReply([_completed_block()])])
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
    transport, session = _with_stream_session([first, second])
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
    assert all(urlsplit(call.url).path == "/http-bridge/v1/hpke/tasks/task-1" for call in session.calls)
    assert session.calls[1].headers["Last-Event-ID"] == "5:1"
    assert "Authorization" not in session.calls[0].headers
    assert session.calls[0].headers["Accept"] == "text/event-stream"
    assert first.closed and second.closed


@pytest.mark.unit
@pytest.mark.parametrize("discovery_error", ["discovery_network", "discovery_expired"])
async def test_task_replays_pre_start_post_but_never_retries_tampering(monkeypatch, discovery_error: str) -> None:
    monkeypatch.setattr(transport_module, "wait_exponential_jitter", lambda **_kwargs: wait_none())
    transport, session = _with_stream_session(
        [
            TransportError(discovery_error, "synthetic pre-start discovery failure"),
            _StreamReply([_completed_block()]),
        ]
    )
    assert len([event async for event in transport.run_task("task-1", _task_request())]) == 1
    assert [call.method for call in session.calls] == ["POST", "POST"]

    transport, session = _with_stream_session([ProtocolError("authentication_failed", "synthetic bad tag")])
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
    transport, session = _with_stream_session([first, second])
    events = [event async for event in transport.run_task("task-1", _task_request())]
    assert [event.id for event in events] == ["9:1", "9:2"]
    assert [call.method for call in session.calls] == ["POST", "GET"]
    assert session.calls[1].headers["Last-Event-ID"] == "9:1"

    first = _StreamReply([_delta_block("10:1", "first"), ProtocolError("malformed_envelope", "missing END")])
    second = _StreamReply([_completed_block("10:2")])
    transport, session = _with_stream_session([first, second])
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
    transport, _ = _with_stream_session([reply])
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
    """Real adapter reuses each service key while preserving inner auth and live SSE."""
    keypair = generate_key_pair()
    recipient_id = b"synthetic-key-1"
    server = Server(keypair.private_key, recipient_id)
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
        assert preparsed.psk_id == hashlib.sha512(_TOKEN.encode()).digest()
        opened = preparsed.authenticate(_TOKEN.encode()).admit(accepted=True)
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
                if logical.path.startswith("/libraries/"):
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

    app = web.Application()
    app.router.add_route("*", "/http-bridge/v1/hpke", endpoint)
    app.router.add_route("*", "/libraries/v1/hpke", endpoint)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=server_ssl)
    await site.start()
    port = runner.addresses[0][1]
    real_source = DiscoveredEndpoint
    connectors: list[aiohttp.TCPConnector] = []

    def local_source(endpoint: str, *, timeout: aiohttp.ClientTimeout) -> DiscoveredEndpoint:
        connector = aiohttp.TCPConnector(ssl=client_ssl)
        connectors.append(connector)
        return real_source(endpoint, connector=connector, timeout=timeout)

    monkeypatch.setattr(transport_module, "DiscoveredEndpoint", local_source)
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
    assert len(connectors) == 2
    assert all(connector.closed for connector in connectors)
    assert calls == [
        ("GET", "/libraries/v1/hpke"),
        ("POST", "/libraries/v1/hpke"),
        ("POST", "/libraries/v1/hpke"),
        ("GET", "/http-bridge/v1/hpke"),
        ("POST", "/http-bridge/v1/hpke"),
        ("POST", "/http-bridge/v1/hpke"),
    ]
    assert [item[:2] for item in observed] == [
        ("GET", f"/libraries/v1/hpke/tenants/{_TENANT}"),
        ("GET", f"/libraries/v1/hpke/tenants/{_TENANT}"),
        ("POST", "/http-bridge/v1/hpke/tasks/task-1"),
        ("POST", "/http-bridge/v1/hpke/tasks/task-stop-me-123456/stop"),
    ]
    assert observed[0][2]["authorization"] == f"Bearer {_TOKEN}"
    assert observed[1][2]["authorization"] == f"Bearer {_TOKEN}"
    assert "authorization" not in observed[2][2]
    assert observed[2][2]["accept"] == "text/event-stream"
    assert observed[2][2]["traceparent"].split("-")[1] == f"{trace_id:032x}"
    assert json.loads(observed[2][3])["action_prompt"] == "Synthetic action"
    assert all("authorization" not in {name.lower() for name in headers} for headers in outer_headers)
    assert all("traceparent" not in {name.lower() for name in headers} for headers in outer_headers)
