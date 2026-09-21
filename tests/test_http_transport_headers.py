"""Test real HTTPTransport sends correct headers (RFC 8878, hpke-http v1.3.0).

This test file uses aioresponses to mock ONLY the HTTP network layer,
allowing the REAL HTTPTransport code to execute. This is critical for
verifying that headers like Accept-Encoding are actually set.

DO NOT use MockHTTPTransport here - it completely replaces HTTPTransport
and would not test the real implementation.

hpke-http v1.3.0 changes:
- NO Authorization header (pure PSK auth, avoids MITM token exposure)
- psk_id sent via X-HPKE-PSK-ID header by HPKEClientSession
"""

import base64
import hashlib
import json
from collections.abc import AsyncIterable, Mapping
from datetime import datetime, timezone
from uuid import UUID

import aiohttp
import pytest
from aioresponses import aioresponses
from aioresponses.core import CallbackResult, RequestCall
from hpke_http.constants import KemId
from hpke_http.core import RequestDecryptor, ResponseEncryptor, SSEEncryptor
from hpke_http.primitives import generate_keypair
from yarl import URL

from dualeai.constants import HTTPDefaults
from dualeai.events.http_transport import (
    HTTPTransport,
    HTTPTransportConnectionError,
    HTTPTransportResponseError,
)
from dualeai.models.bridge import (
    AgentDeregistrationMessage,
    AgentHeartbeatMessage,
    AgentHeartbeatResponse,
    AgentRegistrationMessage,
    BridgeTaskContinueRequest,
    BridgeTaskCreateRequest,
    BridgeToolResultsRequest,
    BridgeToolResultSuccess,
    HeartbeatStatus,
)
from dualeai.models.response_format import PredefinedResponseFormat

# Valid SSE payload with typed discriminator
_TASK_COMPLETED_SSE = (
    b": crc=e89db6c3\n"
    b"id: 1:1\n"
    b"event: task.completed\n"
    b'data: {"type": "task.completed", "timestamp": "2025-12-11T12:00:00Z",'
    b' "result": {"completion": "done", "cache_hit": false}}\n'
    b"\n"
)

# Pre-generate HPKE keypair for test discovery endpoint
_TEST_SK, _TEST_PK = generate_keypair()
_TEST_PK_B64URL = base64.urlsafe_b64encode(_TEST_PK).decode().rstrip("=")
_HPKE_DISCOVERY_RESPONSE = json.dumps(
    {
        "version": 1,
        "keys": [{"kem_id": hex(KemId.DHKEM_X25519_HKDF_SHA256.value), "public_key": _TEST_PK_B64URL}],
    }
).encode()

_BASE_URL = "https://api.test.duale.ai"
_DISCOVERY_URL = f"{_BASE_URL}/.well-known/hpke-keys"
_TASK_URL = f"{_BASE_URL}/v1/tasks/test-task-id"
_REGISTRATION_URL = f"{_BASE_URL}/v1/agent/registration"
_HEARTBEAT_URL = f"{_BASE_URL}/v1/agent/heartbeat"
_DEREGISTRATION_URL = f"{_BASE_URL}/v1/agent/deregistration"
# The API-token digest is the HPKE ``psk_id`` wire identity. Pin an independently
# reviewed literal so an algorithm change or token-prefix rewrite fails before the
# SDK presents a different identity to the service.
#
# The sample is padded past 32 bytes because a shorter one cannot be this SDK's PSK:
# ``hpke-http`` refuses it (RFC 9180 §5.1), and a sample the transport cannot carry is a
# sample that proves nothing about the transport.
PINNED_TOKEN = "dualeai_contract-pinned-token-padded-past-the-32-byte-psk-floor"
PINNED_DIGEST = (
    "0dd594a2b0c1b0582316b0bfeeb3153231f8c03ef94d2ab5dfd916eb224b362d"
    "9ae5e19744dffa681f7201fe551880ccf076152b5fa41eb0f15c3b5d977fdfce"
)

_TOKEN = PINNED_TOKEN
_PSK = _TOKEN.encode()
# READ OFF THE PINNED LITERAL, never recomputed. Every callback below decrypts with this
# value, and RFC 9180 binds ``psk_id`` into the key schedule — so a transport that derived
# its identity any other way (SHA-256, or the prefix stripped) cannot decrypt here at all.
_PSK_ID = bytes.fromhex(PINNED_DIGEST)
_PROCESS_ID = UUID("0192d52a-7000-7000-8000-000000000010")
_PUBLICATION_ID = UUID("0192d52a-7000-7000-8000-000000000011")


