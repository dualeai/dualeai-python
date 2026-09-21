"""Public core Library client behavior."""

import asyncio
from pathlib import Path
from uuid import UUID

import pytest
from aioresponses import aioresponses
from pydantic import ValidationError

import dualeai
from dualeai import DualeAISDK, LibrariesClient
from dualeai.attachments import prepare_attachments
from dualeai.config import DualeAIConfig
from dualeai.events.client import CloudEventsClient
from dualeai.events.http_transport import (
    HTTPTransportAuthError,
    HTTPTransportConnectionError,
    HTTPTransportResponseError,
)
from dualeai.exceptions import BusinessError, DualeAIAuthError, DualeAIConnectionError
from dualeai.models.library import (
    LibraryCreateRequest,
    LibraryDeleteRequest,
    LibraryDocumentDeleteRequest,
    LibraryDocumentGetRequest,
    LibraryDocumentListRequest,
    LibraryGetRequest,
    LibraryListResponse,
    LibraryPatchRequest,
    LibraryResponseDocumentStatus,
    LibraryUpdateRequest,
    PublicIndexedDocument,
)
from dualeai.models.problem_details import ProblemDetails
from tests.mocks.mock_http import MockHTTPTransport

pytestmark = pytest.mark.unit

_TOKEN = "dualeai_test_token_12345_padded_to_32bytes"
_BASE_URL = "https://api.test.duale.ai"
_TENANT_ID = "tenant-test"
_LIBRARY_ID = "018f35a8-2e90-7f2c-a6bb-9426f9c1f401"
_DOCUMENT_ID = "018f35a8-2e90-7f2c-a6bb-9426f9c1f403"
_DATE = "2026-06-12T10:00:00Z"
_LIBRARY_UUID = UUID(_LIBRARY_ID)
_DOCUMENT_UUID = UUID(_DOCUMENT_ID)
_LIBRARIES_URL = f"{_BASE_URL}/libraries/v1/tenants/{_TENANT_ID}"


def _library_response(*, path: str = "projects/contracts") -> dict[str, object]:
    return {
        "id": _LIBRARY_ID,
        "path": path,
        "tags": {},
        "updated_by": "user:test",
        "updated_at": _DATE,
        "created_at": _DATE,
        "deleted_at": None,
    }


def _document_response() -> dict[str, object]:
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
        "status": "ready",
        "progress_pct": 100,
        "failure": None,
        "tags": {},
    }


async def _connected_clients() -> tuple[LibrariesClient, MockHTTPTransport]:
    transport = MockHTTPTransport(tenant_id=_TENANT_ID)
    events = CloudEventsClient(
        DualeAIConfig(
            endpoint="https://api.test.duale.ai",
            token=_TOKEN,
            tenant_id=_TENANT_ID,
        ),
        transport=transport,
    )

    async def ensure_events_client() -> CloudEventsClient:
        if not events.is_connected:
            await events.connect()
        return events

    return LibrariesClient(ensure_events_client), transport


def test_create_request_carries_only_a_path_and_rejects_a_client_supplied_id() -> None:
    assert LibraryCreateRequest(path="projects/contracts").model_dump() == {"path": "projects/contracts"}
    for payload in (
        {},
        {"library_id": _LIBRARY_ID, "path": "projects/contracts"},
        {"path": ""},
    ):
        with pytest.raises(ValidationError):
            LibraryCreateRequest.model_validate(payload)


def test_public_library_surface_is_exported_and_stable_on_sdk() -> None:
    expected_exports = {
        "LibrariesClient",
        "LibraryCreateRequest",
        "LibraryDeleteRequest",
        "LibraryDocumentCreateResponse",
        "LibraryDocumentDeleteRequest",
        "LibraryDocumentGetRequest",
        "LibraryDocumentListRequest",
        "LibraryDocumentPage",
        "LibraryGetRequest",
        "LibraryListResponse",
        "LibraryPatchRequest",
        "LibraryResponseDocumentStatus",
        "LibraryUpdateRequest",
        "LibraryWithRevision",
        "PublicIndexedDocument",
    }
    internal_model_exports = {
        "DocumentId",
        "LibraryId",
        "TenantId",
    }
    transport = MockHTTPTransport(tenant_id=_TENANT_ID)
    sdk = DualeAISDK(
        config=DualeAIConfig(
            endpoint="https://api.test.duale.ai",
            token=_TOKEN,
            tenant_id=_TENANT_ID,
        ),
        transport=transport,
        auto_start=False,
    )

    assert expected_exports <= set(dualeai.__all__)
    assert internal_model_exports.isdisjoint(dualeai.__all__)
    assert isinstance(sdk.libraries, LibrariesClient)


