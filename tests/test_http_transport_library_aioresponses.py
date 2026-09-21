"""Real-session Library tests (aioresponses, not a hand-rolled session stub).

Library calls go over a plain aiohttp.ClientSession (origin base_url + Bearer,
RFC-113). Driving them through aioresponses exercises the REAL session — base_url
join, headers, JSON encode/decode — instead of a fake that could drift.
"""

import asyncio
import json
from uuid import UUID

import aiohttp
import pytest
from aioresponses import aioresponses
from aioresponses.core import RequestCall
from yarl import URL

from dualeai.events.http_transport import (
    HTTPTransport,
    HTTPTransportAuthError,
    HTTPTransportConnectionError,
    HTTPTransportResponseError,
)
from dualeai.exceptions import ConfigurationError
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

_BASE_URL = "https://api.test.duale.ai"
_TOKEN = "dualeai_test_token_12345_padded_to_32bytes"
_TENANT_ID = "tenant-test"
_LIBRARY_ID = "018f35a8-2e90-7f2c-a6bb-9426f9c1f501"
_DOCUMENT_ID = "018f35a8-2e90-7f2c-a6bb-9426f9c1f502"
_UPLOAD_ID = "018f35a8-2e90-7f2c-a6bb-9426f9c1f503"
_DATE = "2026-06-12T10:00:00Z"
_LIBRARY_UUID = UUID(_LIBRARY_ID)
_DOCUMENT_UUID = UUID(_DOCUMENT_ID)

_LIBRARIES = f"{_BASE_URL}/libraries/v1/tenants/{_TENANT_ID}"


def _library_response() -> dict[str, object]:
    return {
        "id": _LIBRARY_ID,
        "path": "agent/agent-test/task/task-test",
        "tags": {},
        "updated_by": "user:test",
        "updated_at": _DATE,
        "created_at": _DATE,
        "deleted_at": None,
    }


def _upload_response() -> dict[str, object]:
    return {
        "upload_id": _UPLOAD_ID,
        "parts": [{"part_number": 1, "upload_url": "https://s3.test/upload/part-1", "offset": 0, "length": 10}],
        "expires_at": _DATE,
        "parts_expires_at": _DATE,
    }


def _document_response(*, status: str = "queued") -> dict[str, object]:
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
        "status": status,
        "progress_pct": None,
        "failure": None,
        "tags": {},
    }


async def _connected_transport(*, tenant_id: str | None = _TENANT_ID) -> HTTPTransport:
    """Real transport with real sessions. connect() is network-free (HPKE lazy)."""
    transport = HTTPTransport(endpoint=_BASE_URL, token=_TOKEN, tenant_id=tenant_id)
    await transport.connect()
    return transport


def _request_call(mocked: aioresponses, method: str, url: str) -> RequestCall:
    requests = mocked.requests
    if not isinstance(requests, dict):
        raise AssertionError("aioresponses did not expose a request registry")
    calls = requests.get((method, URL(url)))
    if not isinstance(calls, list) or not calls:
        raise AssertionError(f"Expected {method} {url}, got {list(requests)}")
    call = calls[0]
    if not isinstance(call, RequestCall):
        raise AssertionError(f"Expected RequestCall, got {type(call).__name__}")
    return call


@pytest.mark.unit
async def test_core_library_crud_uses_real_session() -> None:
    transport = await _connected_transport()
    item_url = f"{_LIBRARIES}/{_LIBRARY_ID}"
    with aioresponses() as mocked:
        mocked.post(_LIBRARIES, status=201, payload=_library_response())
        mocked.get(_LIBRARIES, status=200, payload={"libraries": [_library_response()]})
        mocked.get(item_url, status=200, payload=_library_response())
        mocked.patch(item_url, status=200, payload={**_library_response(), "path": "projects/renamed"})
        mocked.delete(item_url, status=204)
        try:
            created = await transport.create_library(LibraryCreateRequest(path="projects/contracts"))
            libraries = await transport.list_libraries()
            fetched = await transport.get_library(LibraryGetRequest(library_id=_LIBRARY_UUID))
            updated = await transport.update_library(
                LibraryUpdateRequest(
                    library_id=_LIBRARY_UUID,
                    patch=LibraryPatchRequest(path="projects/renamed"),
                )
            )
            await transport.delete_library(LibraryDeleteRequest(library_id=_LIBRARY_UUID))
        finally:
            await transport.disconnect()

    assert str(created.id) == _LIBRARY_ID
    assert [str(library.id) for library in libraries.libraries] == [_LIBRARY_ID]
    assert fetched.path == "agent/agent-test/task/task-test"
    assert updated.path == "projects/renamed"
    create_call = _request_call(mocked, "POST", _LIBRARIES)
    update_call = _request_call(mocked, "PATCH", item_url)
    assert create_call.kwargs["json"] == {"path": "projects/contracts"}
    assert update_call.kwargs["json"] == {"path": "projects/renamed"}
    assert create_call.kwargs["headers"]["Authorization"] == f"Bearer {_TOKEN}"