def _create_task_request() -> BridgeTaskCreateRequest:
    """Return the typed create body used by transport-only tests."""
    return BridgeTaskCreateRequest(
        type="create",
        action_prompt="Test action",
        deadline=datetime(2025, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
    )


def _get_request_call(mocked: aioresponses, method: str, url: str) -> RequestCall:
    """Helper to get request call from aioresponses.

    aioresponses uses (method, URL object) as key, not string.
    """
    request_key = (method, URL(url))
    requests = mocked.requests
    if not isinstance(requests, dict):
        raise AssertionError("aioresponses did not expose a request registry")

    calls = requests.get(request_key)
    if not isinstance(calls, list) or not calls:
        raise AssertionError(f"Expected request to {url}, got {list(requests.keys())}")

    request_call = calls[0]
    if not isinstance(request_call, RequestCall):
        raise AssertionError(f"Expected RequestCall, got {type(request_call).__name__}")
    return request_call


def _mock_hpke_discovery(mocked: aioresponses) -> None:
    """Mock HPKE key discovery endpoint so HPKEClientSession can initialize."""
    mocked.get(
        _DISCOVERY_URL,
        status=200,
        body=_HPKE_DISCOVERY_RESPONSE,
        headers={"Content-Type": "application/json", "Cache-Control": "public, max-age=86400"},
    )


def _request_headers(value: object) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        raise AssertionError("aioresponses callback did not receive request headers")
    return {str(key): str(item) for key, item in value.items()}


async def _request_body(value: object) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, str):
        return value.encode()
    if isinstance(value, AsyncIterable):
        chunks: list[bytes] = []
        async for chunk in value:
            if not isinstance(chunk, bytes):
                raise AssertionError(f"Expected encrypted bytes chunk, got {type(chunk).__name__}")
            chunks.append(chunk)
        return b"".join(chunks)
    raise AssertionError(f"Expected encrypted request body, got {type(value).__name__}")


def _make_encrypted_sse_response(sse_bytes: bytes):
    """Build an aioresponses callback that HPKE-decrypts the request and returns
    ``sse_bytes`` as an HPKE-encrypted SSE stream (per-request context)."""

    async def _callback(_url: URL, **kwargs: object) -> CallbackResult:
        headers = _request_headers(kwargs.get("headers"))
        body = await _request_body(kwargs.get("data"))
        decryptor = RequestDecryptor(headers, _TEST_SK, _PSK, _PSK_ID)
        decryptor.decrypt_all(body)
        encryptor = SSEEncryptor(decryptor.context)
        return CallbackResult(
            status=200,
            body=encryptor.encrypt(sse_bytes),
            content_type="text/event-stream",
            headers=encryptor.get_headers(),
        )

    return _callback


def _make_capturing_encrypted_sse_response(
    sse_bytes: bytes,
    captured_bodies: list[object],
):
    """Decrypt and record the real HPKE request before returning encrypted SSE."""

    async def _callback(_url: URL, **kwargs: object) -> CallbackResult:
        headers = _request_headers(kwargs.get("headers"))
        body = await _request_body(kwargs.get("data"))
        decryptor = RequestDecryptor(headers, _TEST_SK, _PSK, _PSK_ID)
        plaintext = decryptor.decrypt_all(body)
        captured_bodies.append(json.loads(plaintext))
        encryptor = SSEEncryptor(decryptor.context)
        return CallbackResult(
            status=200,
            body=encryptor.encrypt(sse_bytes),
            content_type="text/event-stream",
            headers=encryptor.get_headers(),
        )

    return _callback


async def _encrypted_sse_response(_url: URL, **kwargs: object) -> CallbackResult:
    return await _make_encrypted_sse_response(_TASK_COMPLETED_SSE)(_url, **kwargs)


