"""Unit tests for attachment handling (RFC-113).

Layered with ``test_attachments_s3.py`` (the end-to-end moto+aiohttp suite):

- ``test_attachments_s3.py`` exercises the full upload pipeline against real
  moto S3 — that file covers byte-correctness, sha-correctness,
  Library document creation, varied content, size-only upload body, etc.
- This file is the focused unit layer. It covers what moto cannot easily
  observe:
    - request shape of ``prepare_attachments`` / ``PreparedAttachment``;
    - the streaming generator ``_stream_file_part`` against real files;
    - the wire-level retry/Content-Length contract of
      ``upload_attachment_to_library`` against the S3 presigned URL — using
      ``aioresponses`` (HTTP-layer boundary mock), not internal collaborator
      mocks.

The Library transport here is a tiny concrete protocol stub. That is the public
Protocol boundary the SDK function takes — not an internal SDK collaborator.
``MagicMock`` would be inappropriate; this thin stub records call shape directly.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import aiohttp
import pytest
from aiohttp import web
from aioresponses import aioresponses
from aioresponses.core import RequestCall
from pydantic import ValidationError

from dualeai import DualeAISDK, LibraryUploadError
from dualeai.attachments import (
    PreparedAttachment,
    prepare_attachments,
    upload_attachment_to_library,
    upload_attachments_to_library,
)
from dualeai.events.client import CloudEventsClient
from dualeai.events.transport import BridgeTaskRequest, HTTPTransportProtocol
from dualeai.messages import AgentConfig
from dualeai.models.bridge import BridgeSSEEvent
from dualeai.models.library import (
    LibraryCreateRequest,
    LibraryDocumentCreateOperationRequest,
    LibraryDocumentCreateResponse,
    LibraryDocumentUploadPart,
    LibraryDocumentUploadRequest,
    LibraryDocumentUploadResponse,
    LibraryWithRevision,
)

_S3_BASE = "https://s3.example.com"
_TENANT_ID = "tenant-test"
_RECORDED_LIBRARY_ID = "018f35a8-2e90-7f2c-a6bb-9426f9c1f301"
_RECORDED_DOCUMENT_ID = "018f35a8-2e90-7f2c-a6bb-9426f9c1f302"
_UPLOAD_CONTENT_LENGTH_ID = "018f35a8-2e90-7f2c-a6bb-9426f9c1f310"
_UPLOAD_RETRY_ID = "018f35a8-2e90-7f2c-a6bb-9426f9c1f311"
_UPLOAD_FAILURE_ID = "018f35a8-2e90-7f2c-a6bb-9426f9c1f312"
_UPLOAD_SORT_ID = "018f35a8-2e90-7f2c-a6bb-9426f9c1f313"
_UPLOAD_LIBRARY_RESOLUTION_ID = "018f35a8-2e90-7f2c-a6bb-9426f9c1f314"
_CALL_SCOPED_LIBRARY_ID = "018f35a8-2e90-7f2c-a6bb-9426f9c1f315"


# ============================================================================
# prepare_attachments
# ============================================================================


@pytest.mark.unit
class TestPrepareAttachments:
    """Test prepare_attachments metadata extraction."""

    def test_single_file(self, make_file):
        """Normal: single file produces correct metadata."""
        path = make_file("contract.pdf", size=2048)
        result = prepare_attachments([(path, "Client contract")])

        assert len(result) == 1
        att = result[0]
        assert att.filename == "contract.pdf"
        assert att.description == "Client contract"
        assert att.size == 2048
        assert att.path == path
        # ``key`` must be a syntactically valid UUID v7 — not just non-empty.
        # The leading time-ordered 48 bits + version nibble distinguish v7
        # from v4, so a regressed factory returning ``uuid4`` would fail this.
        parsed = UUID(att.key)
        assert parsed.version == 7

    def test_multiple_files(self, make_file):
        """Normal: multiple files, each gets unique key."""
        paths = [
            (make_file("a.pdf", size=100), "File A"),
            (make_file("b.docx", size=200), "File B"),
            (make_file("c.xlsx", size=300), "File C"),
        ]
        result = prepare_attachments(paths)

        assert len(result) == 3
        keys = {att.key for att in result}
        assert len(keys) == 3  # All unique

    def test_file_not_found(self, tmp_path: Path):
        """Out of bounds: nonexistent file raises FileNotFoundError."""
        missing = tmp_path / "nonexistent.pdf"
        with pytest.raises(FileNotFoundError, match="nonexistent.pdf"):
            prepare_attachments([(missing, "Ghost file")])

    def test_one_byte_file(self, make_file):
        """Edge: 1-byte file is accepted."""
        path = make_file("tiny.bin", size=1)
        result = prepare_attachments([(path, "Tiny")])
        assert result[0].size == 1

    def test_empty_file_is_rejected(self, tmp_path: Path):
        path = tmp_path / "empty.txt"
        path.touch()

        with pytest.raises(ValueError, match="empty"):
            prepare_attachments([(path, "Empty")])

    def test_directory_is_rejected(self, tmp_path: Path):
        with pytest.raises(ValueError, match="regular file"):
            prepare_attachments([(tmp_path, "Directory")])

    def test_public_filename_and_description_limits_are_checked(self, make_file):
        path = make_file("valid.txt", size=1)

        with pytest.raises(ValidationError):
            PreparedAttachment(
                key="attachment-key",
                path=path,
                filename="x" * 501,
                description="description",
                size=1,
            )

        with pytest.raises(ValidationError):
            prepare_attachments([(path, "x" * 501)])

    def test_empty_list(self):
        """Edge: empty input produces empty output."""
        assert prepare_attachments([]) == []

    def test_frozen_model(self, make_file):
        """PreparedAttachment is immutable."""
        path = make_file("test.bin", size=10)
        att = prepare_attachments([(path, "desc")])[0]
        field_name = "size"
        with pytest.raises(ValidationError):
            setattr(att, field_name, 999)


# ============================================================================
# upload_attachment_to_library — S3 wire-level contract via aioresponses
# ============================================================================


class _RecordingLibraryTransport(HTTPTransportProtocol):
    """Tiny concrete transport stub for the two methods the uploader calls.

    Not a ``MagicMock``: this is the Protocol boundary the SDK function
    accepts. It records the requests so tests can assert on the Library
    side of the contract without touching aiohttp at all. The non-upload
    Protocol methods are unused on this code path and raise
    ``NotImplementedError`` rather than silently returning sentinel values.

    Inherits ``HTTPTransportProtocol`` explicitly so static checkers
    accept it without ``cast`` at every call site.
    """

    def __init__(
        self,
        upload_response: LibraryDocumentUploadResponse,
        *,
        library_id: str = _RECORDED_LIBRARY_ID,
    ) -> None:
        self._upload_response = upload_response
        self._library_id = library_id
        self.library_create_requests: list[LibraryCreateRequest] = []
        self.library_paths: list[str] = []
        self.upload_calls: list[LibraryDocumentUploadRequest] = []
        self.document_create_calls: list[LibraryDocumentCreateOperationRequest] = []

    @property
    def is_connected(self) -> bool:  # pragma: no cover - protocol stub
        return True

    async def connect(self) -> None:  # pragma: no cover - protocol stub
        return None

    async def disconnect(self) -> None:  # pragma: no cover - protocol stub
        return None

    def run_task(  # pragma: no cover - protocol stub
        self,
        task_id: str,
        request: BridgeTaskRequest,
    ) -> AsyncIterator[BridgeSSEEvent]:
        raise NotImplementedError("_RecordingLibraryTransport only stubs document upload")

    async def create_library(self, request: LibraryCreateRequest) -> LibraryWithRevision:
        self.library_create_requests.append(request)
        self.library_paths.append(request.path)
        now = datetime.now(timezone.utc)
        return LibraryWithRevision(
            id=UUID(self._library_id),
            path=request.path,
            tags={},
            updated_by="agent:test",
            updated_at=now,
            created_at=now,
            deleted_at=None,
        )

    async def create_document_upload(self, request: LibraryDocumentUploadRequest) -> LibraryDocumentUploadResponse:
        self.upload_calls.append(request)
        return self._upload_response

    async def create_library_document(
        self,
        request: LibraryDocumentCreateOperationRequest,
    ) -> LibraryDocumentCreateResponse:
        self.document_create_calls.append(request)
        return LibraryDocumentCreateResponse(
            document_id=UUID(_RECORDED_DOCUMENT_ID),
            library_id=request.library_id,
            status="queued",
            location=(f"/v1/tenants/{_TENANT_ID}/{request.library_id}/documents/{_RECORDED_DOCUMENT_ID}"),
        )


class _SizeRoutedLibraryTransport(_RecordingLibraryTransport):
    """Return one presigned URL selected by the requested file size."""

    def __init__(
        self,
        upload_urls_by_size: dict[int, str],
        *,
        document_created: asyncio.Event | None = None,
    ) -> None:
        first_size, first_url = next(iter(upload_urls_by_size.items()))
        super().__init__(
            LibraryDocumentUploadResponse(
                upload_id=UUID(int=1),
                parts=[
                    LibraryDocumentUploadPart.model_validate(
                        {
                            "part_number": 1,
                            "upload_url": first_url,
                            "offset": 0,
                            "length": first_size,
                        }
                    )
                ],
                expires_at=datetime.now(timezone.utc),
                parts_expires_at=datetime.now(timezone.utc),
            )
        )
        self._upload_urls_by_size = upload_urls_by_size
        self._next_upload_id = 2
        self._document_created = document_created

    async def create_document_upload(
        self,
        request: LibraryDocumentUploadRequest,
    ) -> LibraryDocumentUploadResponse:
        self.upload_calls.append(request)
        upload_id = UUID(int=self._next_upload_id)
        self._next_upload_id += 1
        now = datetime.now(timezone.utc)
        return LibraryDocumentUploadResponse(
            upload_id=upload_id,
            parts=[
                LibraryDocumentUploadPart.model_validate(
                    {
                        "part_number": 1,
                        "upload_url": self._upload_urls_by_size[request.size_bytes],
                        "offset": 0,
                        "length": request.size_bytes,
                    }
                )
            ],
            expires_at=now,
            parts_expires_at=now,
        )

    async def create_library_document(
        self,
        request: LibraryDocumentCreateOperationRequest,
    ) -> LibraryDocumentCreateResponse:
        response = await super().create_library_document(request)
        if self._document_created is not None:
            self._document_created.set()
        return response


class _ObjectStoreProbe:
    """Loopback object-store boundary for batch scheduling tests."""

    def __init__(
        self,
        *,
        started_target: int,
        failure_name: str | None = None,
        failure_gate: asyncio.Event | None = None,
        block_successes: bool = False,
    ) -> None:
        self.started_target = started_target
        self.failure_name = failure_name
        self.failure_gate = failure_gate
        self.block_successes = block_successes
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.started_names: set[str] = set()
        self.active = 0
        self.max_active = 0

    async def put(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.started_names.add(name)
        if len(self.started_names) >= self.started_target:
            self.started.set()
        try:
            if name == self.failure_name:
                if self.failure_gate is None:
                    await self.started.wait()
                else:
                    await self.failure_gate.wait()
                return web.Response(status=403, text="Access denied")
            if self.block_successes:
                await self.release.wait()
            return web.Response(status=200, headers={"ETag": f'"etag-{name}"'})
        finally:
            self.active -= 1


async def _use_attachment_transport(sdk: DualeAISDK, transport: HTTPTransportProtocol) -> None:
    events_client = CloudEventsClient(sdk.config, transport=transport)
    await events_client.connect()
    sdk.events_client = events_client


def _put_calls_for(mocked: aioresponses, url: str) -> list[RequestCall]:
    """Filter aioresponses' recorded PUT calls for a given URL.

    ``aioresponses.requests`` is typed as ``dict | None`` upstream; this
    helper asserts the dict is present and returns the matching call list
    so callers don't have to repeat the None check.
    """
    requests = mocked.requests
    assert requests is not None
    return [
        call
        for (method, recorded_url), calls in requests.items()
        if method == "PUT" and str(recorded_url) == url
        for call in calls
    ]


def _request_headers(call: RequestCall) -> dict[str, str]:
    """Extract Headers from an aioresponses RequestCall as a plain dict."""
    headers = call.kwargs.get("headers", {})
    if not isinstance(headers, dict):
        raise AssertionError("aioresponses RequestCall headers must be a dict")
    return {str(k): str(v) for k, v in headers.items()}


def _upload_response(upload_id: str, parts: list[dict[str, object]]) -> LibraryDocumentUploadResponse:
    now = datetime.now(timezone.utc)
    return LibraryDocumentUploadResponse(
        upload_id=UUID(upload_id),
        parts=[LibraryDocumentUploadPart.model_validate(p) for p in parts],
        expires_at=now,
        parts_expires_at=now,
    )


async def _upload_single(
    transport: HTTPTransportProtocol,
    s3_session: aiohttp.ClientSession,
    attachment: PreparedAttachment,
    library_id: str,
) -> LibraryDocumentCreateResponse:
    """Run one real upload with a batch-level part semaphore."""
    return await upload_attachment_to_library(
        transport,
        s3_session,
        attachment,
        library_id,
        asyncio.Semaphore(8),
    )


@pytest.mark.unit
class TestUploadAttachmentToLibraryWire:
    """S3 PUT wire contract: Content-Length header, retry policy, ordering.

    Uses ``aioresponses`` to intercept S3 PUTs (HTTP boundary). The Library
    transport is a tiny recording stub at the Protocol boundary. Byte-
    correctness and hashing are covered against real moto S3 in
    ``test_attachments_s3.py``.
    """

    async def test_content_length_header_set_on_each_put(self, make_attachment):
        """Each PUT carries an explicit ``Content-Length`` header.

        S3 rejects chunked Transfer-Encoding on presigned URLs, so the SDK
        MUST set Content-Length itself. ``aiohttp`` does not auto-add it
        for async-iterable bodies — verifying this guards the contract.
        """
        att = make_attachment(size=500)
        transport = _RecordingLibraryTransport(
            _upload_response(
                _UPLOAD_CONTENT_LENGTH_ID,
                [
                    {"part_number": 1, "upload_url": f"{_S3_BASE}/p1", "offset": 0, "length": 300},
                    {"part_number": 2, "upload_url": f"{_S3_BASE}/p2", "offset": 300, "length": 200},
                ],
            ),
        )

        with aioresponses() as mocked:
            mocked.put(f"{_S3_BASE}/p1", status=200, headers={"ETag": '"e1"'})
            mocked.put(f"{_S3_BASE}/p2", status=200, headers={"ETag": '"e2"'})

            async with aiohttp.ClientSession() as s3_session:
                await _upload_single(transport, s3_session, att, _RECORDED_LIBRARY_ID)

            p1_calls = _put_calls_for(mocked, f"{_S3_BASE}/p1")
            p2_calls = _put_calls_for(mocked, f"{_S3_BASE}/p2")
            assert len(p1_calls) == 1
            assert len(p2_calls) == 1
            assert _request_headers(p1_calls[0])["Content-Length"] == "300"
            assert _request_headers(p2_calls[0])["Content-Length"] == "200"

    async def test_retry_on_503_then_succeeds(self, make_attachment):
        """Tenacity retries on S3 503 and succeeds on the second attempt.

        ``aioresponses`` queues 503 then 200 for the same URL — the
        production tenacity policy must drain both responses.
        """
        att = make_attachment(size=100)
        transport = _RecordingLibraryTransport(
            _upload_response(
                _UPLOAD_RETRY_ID,
                [{"part_number": 1, "upload_url": f"{_S3_BASE}/retry", "offset": 0, "length": 100}],
            ),
        )

        with aioresponses() as mocked:
            mocked.put(f"{_S3_BASE}/retry", status=503, body="Service Unavailable")
            mocked.put(f"{_S3_BASE}/retry", status=200, headers={"ETag": '"after-retry"'})

            async with aiohttp.ClientSession() as s3_session:
                await _upload_single(transport, s3_session, att, _RECORDED_LIBRARY_ID)

            put_calls = _put_calls_for(mocked, f"{_S3_BASE}/retry")
            assert len(put_calls) == 2  # 503 then 200

        # Library document creation carries the etag from the successful retry.
        assert len(transport.document_create_calls) == 1
        create_request = transport.document_create_calls[0]
        assert create_request.document.parts[0].etag == '"after-retry"'

    async def test_no_retry_on_403_permanent_failure(self, make_attachment):
        """4xx must short-circuit and identify the failed attachment and part.

        ``aioresponses`` queues two 403s; production must consume only
        the first (no retry) and raise immediately.
        """
        att = make_attachment(size=100)
        transport = _RecordingLibraryTransport(
            _upload_response(
                _UPLOAD_FAILURE_ID,
                [{"part_number": 1, "upload_url": f"{_S3_BASE}/forbidden", "offset": 0, "length": 100}],
            ),
        )

        with aioresponses() as mocked:
            mocked.put(f"{_S3_BASE}/forbidden", status=403, body="Access Denied")
            # Second response should NEVER be consumed.
            mocked.put(f"{_S3_BASE}/forbidden", status=200, headers={"ETag": '"never"'})

            async with aiohttp.ClientSession() as s3_session:
                with pytest.raises(LibraryUploadError, match="HTTP 403") as raised:
                    await _upload_single(transport, s3_session, att, _RECORDED_LIBRARY_ID)

            put_calls = _put_calls_for(mocked, f"{_S3_BASE}/forbidden")
            assert len(put_calls) == 1  # No retry on 4xx

        # Document creation must not be invoked on terminal failure.
        assert transport.document_create_calls == []
        assert raised.value.attachment_key == att.key
        assert raised.value.part_number == 1

    async def test_missing_etag_is_a_typed_upload_error(self, make_attachment):
        att = make_attachment(size=1)
        transport = _RecordingLibraryTransport(
            _upload_response(
                _UPLOAD_FAILURE_ID,
                [{"part_number": 1, "upload_url": f"{_S3_BASE}/missing-etag", "offset": 0, "length": 1}],
            )
        )

        with aioresponses() as mocked:
            mocked.put(f"{_S3_BASE}/missing-etag", status=200)
            async with aiohttp.ClientSession() as s3_session:
                with pytest.raises(LibraryUploadError, match="omitted ETag") as raised:
                    await _upload_single(transport, s3_session, att, _RECORDED_LIBRARY_ID)

        assert raised.value.attachment_key == att.key
        assert raised.value.part_number == 1

    async def test_parts_sorted_in_document_create_request(self, make_attachment):
        """Parts in document creation are sorted by ``part_number``.

        Library may return parts in arbitrary order; the uploader uploads in
        parallel; the SDK must sort by part_number before document creation.
        """
        att = make_attachment(size=200)
        transport = _RecordingLibraryTransport(
            _upload_response(
                _UPLOAD_SORT_ID,
                [
                    # Intentionally out of order.
                    {"part_number": 2, "upload_url": f"{_S3_BASE}/p2", "offset": 100, "length": 100},
                    {"part_number": 1, "upload_url": f"{_S3_BASE}/p1", "offset": 0, "length": 100},
                ],
            ),
        )

        with aioresponses() as mocked:
            mocked.put(f"{_S3_BASE}/p1", status=200, headers={"ETag": '"etag-p1"'})
            mocked.put(f"{_S3_BASE}/p2", status=200, headers={"ETag": '"etag-p2"'})

            async with aiohttp.ClientSession() as s3_session:
                await _upload_single(transport, s3_session, att, _RECORDED_LIBRARY_ID)

        assert len(transport.document_create_calls) == 1
        create_request = transport.document_create_calls[0]
        part_numbers = [part.part_number for part in create_request.document.parts]
        assert part_numbers == [1, 2]
        etags = [part.etag for part in create_request.document.parts]
        assert etags == ['"etag-p1"', '"etag-p2"']

    async def test_document_create_uses_explicit_library_id(self, make_attachment):
        """Document creation targets the caller's stable Library id."""
        att = make_attachment(size=50)
        transport = _RecordingLibraryTransport(
            _upload_response(
                _UPLOAD_LIBRARY_RESOLUTION_ID,
                [{"part_number": 1, "upload_url": f"{_S3_BASE}/library-id", "offset": 0, "length": 50}],
            ),
            library_id=_CALL_SCOPED_LIBRARY_ID,
        )

        with aioresponses() as mocked:
            mocked.put(f"{_S3_BASE}/library-id", status=200, headers={"ETag": '"etag-lib"'})

            async with aiohttp.ClientSession() as s3_session:
                await _upload_single(transport, s3_session, att, _CALL_SCOPED_LIBRARY_ID)

        assert transport.library_paths == []
        assert len(transport.upload_calls) == 1
        assert transport.upload_calls[0].model_dump(mode="json") == {"size_bytes": 50}
        assert len(transport.document_create_calls) == 1
        create_request = transport.document_create_calls[0]
        assert str(create_request.library_id) == _CALL_SCOPED_LIBRARY_ID
        create_body = create_request.document.model_dump(mode="json")
        assert create_body["upload_id"] == _UPLOAD_LIBRARY_RESOLUTION_ID
        assert "library_path" not in create_body