@pytest.mark.unit
async def test_core_document_reads_and_delete_use_real_session() -> None:
    transport = await _connected_transport()
    documents_url = f"{_LIBRARIES}/{_LIBRARY_ID}/documents"
    document_url = f"{documents_url}/{_DOCUMENT_ID}"
    with aioresponses() as mocked:
        mocked.get(
            f"{documents_url}?limit=100",
            status=200,
            payload={"documents": [_document_response()], "next_cursor": None},
        )
        mocked.get(document_url, status=200, payload=_document_response())
        mocked.delete(document_url, status=204)
        try:
            documents = await transport.list_library_documents(
                LibraryDocumentListRequest(
                    library_id=_LIBRARY_UUID,
                    limit=100,
                    cursor=None,
                )
            )
            document = await transport.get_library_document(
                LibraryDocumentGetRequest(
                    library_id=_LIBRARY_UUID,
                    document_id=_DOCUMENT_UUID,
                )
            )
            await transport.delete_library_document(
                LibraryDocumentDeleteRequest(
                    library_id=_LIBRARY_UUID,
                    document_id=_DOCUMENT_UUID,
                )
            )
        finally:
            await transport.disconnect()

    assert [str(item.document_id) for item in documents.documents] == [_DOCUMENT_ID]
    assert documents.next_cursor is None
    assert str(document.document_id) == _DOCUMENT_ID


@pytest.mark.unit
async def test_update_library_preserves_explicit_empty_tags() -> None:
    transport = await _connected_transport()
    item_url = f"{_LIBRARIES}/{_LIBRARY_ID}"
    with aioresponses() as mocked:
        mocked.patch(item_url, status=200, payload=_library_response())
        try:
            await transport.update_library(
                LibraryUpdateRequest(
                    library_id=_LIBRARY_UUID,
                    patch=LibraryPatchRequest(tags={}),
                )
            )
        finally:
            await transport.disconnect()

    assert _request_call(mocked, "PATCH", item_url).kwargs["json"] == {"tags": {}}


@pytest.mark.unit
async def test_library_connection_failure_maps_to_transport_error() -> None:
    transport = await _connected_transport()
    with aioresponses() as mocked:
        mocked.get(_LIBRARIES, exception=aiohttp.ClientConnectionError("connection lost"))
        try:
            with pytest.raises(HTTPTransportConnectionError) as raised:
                await transport.list_libraries()
        finally:
            await transport.disconnect()

    assert isinstance(raised.value.__cause__, aiohttp.ClientConnectionError)


@pytest.mark.unit
async def test_library_domain_failure_is_not_a_connection_error() -> None:
    transport = await _connected_transport()
    library_url = f"{_LIBRARIES}/{_LIBRARY_ID}"
    with aioresponses() as mocked:
        mocked.get(
            library_url,
            status=410,
            payload={
                "type": "about:blank",
                "title": "Library deleted",
                "status": 410,
                "detail": "The Library is no longer active.",
                "error_code": "LIBRARY_DELETED",
            },
        )
        try:
            with pytest.raises(HTTPTransportResponseError) as raised:
                await transport.get_library(LibraryGetRequest(library_id=_LIBRARY_UUID))
        finally:
            await transport.disconnect()

    assert raised.value.problem_details is not None
    assert raised.value.problem_details.error_code == "LIBRARY_DELETED"


@pytest.mark.unit
async def test_library_timeout_maps_to_transport_error_on_python_310() -> None:
    transport = await _connected_transport()
    with aioresponses() as mocked:
        mocked.get(_LIBRARIES, exception=asyncio.TimeoutError())
        try:
            with pytest.raises(HTTPTransportConnectionError) as raised:
                await transport.list_libraries()
        finally:
            await transport.disconnect()

    assert isinstance(raised.value.__cause__, asyncio.TimeoutError)


@pytest.mark.unit
async def test_library_invalid_json_maps_to_transport_error() -> None:
    transport = await _connected_transport()
    with aioresponses() as mocked:
        mocked.get(
            _LIBRARIES,
            status=200,
            body="{not-json",
            content_type="application/json",
        )
        try:
            with pytest.raises(
                HTTPTransportConnectionError,
                match="Library list response did not match schema",
            ):
                await transport.list_libraries()
        finally:
            await transport.disconnect()


@pytest.mark.unit
async def test_library_response_validation_reads_raw_json_bytes() -> None:
    transport = await _connected_transport()
    with aioresponses() as mocked:
        mocked.get(
            _LIBRARIES,
            status=200,
            body=json.dumps({"libraries": [_library_response()]}),
            content_type="text/plain",
        )
        try:
            result = await transport.list_libraries()
        finally:
            await transport.disconnect()

    assert [str(library.id) for library in result.libraries] == [_LIBRARY_ID]