def _make_encrypted_response(
    response_body: bytes,
    *,
    status: int = 202,
    content_type: str = "application/json",
    captured_bodies: list[object] | None = None,
):
    """Decrypt one real HPKE request and return an encrypted standard response."""

    async def _callback(_url: URL, **kwargs: object) -> CallbackResult:
        headers = _request_headers(kwargs.get("headers"))
        body = await _request_body(kwargs.get("data"))
        decryptor = RequestDecryptor(headers, _TEST_SK, _PSK, _PSK_ID)
        plaintext = decryptor.decrypt_all(body)
        if captured_bodies is not None:
            captured_bodies.append(json.loads(plaintext))
        encryptor = ResponseEncryptor(decryptor.context)
        return CallbackResult(
            status=status,
            body=encryptor.encrypt_all(response_body),
            content_type=content_type,
            headers=encryptor.get_headers(),
        )

    return _callback


def _registration_request() -> AgentRegistrationMessage:
    now = datetime(2026, 8, 10, 12, tzinfo=timezone.utc)
    return AgentRegistrationMessage(
        agent_id="agent_test",
        process_id=_PROCESS_ID,
        tools=[],
        config_hash="a" * 64,
        manifest_publication_id=_PUBLICATION_ID,
        time=now,
        sdk_version="0.1.0",
    )


def _heartbeat_request() -> AgentHeartbeatMessage:
    now = datetime(2026, 8, 10, 12, 0, 5, tzinfo=timezone.utc)
    return AgentHeartbeatMessage(
        agent_id="agent_test",
        process_id=_PROCESS_ID,
        status=HeartbeatStatus.healthy,
        time=now,
        config_hash="a" * 64,
        heartbeat_publication_id=_PUBLICATION_ID,
    )


def _deregistration_request() -> AgentDeregistrationMessage:
    now = datetime(2026, 8, 10, 12, 0, 10, tzinfo=timezone.utc)
    return AgentDeregistrationMessage(
        agent_id="agent_test",
        process_id=_PROCESS_ID,
        deregistration_publication_id=_PUBLICATION_ID,
        reason="shutdown",
        time=now,
    )


@pytest.mark.unit
async def test_agent_lifecycle_uses_typed_hpke_requests_and_raw_heartbeat_json() -> None:
    transport = HTTPTransport(endpoint=_BASE_URL, token=_TOKEN)
    registration = _registration_request()
    heartbeat = _heartbeat_request()
    deregistration = _deregistration_request()
    captured_bodies: list[object] = []
    heartbeat_body = (
        AgentHeartbeatResponse(
            server_received_at=heartbeat.time,
            server_sent_at=heartbeat.time,
            client_sent_at=heartbeat.time,
        )
        .model_dump_json()
        .encode()
    )

    with aioresponses() as mocked:
        _mock_hpke_discovery(mocked)
        await transport.connect()
        mocked.post(
            _REGISTRATION_URL,
            callback=_make_encrypted_response(b"", captured_bodies=captured_bodies),
        )
        mocked.post(
            _HEARTBEAT_URL,
            callback=_make_encrypted_response(
                heartbeat_body,
                content_type="text/plain",
                captured_bodies=captured_bodies,
            ),
        )
        mocked.post(
            _DEREGISTRATION_URL,
            callback=_make_encrypted_response(b"", captured_bodies=captured_bodies),
        )
        try:
            await transport.register_agent_manifest(registration)
            response = await transport.send_agent_heartbeat(heartbeat)
            await transport.deregister_agent_process(deregistration)
        finally:
            await transport.disconnect()

    assert response.client_sent_at == heartbeat.time
    assert captured_bodies == [
        json.loads(registration.model_dump_json()),
        json.loads(heartbeat.model_dump_json()),
        json.loads(deregistration.model_dump_json()),
    ]


@pytest.mark.unit
async def test_agent_lifecycle_requires_exact_accepted_status_and_empty_ack_body() -> None:
    transport = HTTPTransport(endpoint=_BASE_URL, token=_TOKEN)
    with aioresponses() as mocked:
        _mock_hpke_discovery(mocked)
        await transport.connect()
        mocked.post(
            _REGISTRATION_URL,
            callback=_make_encrypted_response(b"", status=200),
        )
        mocked.post(
            _DEREGISTRATION_URL,
            callback=_make_encrypted_response(b"{}"),
        )
        try:
            with pytest.raises(
                HTTPTransportConnectionError,
                match="accepted response returned HTTP 200",
            ):
                await transport.register_agent_manifest(_registration_request())
            with pytest.raises(
                HTTPTransportConnectionError,
                match="accepted response contained an unexpected body",
            ):
                await transport.deregister_agent_process(_deregistration_request())
        finally:
            await transport.disconnect()