@pytest.mark.unit
async def test_upload_batch_bounds_real_object_store_requests(aiohttp_server, make_attachment) -> None:
    probe = _ObjectStoreProbe(started_target=8, block_successes=True)
    application = web.Application()
    application.router.add_put("/{name}", probe.put)
    server = await aiohttp_server(application)
    attachments = [
        make_attachment(name=f"file-{index}.bin", size=index + 1).model_copy(update={"key": f"key-{index}"})
        for index in range(10)
    ]
    upload_urls = {attachment.size: str(server.make_url(f"/{attachment.filename}")) for attachment in attachments}
    transport = _SizeRoutedLibraryTransport(upload_urls)

    batch = asyncio.create_task(upload_attachments_to_library(transport, attachments, _RECORDED_LIBRARY_ID))
    try:
        await asyncio.wait_for(probe.started.wait(), timeout=1.0)
        assert probe.max_active == 8
    finally:
        probe.release.set()
    receipts = await batch

    assert list(receipts) == [attachment.key for attachment in attachments]


@pytest.mark.unit
async def test_upload_batch_cancels_a_blocked_object_store_request(aiohttp_server, make_attachment) -> None:
    probe = _ObjectStoreProbe(started_target=2, failure_name="failed.bin", block_successes=True)
    application = web.Application()
    application.router.add_put("/{name}", probe.put)
    server = await aiohttp_server(application)
    failed = make_attachment(name="failed.bin", size=1)
    sibling = make_attachment(name="sibling.bin", size=2).model_copy(update={"key": "sibling-key"})
    transport = _SizeRoutedLibraryTransport(
        {
            failed.size: str(server.make_url("/failed.bin")),
            sibling.size: str(server.make_url("/sibling.bin")),
        }
    )

    try:
        with pytest.raises(LibraryUploadError, match="HTTP 403") as raised:
            await asyncio.wait_for(
                upload_attachments_to_library(transport, [failed, sibling], _RECORDED_LIBRARY_ID),
                timeout=1.0,
            )
    finally:
        probe.release.set()

    assert raised.value.attachment_key == failed.key
    assert transport.document_create_calls == []