@pytest.mark.unit
async def test_library_list_rejects_the_removed_bare_array_response() -> None:
    transport = await _connected_transport()
    with aioresponses() as mocked:
        mocked.get(_LIBRARIES, status=200, payload=[_library_response()])
        try:
            with pytest.raises(
                HTTPTransportConnectionError,
                match="Library list response did not match schema",
            ):
                await transport.list_libraries()
        finally:
            await transport.disconnect()


@pytest.mark.unit
async def test_library_delete_requires_exact_no_content_status() -> None:
    transport = await _connected_transport()
    item_url = f"{_LIBRARIES}/{_LIBRARY_ID}"
    with aioresponses() as mocked:
        mocked.delete(item_url, status=200, payload={})
        try:
            with pytest.raises(
                HTTPTransportConnectionError,
                match="no-content response returned HTTP 200",
            ):
                await transport.delete_library(LibraryDeleteRequest(library_id=_LIBRARY_UUID))
        finally:
            await transport.disconnect()


@pytest.mark.unit
async def test_create_document_upload_returns_presigned_parts() -> None:
    transport = await _connected_transport()
    url = f"{_LIBRARIES}/document-uploads"
    with aioresponses() as mocked:
        mocked.post(url, status=200, payload=_upload_response())
        try:
            result = await transport.create_document_upload(LibraryDocumentUploadRequest(size_bytes=10))
        finally:
            await transport.disconnect()

    assert str(result.upload_id) == _UPLOAD_ID
    assert str(result.parts[0].upload_url) == "https://s3.test/upload/part-1"
    assert _request_call(mocked, "POST", url).kwargs["json"] == {"size_bytes": 10}


@pytest.mark.unit
async def test_create_library_document_queues_document() -> None:
    transport = await _connected_transport()
    url = f"{_LIBRARIES}/{_LIBRARY_ID}/documents"
    request = LibraryDocumentCreateRequest(
        upload_id=UUID(_UPLOAD_ID),
        filename="contract.pdf",
        description="Contract",
        content_sha256="a" * 64,
        size_bytes=10,
        parts=[LibraryDocumentCreatePartRef(part_number=1, etag='"etag-1"')],
    )
    with aioresponses() as mocked:
        mocked.post(
            url,
            status=200,
            payload={
                "document_id": _DOCUMENT_ID,
                "library_id": _LIBRARY_ID,
                "status": "queued",
                "location": f"/v1/tenants/{_TENANT_ID}/{_LIBRARY_ID}/documents/{_DOCUMENT_ID}",
            },
        )
        try:
            result = await transport.create_library_document(
                LibraryDocumentCreateOperationRequest(
                    library_id=_LIBRARY_UUID,
                    document=request,
                )
            )
        finally:
            await transport.disconnect()

    assert str(result.document_id) == _DOCUMENT_ID
    assert _request_call(mocked, "POST", url).kwargs["json"] == {
        "upload_id": _UPLOAD_ID,
        "filename": "contract.pdf",
        "description": "Contract",
        "content_sha256": "a" * 64,
        "size_bytes": 10,
        "parts": [{"part_number": 1, "etag": '"etag-1"'}],
    }


@pytest.mark.unit
async def test_library_methods_require_configured_tenant_id() -> None:
    transport = await _connected_transport(tenant_id=None)
    try:
        with pytest.raises(ConfigurationError, match="require tenant_id") as raised:
            await transport.create_library(LibraryCreateRequest(path="projects/contracts"))
    finally:
        await transport.disconnect()

    assert raised.value.config_key == "tenant_id"


@pytest.mark.unit
async def test_document_create_malformed_response_maps_to_transport_error() -> None:
    transport = await _connected_transport()
    url = f"{_LIBRARIES}/{_LIBRARY_ID}/documents"
    request = LibraryDocumentCreateRequest(
        upload_id=UUID(_UPLOAD_ID),
        filename="contract.pdf",
        description="Contract",
        content_sha256="a" * 64,
        size_bytes=10,
        parts=[LibraryDocumentCreatePartRef(part_number=1, etag='"etag-1"')],
    )
    with aioresponses() as mocked:
        mocked.post(
            url,
            status=200,
            payload={"document_id": _DOCUMENT_ID, "library_id": _LIBRARY_ID},
        )
        try:
            with pytest.raises(HTTPTransportConnectionError, match="Document create response did not match schema"):
                await transport.create_library_document(
                    LibraryDocumentCreateOperationRequest(
                        library_id=_LIBRARY_UUID,
                        document=request,
                    )
                )
        finally:
            await transport.disconnect()


@pytest.mark.unit
async def test_library_auth_error_maps_to_typed_error() -> None:
    transport = await _connected_transport()
    with aioresponses() as mocked:
        mocked.post(_LIBRARIES, status=401, payload={"detail": "bad token"})
        try:
            with pytest.raises(HTTPTransportAuthError):
                await transport.create_library(LibraryCreateRequest(path="projects/contracts"))
        finally:
            await transport.disconnect()