@pytest.mark.unit
async def test_agent_heartbeat_rejects_wrong_success_status_and_invalid_response_model() -> None:
    transport = HTTPTransport(endpoint=_BASE_URL, token=_TOKEN)
    with aioresponses() as mocked:
        _mock_hpke_discovery(mocked)
        await transport.connect()
        mocked.post(
            _HEARTBEAT_URL,
            callback=_make_encrypted_response(b"{}", status=200),
        )
        mocked.post(
            _HEARTBEAT_URL,
            callback=_make_encrypted_response(b"{}"),
        )
        try:
            with pytest.raises(
                HTTPTransportConnectionError,
                match="heartbeat response returned HTTP 200",
            ):
                await transport.send_agent_heartbeat(_heartbeat_request())
            with pytest.raises(
                HTTPTransportConnectionError,
                match="Heartbeat response did not match schema",
            ):
                await transport.send_agent_heartbeat(_heartbeat_request())
        finally:
            await transport.disconnect()


# The second event deliberately mismatches. The transport must resume after the
# first event's exact composite cursor, not perform cursor arithmetic.
_BAD_CHECKSUM_SSE = (
    b": crc=8263d8a5\n"
    b"id: 5:1\n"
    b"event: content.delta\n"
    b'data: {"type": "content.delta", "timestamp": "2025-12-11T12:00:00Z", "delta": "partial"}\n'
    b"\n"
    b": crc=00000000\n"
    b"id: 5:2\n"
    b"event: content.delta\n"
    b'data: {"type": "content.delta", "timestamp": "2025-12-11T12:00:00Z", "delta": "partial"}\n'
    b"\n"
)


@pytest.mark.unit
async def test_accept_encoding_header_sent():
    """Verify REAL HTTPTransport sends Accept-Encoding: zstd, gzip, deflate."""
    transport = HTTPTransport(
        endpoint=_BASE_URL,
        token=_TOKEN,
    )

    with aioresponses() as mocked:
        _mock_hpke_discovery(mocked)
        await transport.connect()

        try:
            mocked.post(
                _TASK_URL,
                callback=_encrypted_sse_response,
            )

            # Execute REAL HTTPTransport code
            events = []
            async for event in transport.run_task(
                "test-task-id",
                _create_task_request(),
            ):
                events.append(event)

            # Get request and check headers
            request_call = _get_request_call(mocked, "POST", _TASK_URL)
            headers = request_call.kwargs.get("headers", {})

            # THIS IS THE CRITICAL ASSERTION - verify real code sets Accept-Encoding
            assert "Accept-Encoding" in headers, "Accept-Encoding header not sent"
            assert headers["Accept-Encoding"] == HTTPDefaults.ACCEPT_ENCODING
            assert "zstd" in headers["Accept-Encoding"]
            assert "gzip" in headers["Accept-Encoding"]
            assert "deflate" in headers["Accept-Encoding"]

        finally:
            await transport.disconnect()


@pytest.mark.unit
async def test_traceparent_header_sent_under_active_span():
    """Verify REAL HTTPTransport injects the active span's W3C traceparent on the bridge request.

    Drives run_task through the real HPKEClientSession so this also confirms HPKE
    preserves the traceparent header. RED if inject_trace_context wiring is removed.
    """
    from opentelemetry.sdk.trace import TracerProvider

    tracer = TracerProvider().get_tracer("test")
    transport = HTTPTransport(
        endpoint=_BASE_URL,
        token=_TOKEN,
    )

    with aioresponses() as mocked:
        _mock_hpke_discovery(mocked)
        await transport.connect()

        try:
            mocked.post(
                _TASK_URL,
                callback=_encrypted_sse_response,
            )

            with tracer.start_as_current_span("sdk-task"):
                async for _event in transport.run_task(
                    "test-task-id",
                    _create_task_request(),
                ):
                    pass

            request_call = _get_request_call(mocked, "POST", _TASK_URL)
            headers = request_call.kwargs.get("headers", {})
            assert "traceparent" in headers, "traceparent not injected onto the bridge request"

        finally:
            await transport.disconnect()