async def test_first_concurrent_library_calls_share_one_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = MockHTTPTransport(tenant_id=_TENANT_ID)
    sdk = DualeAISDK(
        config=DualeAIConfig(
            endpoint="https://api.test.duale.ai",
            token=_TOKEN,
            tenant_id=_TENANT_ID,
        ),
        transport=transport,
        auto_start=False,
    )
    connect_started = asyncio.Event()
    release_connect = asyncio.Event()
    connect_calls = 0
    original_connect = transport.connect

    async def delayed_connect() -> None:
        nonlocal connect_calls
        connect_calls += 1
        connect_started.set()
        await release_connect.wait()
        await original_connect()

    monkeypatch.setattr(transport, "connect", delayed_connect)

    try:
        first = asyncio.create_task(sdk.libraries.list())
        await connect_started.wait()
        second = asyncio.create_task(sdk.libraries.list())
        await asyncio.sleep(0)
        release_connect.set()
        await asyncio.gather(first, second)
    finally:
        release_connect.set()
        await sdk.cleanup()

    assert connect_calls == 1


async def test_core_library_crud_crosses_the_real_transport_boundary() -> None:
    sdk = DualeAISDK(
        config=DualeAIConfig(endpoint=_BASE_URL, token=_TOKEN, tenant_id=_TENANT_ID),
        auto_start=False,
    )
    item_url = f"{_LIBRARIES_URL}/{_LIBRARY_ID}"
    with aioresponses() as mocked:
        mocked.post(_LIBRARIES_URL, status=201, payload=_library_response())
        mocked.get(_LIBRARIES_URL, status=200, payload={"libraries": [_library_response()]})
        mocked.get(item_url, status=200, payload=_library_response())
        mocked.patch(item_url, status=200, payload=_library_response(path="projects/renamed"))
        mocked.delete(item_url, status=204)
        try:
            created = await sdk.libraries.create(LibraryCreateRequest(path="projects/contracts"))
            listed = await sdk.libraries.list()
            fetched = await sdk.libraries.get(LibraryGetRequest(library_id=_LIBRARY_UUID))
            updated = await sdk.libraries.update(
                LibraryUpdateRequest(
                    library_id=_LIBRARY_UUID,
                    patch=LibraryPatchRequest(path="projects/renamed"),
                )
            )
            await sdk.libraries.delete(LibraryDeleteRequest(library_id=_LIBRARY_UUID))
        finally:
            await sdk.cleanup()

    assert created.path == "projects/contracts"
    assert [str(library.id) for library in listed.libraries] == [_LIBRARY_ID]
    assert str(fetched.id) == _LIBRARY_ID
    assert updated.path == "projects/renamed"


async def test_core_document_reads_and_delete_cross_the_real_transport_boundary() -> None:
    sdk = DualeAISDK(
        config=DualeAIConfig(endpoint=_BASE_URL, token=_TOKEN, tenant_id=_TENANT_ID),
        auto_start=False,
    )
    documents_url = f"{_LIBRARIES_URL}/{_LIBRARY_ID}/documents"
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
            documents = await sdk.libraries.list_documents(
                LibraryDocumentListRequest(
                    library_id=_LIBRARY_UUID,
                    limit=100,
                    cursor=None,
                )
            )
            document = await sdk.libraries.get_document(
                LibraryDocumentGetRequest(
                    library_id=_LIBRARY_UUID,
                    document_id=_DOCUMENT_UUID,
                )
            )
            await sdk.libraries.delete_document(
                LibraryDocumentDeleteRequest(
                    library_id=_LIBRARY_UUID,
                    document_id=_DOCUMENT_UUID,
                )
            )
        finally:
            await sdk.cleanup()

    assert [str(item.document_id) for item in documents.documents] == [_DOCUMENT_ID]
    assert documents.next_cursor is None
    assert str(document.document_id) == _DOCUMENT_ID


@pytest.mark.parametrize(
    ("transport_error_type", "sdk_error_type"),
    [
        (HTTPTransportAuthError, DualeAIAuthError),
        (HTTPTransportResponseError, BusinessError),
        (HTTPTransportConnectionError, DualeAIConnectionError),
    ],
)
async def test_library_errors_are_translated_with_problem_details(
    monkeypatch: pytest.MonkeyPatch,
    transport_error_type: type[HTTPTransportAuthError | HTTPTransportResponseError | HTTPTransportConnectionError],
    sdk_error_type: type[DualeAIAuthError | BusinessError | DualeAIConnectionError],
) -> None:
    libraries, transport = await _connected_clients()
    problem = ProblemDetails(
        title="Library request denied",
        status=403,
        detail="The caller cannot read this Library.",
        error_code="PERMISSION_DENIED",
    )

    async def list_libraries() -> LibraryListResponse:
        raise transport_error_type("HTTP request failed", problem_details=problem)

    monkeypatch.setattr(transport, "list_libraries", list_libraries)

    with pytest.raises(sdk_error_type) as raised:
        await libraries.list()

    assert raised.value.problem_details is problem