@pytest.mark.unit
async def test_upload_batch_reports_receipts_completed_before_real_http_failure(
    aiohttp_server,
    make_attachment,
) -> None:
    document_created = asyncio.Event()
    probe = _ObjectStoreProbe(
        started_target=2,
        failure_name="failed.bin",
        failure_gate=document_created,
    )
    application = web.Application()
    application.router.add_put("/{name}", probe.put)
    server = await aiohttp_server(application)
    completed = make_attachment(name="completed.bin", size=1)
    failed = make_attachment(name="failed.bin", size=2).model_copy(update={"key": "failed-key"})
    transport = _SizeRoutedLibraryTransport(
        {
            completed.size: str(server.make_url("/completed.bin")),
            failed.size: str(server.make_url("/failed.bin")),
        },
        document_created=document_created,
    )

    with pytest.raises(LibraryUploadError, match="HTTP 403") as raised:
        await upload_attachments_to_library(transport, [completed, failed], _RECORDED_LIBRARY_ID)

    assert raised.value.attachment_key == failed.key
    assert list(raised.value.completed_receipts) == [completed.key]
    assert transport.document_create_calls[0].document.filename == completed.filename


@pytest.mark.unit
async def test_upload_batch_rejects_duplicate_keys_and_changed_files(make_attachment) -> None:
    attachment = make_attachment(name="mutable.bin", size=1)
    transport = _RecordingLibraryTransport(
        _upload_response(
            _UPLOAD_FAILURE_ID,
            [{"part_number": 1, "upload_url": f"{_S3_BASE}/unused", "offset": 0, "length": 1}],
        )
    )

    with pytest.raises(ValueError, match="Duplicate attachment key"):
        await upload_attachments_to_library(transport, [attachment, attachment], _RECORDED_LIBRARY_ID)

    attachment.path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed after preparation"):
        await upload_attachments_to_library(transport, [attachment], _RECORDED_LIBRARY_ID)

    assert transport.upload_calls == []