@pytest.mark.unit
async def test_accept_header_sent():
    """Verify REAL HTTPTransport sends Accept: text/event-stream."""
    transport = HTTPTransport(
        endpoint=_BASE_URL,
        token=_TOKEN,
    )

    with aioresponses() as mocked:
        _mock_hpke_discovery(mocked)
        await transport.connect()

        try:
            mocked.post(
                _TASK_URL,
                callback=_encrypted_sse_response,
            )

            events = []
            async for event in transport.run_task(
                "test-task-id",
                _create_task_request(),
            ):
                events.append(event)

            request_call = _get_request_call(mocked, "POST", _TASK_URL)
            headers = request_call.kwargs.get("headers", {})

            # Verify Accept header for SSE
            assert "Accept" in headers
            assert headers["Accept"] == HTTPDefaults.ACCEPT_SSE

        finally:
            await transport.disconnect()


@pytest.mark.unit
async def test_psk_id_header_is_the_pinned_cross_tier_token_digest():
    """The identity this SDK presents is SHA-512 of the COMPLETE token, to the byte.

    An SDK that hashes with SHA-256 or strips the ``dualeai_`` prefix presents a
    different identity and cannot authenticate.

    Read off the wire header because it is the public protocol boundary. The
    expectation is the literal above, base64url-encoded the way
    ``hpke-http`` transports a ``psk_id`` (RFC 9180 §5.1) — never a second call to
    ``hashlib`` here, which would agree with any preimage rule this file also adopted.

    The reply is a 4xx rather than an encrypted stream so that a wrong identity fails on
    THIS assertion. Answering with ciphertext would fail earlier and elsewhere: RFC 9180
    binds ``psk_id`` into the key schedule, so the mock's decryption would raise first and
    report a broken cipher instead of the wrong digest.
    """
    transport = HTTPTransport(
        endpoint=_BASE_URL,
        token=PINNED_TOKEN,
    )

    with aioresponses() as mocked:
        _mock_hpke_discovery(mocked)
        await transport.connect()

        try:
            mocked.post(_TASK_URL, status=422, payload={"title": "stop here", "status": 422})

            with pytest.raises(HTTPTransportResponseError):
                _ = [event async for event in transport.run_task("test-task-id", _create_task_request())]

            request_call = _get_request_call(mocked, "POST", _TASK_URL)
            headers = request_call.kwargs.get("headers", {})

            expected = base64.urlsafe_b64encode(bytes.fromhex(PINNED_DIGEST)).rstrip(b"=").decode()
            assert headers["X-HPKE-PSK-ID"] == expected, (
                "the identity this SDK presents is not SHA-512 of the complete token; Profile "
                "resolves the raw token by these exact bytes and would find no row"
            )

        finally:
            await transport.disconnect()


@pytest.mark.unit
def test_the_pinned_digest_is_what_stdlib_sha512_makes_of_the_whole_token():
    """The transcribed literal is a real SHA-512, checked against stdlib and nothing else.

    Without this the two lines above are only self-consistent: the header test would pass
    on any pair somebody wrote down together.
    """
    assert hashlib.sha512(PINNED_TOKEN.encode()).hexdigest() == PINNED_DIGEST
    assert bytes.fromhex(PINNED_DIGEST) == _PSK_ID


@pytest.mark.unit
async def test_no_authorization_header_pure_psk_auth():
    """Verify HTTPTransport does NOT send Authorization header (hpke-http v1.3.0).

    hpke-http v1.3.0 uses pure PSK authentication:
    - psk_id (SHA-512 of token) sent via X-HPKE-PSK-ID header by HPKEClientSession
    - NO Authorization header - avoids MITM token exposure at TLS proxy
    - Server resolves psk_id → raw token via Profile service

    The Authorization header was removed to prevent exposing the token in clear
    HTTP headers (only TLS protects headers, MITM at proxy would see it).
    """
    transport = HTTPTransport(
        endpoint=_BASE_URL,
        token=_TOKEN,
    )

    with aioresponses() as mocked:
        _mock_hpke_discovery(mocked)
        await transport.connect()

        try:
            mocked.post(
                _TASK_URL,
                callback=_encrypted_sse_response,
            )

            events = []
            async for event in transport.run_task(
                "test-task-id",
                _create_task_request(),
            ):
                events.append(event)

            request_call = _get_request_call(mocked, "POST", _TASK_URL)
            headers = request_call.kwargs.get("headers", {})

            # Verify NO Authorization header (pure PSK auth via X-HPKE-PSK-ID)
            assert "Authorization" not in headers, "Authorization header must NOT be sent (pure PSK auth)"

        finally:
            await transport.disconnect()