async def test_upload_targets_explicit_library_and_returns_keyed_receipt(
    tmp_path: Path,
) -> None:
    libraries, transport = await _connected_clients()
    source = tmp_path / "contract.txt"
    source.write_text("contract")
    attachment = prepare_attachments([(source, "Contract")])[0]
    upload_url = "http://mock-s3:9000/test-bucket/mock-upload?partNumber=1"

    with aioresponses() as mocked:
        mocked.put(upload_url, status=200, headers={"ETag": '"etag-1"'})
        receipts = await libraries.upload(_LIBRARY_ID, [attachment])

    assert list(receipts) == [attachment.key]
    assert str(receipts[attachment.key].library_id) == _LIBRARY_ID
    assert [(request["method"], request["path"]) for request in transport.get_requests()] == [
        ("POST", f"/libraries/v1/tenants/{_TENANT_ID}/document-uploads"),
        ("POST", f"/libraries/v1/tenants/{_TENANT_ID}/{_LIBRARY_ID}/documents"),
    ]


async def test_empty_upload_does_not_connect() -> None:
    async def unexpected_connection() -> CloudEventsClient:
        raise AssertionError("empty upload must not connect")

    libraries = LibrariesClient(unexpected_connection)

    assert await libraries.upload(_LIBRARY_ID, []) == {}


async def test_invalid_prepared_upload_does_not_connect(tmp_path: Path) -> None:
    source = tmp_path / "contract.txt"
    source.write_text("contract")
    attachments = prepare_attachments([(source, "Contract")])
    source.write_text("contract changed")

    async def unexpected_connection() -> CloudEventsClient:
        raise AssertionError("invalid prepared input must not connect")

    libraries = LibrariesClient(unexpected_connection)

    with pytest.raises(ValueError, match="changed after preparation"):
        await libraries.upload(_LIBRARY_ID, attachments)


async def test_wait_for_document_uses_fixed_interval_and_returns_terminal_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    libraries, transport = await _connected_clients()
    request = LibraryDocumentGetRequest(
        library_id=_LIBRARY_UUID,
        document_id=_DOCUMENT_UUID,
    )
    queued = await libraries.get_document(request)
    ready = queued.model_copy(
        update={
            "status": LibraryResponseDocumentStatus.ready,
            "progress_pct": 100,
        }
    )
    polls = iter([queued, ready])
    delays: list[float] = []

    async def get_library_document(_request: LibraryDocumentGetRequest) -> PublicIndexedDocument:
        return next(polls)

    async def sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(transport, "get_library_document", get_library_document)
    monkeypatch.setattr("dualeai.libraries.asyncio.sleep", sleep)

    document = await libraries.wait_for_document(request, timeout=30)

    assert document.status.value == "ready"
    assert delays == [5.0]


async def test_wait_for_document_times_out_during_a_slow_terminal_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    libraries, transport = await _connected_clients()
    request = LibraryDocumentGetRequest(
        library_id=_LIBRARY_UUID,
        document_id=_DOCUMENT_UUID,
    )
    queued = await libraries.get_document(request)
    ready = queued.model_copy(
        update={
            "status": LibraryResponseDocumentStatus.ready,
            "progress_pct": 100,
        }
    )

    async def get_library_document(_request: LibraryDocumentGetRequest) -> PublicIndexedDocument:
        await asyncio.sleep(0.05)
        return ready

    monkeypatch.setattr(transport, "get_library_document", get_library_document)

    with pytest.raises(TimeoutError, match="did not reach a terminal state within 0.01s"):
        await libraries.wait_for_document(request, timeout=0.01)


async def test_wait_for_document_returns_failed_terminal_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    libraries, transport = await _connected_clients()
    request = LibraryDocumentGetRequest(
        library_id=_LIBRARY_UUID,
        document_id=_DOCUMENT_UUID,
    )
    queued = await libraries.get_document(request)
    failed = queued.model_copy(update={"status": LibraryResponseDocumentStatus.failed})

    async def get_library_document(_request: LibraryDocumentGetRequest) -> PublicIndexedDocument:
        return failed

    monkeypatch.setattr(transport, "get_library_document", get_library_document)

    document = await libraries.wait_for_document(request, timeout=1)

    assert document.status is LibraryResponseDocumentStatus.failed


async def test_wait_for_document_rejects_non_positive_timeout() -> None:
    libraries, _transport = await _connected_clients()

    with pytest.raises(ValueError, match="timeout"):
        await libraries.wait_for_document(
            LibraryDocumentGetRequest(
                library_id=_LIBRARY_UUID,
                document_id=_DOCUMENT_UUID,
            ),
            timeout=0,
        )