# ============================================================================
# DualeAISDK.upload_attachments — keyword-only agent_id + resolver
# ============================================================================


def _noop_agent_func(*_args: object, **_kwargs: object) -> None:
    """No-op callable used to populate SDK agent registry in tests."""
    return


@pytest.mark.unit
class TestUploadAttachmentsAgentResolution:
    """``DualeAISDK.upload_attachments`` resolves agent_id from public calls.

    Contract (RFC-113 call-scope path):

    - ``agent_id`` is keyword-only and optional.
    - One registered agent → resolved silently.
    - Zero or multiple registered agents → ``ValueError`` — call site MUST
      pass an explicit ``agent_id`` because the call-scope library path
      encodes one identifier.

    These tests exercise the public SDK method and record the transport
    boundary path. They avoid pinning private resolver helpers.
    """

    async def test_resolves_single_registered_agent(self, minimal_mock_sdk: DualeAISDK, make_attachment) -> None:
        """One registered agent → upload path uses that agent id."""
        minimal_mock_sdk.register_agent("solo-agent", _noop_agent_func, AgentConfig(name="solo-agent"))
        att = make_attachment(size=25)
        transport = _RecordingLibraryTransport(
            _upload_response(
                _UPLOAD_LIBRARY_RESOLUTION_ID,
                [{"part_number": 1, "upload_url": f"{_S3_BASE}/solo-agent", "offset": 0, "length": 25}],
            )
        )
        await _use_attachment_transport(minimal_mock_sdk, transport)

        with aioresponses() as mocked:
            mocked.put(f"{_S3_BASE}/solo-agent", status=200, headers={"ETag": '"etag-solo"'})

            receipts = await minimal_mock_sdk.upload_attachments("task-single-agent", [att])

        assert transport.library_paths == ["agent/solo-agent/task/task-single-agent"]
        assert len(transport.library_create_requests) == 1
        assert transport.library_create_requests[0].model_dump(mode="json") == {
            "path": "agent/solo-agent/task/task-single-agent"
        }
        assert list(receipts) == [att.key]
        assert str(receipts[att.key].document_id) == _RECORDED_DOCUMENT_ID

    async def test_explicit_agent_id_wins_over_registry(self, minimal_mock_sdk: DualeAISDK, make_attachment) -> None:
        """Explicit ``agent_id`` overrides the registered agent."""
        minimal_mock_sdk.register_agent("default-agent", _noop_agent_func, AgentConfig(name="default-agent"))
        att = make_attachment(size=30)
        transport = _RecordingLibraryTransport(
            _upload_response(
                _UPLOAD_LIBRARY_RESOLUTION_ID,
                [{"part_number": 1, "upload_url": f"{_S3_BASE}/override-agent", "offset": 0, "length": 30}],
            )
        )
        await _use_attachment_transport(minimal_mock_sdk, transport)

        with aioresponses() as mocked:
            mocked.put(f"{_S3_BASE}/override-agent", status=200, headers={"ETag": '"etag-override"'})

            await minimal_mock_sdk.upload_attachments("task-explicit-agent", [att], agent_id="override-agent")

        assert transport.library_paths == ["agent/override-agent/task/task-explicit-agent"]

    async def test_raises_when_no_agents_registered(self, minimal_mock_sdk: DualeAISDK, make_attachment) -> None:
        """Zero agents + no explicit id → ValueError. No silent fallback."""
        att = make_attachment(size=10)

        with pytest.raises(ValueError, match="no agents registered"):
            await minimal_mock_sdk.upload_attachments("task-missing-agent", [att])

    async def test_raises_when_multiple_agents_registered(self, minimal_mock_sdk: DualeAISDK, make_attachment) -> None:
        """Multiple agents + no explicit id → ValueError. Path is single-agent."""
        minimal_mock_sdk.register_agent("agent-a", _noop_agent_func, AgentConfig(name="agent-a"))
        minimal_mock_sdk.register_agent("agent-b", _noop_agent_func, AgentConfig(name="agent-b"))
        att = make_attachment(size=10)

        with pytest.raises(ValueError, match="multiple agents are registered"):
            await minimal_mock_sdk.upload_attachments("task-many-agents", [att])

    def test_upload_attachments_agent_id_is_keyword_only(self) -> None:
        """``agent_id`` is keyword-only on ``DualeAISDK.upload_attachments``.

        Inspecting the signature documents the contract without a runtime
        type-check violation. Guards against silent regression to a
        positional-friendly shape which would break the single-agent
        ergonomic shortcut.
        """
        sig = inspect.signature(DualeAISDK.upload_attachments)
        agent_param = sig.parameters["agent_id"]
        assert agent_param.kind is inspect.Parameter.KEYWORD_ONLY
        assert agent_param.default is None

    async def test_upload_attachments_accepts_empty_attachment_list(self, minimal_mock_sdk: DualeAISDK) -> None:
        """Empty attachment list short-circuits without network access."""
        minimal_mock_sdk.register_agent("solo-agent", _noop_agent_func, AgentConfig(name="solo-agent"))

        assert await minimal_mock_sdk.upload_attachments("task-empty", []) == {}
        assert await minimal_mock_sdk.upload_attachments("task-empty", [], agent_id="solo-agent") == {}