@pytest.mark.unit
async def test_sse_checksum_mismatch_retries_as_get_with_last_event_id():
    """A checksum retry resumes after the last yielded composite cursor."""
    transport = HTTPTransport(endpoint=_BASE_URL, token=_TOKEN)

    with aioresponses() as mocked:
        _mock_hpke_discovery(mocked)
        await transport.connect()

        try:
            # First POST: 2xx then a bad-checksum event -> SSEChecksumError -> retry.
            mocked.post(_TASK_URL, callback=_make_encrypted_sse_response(_BAD_CHECKSUM_SSE))
            # Retry is a GET (stream_established after the 2xx); it resumes and completes.
            mocked.get(_TASK_URL, callback=_make_encrypted_sse_response(_TASK_COMPLETED_SSE))

            events = [
                e
                async for e in transport.run_task(
                    "test-task-id",
                    _create_task_request(),
                )
            ]

            get_call = _get_request_call(mocked, "GET", _TASK_URL)
            assert get_call.kwargs.get("headers", {}).get("Last-Event-ID") == "5:1"
            assert any(getattr(e.data, "type", None) == "task.completed" for e in events)

        finally:
            await transport.disconnect()


@pytest.mark.unit
async def test_continue_task_sends_exact_child_keyed_encrypted_body():
    """The real HPKE session sends the public child in the URL and parent in JSON."""
    transport = HTTPTransport(endpoint=_BASE_URL, token=_TOKEN)
    request = BridgeTaskContinueRequest(
        type="continue",
        parent_task_id="parent-task-123",
        message="Continue with é漢🙂",
        response_format=PredefinedResponseFormat.json,
        deadline=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
    )
    captured_bodies: list[object] = []

    with aioresponses() as mocked:
        _mock_hpke_discovery(mocked)
        await transport.connect()
        try:
            mocked.post(
                _TASK_URL,
                callback=_make_capturing_encrypted_sse_response(_TASK_COMPLETED_SSE, captured_bodies),
            )

            events = [event async for event in transport.run_task("test-task-id", request)]

            assert captured_bodies == [
                {
                    "type": "continue",
                    "parent_task_id": "parent-task-123",
                    "message": "Continue with é漢🙂",
                    "response_format": "json",
                    "deadline": "2026-08-03T12:00:00Z",
                }
            ]
            assert [event.data.type for event in events] == ["task.completed"]
            assert ("GET", URL(_TASK_URL)) not in (mocked.requests or {})
        finally:
            await transport.disconnect()


@pytest.mark.unit
async def test_submit_tool_results_sends_exact_child_keyed_encrypted_body_and_replay_cursor():
    """The real adapter posts generated tool results and the caller's replay cursor to child C."""
    transport = HTTPTransport(endpoint=_BASE_URL, token=_TOKEN)
    request = BridgeToolResultsRequest(
        type="tool_results",
        tool_results=[
            BridgeToolResultSuccess(
                type="success",
                tool_call_id="call-123456789",
                output={"state": "closed"},
            )
        ],
    )
    captured_bodies: list[object] = []

    with aioresponses() as mocked:
        _mock_hpke_discovery(mocked)
        await transport.connect()
        try:
            mocked.post(
                _TASK_URL,
                callback=_make_capturing_encrypted_sse_response(_TASK_COMPLETED_SSE, captured_bodies),
            )

            events = [
                event
                async for event in transport.submit_tool_results(
                    "test-task-id",
                    request,
                    last_event_id="42:3",
                )
            ]

            assert captured_bodies == [
                {
                    "type": "tool_results",
                    "tool_results": [
                        {
                            "type": "success",
                            "tool_call_id": "call-123456789",
                            "output": {"state": "closed"},
                        }
                    ],
                }
            ]
            post_call = _get_request_call(mocked, "POST", _TASK_URL)
            assert post_call.kwargs.get("headers", {}).get("Last-Event-ID") == "42:3"
            assert [event.data.type for event in events] == ["task.completed"]
            assert ("GET", URL(_TASK_URL)) not in (mocked.requests or {})
        finally:
            await transport.disconnect()


