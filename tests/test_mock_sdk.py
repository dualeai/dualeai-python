"""Behavioral tests for the public ``dualeai.testing.MockSDK`` helper."""

from pathlib import Path

import pytest

from dualeai import (
    LibraryCreateRequest,
    LibraryDeleteRequest,
    LibraryDocumentDeleteRequest,
    LibraryDocumentGetRequest,
    LibraryDocumentListRequest,
    LibraryGetRequest,
    LibraryPatchRequest,
    LibraryResponseDocumentStatus,
    LibraryUpdateRequest,
)
from dualeai.exceptions import BusinessError
from dualeai.testing import MockSDK


def _library_create_request(path: str) -> LibraryCreateRequest:
    return LibraryCreateRequest(path=path)


@pytest.mark.unit
async def test_mock_sdk_returns_configured_response() -> None:
    """A configured action returns exactly the response set for it."""
    sdk = MockSDK()
    sdk.set_mock_response("hello", "world")
    assert await sdk.mock_ask("hello") == "world"


@pytest.mark.unit
async def test_mock_sdk_default_response_for_unconfigured_action() -> None:
    """An unconfigured action returns the deterministic default string."""
    sdk = MockSDK()
    assert await sdk.mock_ask("unknown-action") == "Mock response for: unknown-action"


@pytest.mark.unit
async def test_mock_sdk_supports_structured_and_last_write_wins() -> None:
    """Structured responses round-trip, and the latest set value wins."""
    sdk = MockSDK()
    sdk.set_mock_response("data", {"key": "value"})
    assert await sdk.mock_ask("data") == {"key": "value"}
    sdk.set_mock_response("data", [1, 2, 3])
    assert await sdk.mock_ask("data") == [1, 2, 3]


@pytest.mark.unit
async def test_mock_sdk_runs_library_workflow_without_a_connection(tmp_path: Path) -> None:
    document_path = tmp_path / "contract.txt"
    document_path.write_text("contract")
    sdk = MockSDK()
    missing_library_id = "018f35a8-2e90-7f2c-a6bb-9426f9c1f999"

    assert await sdk.libraries.upload(missing_library_id, []) == {}

    create_request = _library_create_request("projects/contracts")
    retry_candidate = _library_create_request("projects/contracts")
    library = await sdk.libraries.create(create_request)
    same_library = await sdk.libraries.create(retry_candidate)
    # The server owns the Library identity: the create body carries the path alone.
    assert create_request.model_dump(mode="json") == {"path": "projects/contracts"}
    assert retry_candidate.model_dump(mode="json") == create_request.model_dump(mode="json")
    assert same_library.id == library.id
    assert [item.id for item in (await sdk.libraries.list()).libraries] == [library.id]
    assert (await sdk.libraries.get(LibraryGetRequest(library_id=library.id))).id == library.id
    library = await sdk.libraries.update(
        LibraryUpdateRequest(
            library_id=library.id,
            patch=LibraryPatchRequest(path="projects/renamed"),
        )
    )
    assert library.path == "projects/renamed"

    prepared = sdk.prepare_attachments([(document_path, "Signed contract")])
    receipts = await sdk.libraries.upload(library.id, prepared)
    get_document_request = LibraryDocumentGetRequest(
        library_id=library.id,
        document_id=receipts[prepared[0].key].document_id,
    )
    queued = await sdk.libraries.get_document(get_document_request)
    document = await sdk.libraries.wait_for_document(get_document_request)

    assert queued.status is LibraryResponseDocumentStatus.queued
    assert document.status is LibraryResponseDocumentStatus.ready
    assert document.progress_pct == 100
    list_documents_request = LibraryDocumentListRequest(
        library_id=library.id,
        limit=100,
        cursor=None,
    )
    page = await sdk.libraries.list_documents(list_documents_request)
    assert [item.document_id for item in page.documents] == [document.document_id]
    assert page.next_cursor is None

    delete_document_request = LibraryDocumentDeleteRequest(
        library_id=library.id,
        document_id=document.document_id,
    )
    await sdk.libraries.delete_document(delete_document_request)
    await sdk.libraries.delete_document(delete_document_request)
    page = await sdk.libraries.list_documents(list_documents_request)
    assert page.documents == []
    assert page.next_cursor is None
    with pytest.raises(BusinessError, match="does not exist"):
        await sdk.libraries.get_document(get_document_request)

    delete_request = LibraryDeleteRequest(library_id=library.id)
    await sdk.libraries.delete(delete_request)
    await sdk.libraries.delete(delete_request)
    assert (await sdk.libraries.list()).libraries == []
    with pytest.raises(BusinessError, match="does not exist"):
        await sdk.libraries.get(LibraryGetRequest(library_id=library.id))


@pytest.mark.unit
async def test_mock_library_tag_update_replaces_the_tag_map() -> None:
    sdk = MockSDK()
    library = await sdk.libraries.create(_library_create_request("projects/contracts"))
    library = await sdk.libraries.update(
        LibraryUpdateRequest(
            library_id=library.id,
            patch=LibraryPatchRequest(tags={"project": "contracts", "year": 2026}),
        )
    )
    library = await sdk.libraries.update(
        LibraryUpdateRequest(
            library_id=library.id,
            patch=LibraryPatchRequest(tags={"project": "archive"}),
        )
    )

    assert library.tags == {"project": "archive"}


@pytest.mark.unit
async def test_mock_library_upload_rejects_duplicate_attachment_keys(tmp_path: Path) -> None:
    document_path = tmp_path / "contract.txt"
    document_path.write_text("contract")
    sdk = MockSDK()
    library = await sdk.libraries.create(_library_create_request("projects/contracts"))
    prepared = sdk.prepare_attachments([(document_path, "Signed contract")])

    with pytest.raises(ValueError, match="Duplicate attachment key"):
        await sdk.libraries.upload(library.id, [prepared[0], prepared[0]])


@pytest.mark.unit
async def test_mock_library_upload_rejects_missing_prepared_file(tmp_path: Path) -> None:
    document_path = tmp_path / "contract.txt"
    document_path.write_text("contract")
    sdk = MockSDK()
    library = await sdk.libraries.create(_library_create_request("projects/contracts"))
    prepared = sdk.prepare_attachments([(document_path, "Signed contract")])
    document_path.unlink()

    with pytest.raises(FileNotFoundError, match="contract.txt"):
        await sdk.libraries.upload(library.id, prepared)


@pytest.mark.unit
async def test_mock_library_upload_rejects_empty_prepared_file(tmp_path: Path) -> None:
    document_path = tmp_path / "contract.txt"
    document_path.write_text("contract")
    sdk = MockSDK()
    library = await sdk.libraries.create(_library_create_request("projects/contracts"))
    prepared = sdk.prepare_attachments([(document_path, "Signed contract")])
    document_path.write_bytes(b"")

    with pytest.raises(ValueError, match="empty"):
        await sdk.libraries.upload(library.id, prepared)


@pytest.mark.unit
async def test_mock_library_upload_rejects_changed_prepared_file(tmp_path: Path) -> None:
    document_path = tmp_path / "contract.txt"
    document_path.write_text("contract")
    sdk = MockSDK()
    library = await sdk.libraries.create(_library_create_request("projects/contracts"))
    prepared = sdk.prepare_attachments([(document_path, "Signed contract")])
    document_path.write_text("contract changed")

    with pytest.raises(ValueError, match="changed after preparation"):
        await sdk.libraries.upload(library.id, prepared)