@pytest.mark.unit
async def test_continue_task_retries_midstream_as_get_without_reposting():
    """A continuation switches to child-keyed GET after its POST got a 2xx."""
    transport = HTTPTransport(endpoint=_BASE_URL, token=_TOKEN)
    request = BridgeTaskContinueRequest(
        type="continue",
        parent_task_id="parent-task-123",
        message="Continue",
        deadline=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
    )

    with aioresponses() as mocked:
        _mock_hpke_discovery(mocked)
        await transport.connect()
        try:
            mocked.post(_TASK_URL, callback=_make_encrypted_sse_response(_BAD_CHECKSUM_SSE))
            mocked.get(_TASK_URL, callback=_make_encrypted_sse_response(_TASK_COMPLETED_SSE))

            events = [event async for event in transport.run_task("test-task-id", request)]

            requests = mocked.requests or {}
            assert len(requests[("POST", URL(_TASK_URL))]) == 1
            assert len(requests[("GET", URL(_TASK_URL))]) == 1
            get_call = _get_request_call(mocked, "GET", _TASK_URL)
            assert get_call.kwargs.get("headers", {}).get("Last-Event-ID") == "5:1"
            assert [event.data.type for event in events] == ["content.delta", "task.completed"]
        finally:
            await transport.disconnect()


@pytest.mark.unit
async def test_continue_task_retries_pre_2xx_connection_failure_on_same_child_post():
    """A connection failure before any 2xx retries the same child-keyed POST."""
    transport = HTTPTransport(endpoint=_BASE_URL, token=_TOKEN)
    request = BridgeTaskContinueRequest(
        type="continue",
        parent_task_id="parent-task-123",
        message="Continue",
        deadline=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
    )
    captured_bodies: list[object] = []

    with aioresponses() as mocked:
        _mock_hpke_discovery(mocked)
        await transport.connect()
        try:
            mocked.post(_TASK_URL, exception=aiohttp.ClientConnectionError("connect failed"))
            mocked.post(
                _TASK_URL,
                callback=_make_capturing_encrypted_sse_response(_TASK_COMPLETED_SSE, captured_bodies),
            )

            events = [event async for event in transport.run_task("test-task-id", request)]

            requests = mocked.requests or {}
            assert len(requests[("POST", URL(_TASK_URL))]) == 2
            assert ("GET", URL(_TASK_URL)) not in requests
            assert captured_bodies == [
                {
                    "type": "continue",
                    "parent_task_id": "parent-task-123",
                    "message": "Continue",
                    "deadline": "2026-08-03T12:00:00Z",
                }
            ]
            assert [event.data.type for event in events] == ["task.completed"]
        finally:
            await transport.disconnect()


@pytest.mark.unit
async def test_continue_task_does_not_retry_pre_2xx_422_and_preserves_problem_details():
    """A rejected continuation stays on its single POST and keeps the typed problem."""
    transport = HTTPTransport(endpoint=_BASE_URL, token=_TOKEN)
    request = BridgeTaskContinueRequest(
        type="continue",
        parent_task_id="parent-task-123",
        message="Continue",
        deadline=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
    )
    problem = {
        "title": "Invalid continuation",
        "status": 422,
        "detail": "The parent cannot be continued",
        "error_code": "INVALID_CONTINUATION",
        "retryable": False,
    }

    with aioresponses() as mocked:
        _mock_hpke_discovery(mocked)
        await transport.connect()
        try:
            mocked.post(_TASK_URL, status=422, payload=problem)

            with pytest.raises(HTTPTransportResponseError) as exc_info:
                _ = [event async for event in transport.run_task("test-task-id", request)]

            requests = mocked.requests or {}
            assert len(requests[("POST", URL(_TASK_URL))]) == 1
            assert ("GET", URL(_TASK_URL)) not in requests
            assert exc_info.value.problem_details is not None
            assert exc_info.value.problem_details.error_code == "INVALID_CONTINUATION"
            assert exc_info.value.problem_details.status == 422
            assert exc_info.value.problem_details.retryable is False
        finally:
            await transport.disconnect()
