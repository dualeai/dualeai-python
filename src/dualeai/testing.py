"""Testing utilities for the Duale AI SDK.

Provides MockSDK for testing task responses and Library workflows without HTTP.

Usage in tests:
    from dualeai.testing import MockSDK

    async def test_example():
        sdk = MockSDK()
        sdk.set_mock_response("hello", "world")
        result = await sdk.mock_ask("hello")
        assert result == "world"

Behavior is covered by ``tests/test_mock_sdk.py``.
"""

from __future__ import annotations

import builtins
from datetime import datetime, timezone
from uuid import UUID

from uuid_utils import uuid7

from dualeai.attachments import PreparedAttachment, _validate_prepared_attachments
from dualeai.config import DualeAIConfig
from dualeai.exceptions import BusinessError
from dualeai.libraries import LibrariesClient
from dualeai.models.library import (
    LibraryCreateRequest,
    LibraryDeleteRequest,
    LibraryDocumentCreateResponse,
    LibraryDocumentDeleteRequest,
    LibraryDocumentGetRequest,
    LibraryDocumentListRequest,
    LibraryDocumentPage,
    LibraryGetRequest,
    LibraryListResponse,
    LibraryResponseDocumentStatus,
    LibraryUpdateRequest,
    LibraryWithRevision,
    PublicIndexedDocument,
)
from dualeai.sdk import DualeAISDK

# Type aliases for common test data
TestOutput = str | int | float | bool | dict[str, str | int | float | bool] | list[str | int | float | bool] | None


class MockLibrariesClient(LibrariesClient):
    """In-memory subset of the Library client for deterministic tests.

    It performs no HTTP, authentication, object-store upload, or asynchronous
    ingestion. ``wait_for_document`` changes a queued document to ``ready``
    immediately, and deletes remove in-memory records rather than modelling
    service retention or trash behavior.
    """

    def __init__(self, *, tenant_id: str = "tenant-test") -> None:
        self._tenant_id = tenant_id
        self._libraries: dict[str, LibraryWithRevision] = {}
        self._documents: dict[tuple[str, str], PublicIndexedDocument] = {}

    @staticmethod
    def _copy_library(library: LibraryWithRevision) -> LibraryWithRevision:
        return library.model_copy(deep=True)

    @staticmethod
    def _copy_document(document: PublicIndexedDocument) -> PublicIndexedDocument:
        return document.model_copy(deep=True)

    def _require_library(self, library_id: str | UUID) -> LibraryWithRevision:
        key = str(library_id)
        library = self._libraries.get(key)
        if library is None:
            raise BusinessError(f"Library {key} does not exist", operation="mock_library_read")
        return library

    def _require_document(
        self,
        library_id: str | UUID,
        document_id: str | UUID,
    ) -> PublicIndexedDocument:
        key = (str(library_id), str(document_id))
        document = self._documents.get(key)
        if document is None:
            raise BusinessError(f"Library document {key[1]} does not exist", operation="mock_library_document_read")
        return document

    async def create(self, request: LibraryCreateRequest) -> LibraryWithRevision:
        """Create a Library or resolve the first active matching path."""
        matching = sorted(
            (library for library in self._libraries.values() if library.path == request.path),
            key=lambda library: str(library.id),
        )
        if matching:
            return self._copy_library(matching[0])

        now = datetime.now(timezone.utc)
        library = LibraryWithRevision(
            id=UUID(str(uuid7())),
            path=request.path,
            tags={},
            updated_by="agent:mock",
            updated_at=now,
            created_at=now,
            deleted_at=None,
        )
        self._libraries[str(library.id)] = library
        return self._copy_library(library)

    async def list(self) -> LibraryListResponse:
        """List active in-memory Libraries in stable id order."""
        return LibraryListResponse(
            libraries=[
                self._copy_library(library)
                for library in sorted(self._libraries.values(), key=lambda item: str(item.id))
            ]
        )

    async def get(self, request: LibraryGetRequest) -> LibraryWithRevision:
        """Get one in-memory Library."""
        return self._copy_library(self._require_library(request.library_id))

    async def update(self, request: LibraryUpdateRequest) -> LibraryWithRevision:
        """Replace the supplied path or complete tag map."""
        library = self._require_library(request.library_id)
        updated = library.model_copy(
            update={
                "path": request.patch.path if request.patch.path is not None else library.path,
                "tags": request.patch.tags if request.patch.tags is not None else library.tags,
                "updated_at": datetime.now(timezone.utc),
            },
            deep=True,
        )
        self._libraries[str(updated.id)] = updated
        return self._copy_library(updated)

    async def delete(self, request: LibraryDeleteRequest) -> None:
        """Delete one in-memory Library and its documents."""
        key = str(request.library_id)
        if self._libraries.pop(key, None) is None:
            return
        self._documents = {
            document_key: document for document_key, document in self._documents.items() if document_key[0] != key
        }

    async def upload(
        self,
        library_id: str | UUID,
        attachments: builtins.list[PreparedAttachment],
    ) -> dict[str, LibraryDocumentCreateResponse]:
        """Queue prepared attachments in memory and return their receipts."""
        if not attachments:
            return {}
        _validate_prepared_attachments(attachments)
        library = self._require_library(library_id)
        receipts: dict[str, LibraryDocumentCreateResponse] = {}
        now = datetime.now(timezone.utc)
        for attachment in attachments:
            document_id = UUID(str(uuid7()))
            document = PublicIndexedDocument(
                document_id=document_id,
                library_id=library.id,
                filename=attachment.filename,
                description=attachment.description,
                content_type=None,
                size_bytes=attachment.size,
                page_count=None,
                created_at=now,
                deleted_at=None,
                status=LibraryResponseDocumentStatus.queued,
                progress_pct=None,
                failure=None,
                tags={},
            )
            self._documents[(str(library.id), str(document_id))] = document
            receipts[attachment.key] = LibraryDocumentCreateResponse(
                document_id=document_id,
                library_id=library.id,
                status="queued",
                location=f"/v1/hpke/tenants/{self._tenant_id}/{library.id}/documents/{document_id}",
            )
        return receipts

    async def list_documents(self, request: LibraryDocumentListRequest) -> LibraryDocumentPage:
        """List one bounded page of live in-memory documents for one Library."""
        library = self._require_library(request.library_id)
        documents = [
            self._copy_document(document)
            for (owner_id, _), document in sorted(self._documents.items())
            if owner_id == str(library.id)
        ]
        if request.cursor is not None:
            documents = [document for document in documents if str(document.document_id) > request.cursor]
        page = documents[: request.limit]
        next_cursor = str(page[-1].document_id) if len(documents) > request.limit else None
        return LibraryDocumentPage(documents=page, next_cursor=next_cursor)

    async def get_document(self, request: LibraryDocumentGetRequest) -> PublicIndexedDocument:
        """Get one in-memory document."""
        self._require_library(request.library_id)
        return self._copy_document(self._require_document(request.library_id, request.document_id))

    async def wait_for_document(
        self,
        request: LibraryDocumentGetRequest,
        *,
        timeout: float = 360.0,
    ) -> PublicIndexedDocument:
        """Complete one queued in-memory document immediately."""
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        self._require_library(request.library_id)
        document = self._require_document(request.library_id, request.document_id)
        ready = document.model_copy(
            update={"status": LibraryResponseDocumentStatus.ready, "progress_pct": 100},
            deep=True,
        )
        self._documents[(str(request.library_id), str(request.document_id))] = ready
        return self._copy_document(ready)

    async def delete_document(self, request: LibraryDocumentDeleteRequest) -> None:
        """Delete one in-memory document."""
        library = self._require_library(request.library_id)
        self._documents.pop((str(library.id), str(request.document_id)), None)


class MockSDK(DualeAISDK):
    """Offline test helper with keyed responses and an in-memory Library client.

    ``mock_ask()`` is a deterministic lookup, not the production ``ask()``
    transport. It does not simulate streaming, Tool dispatch, terminal errors,
    authentication, retries, object storage, or asynchronous document ingestion.
    """

    def __init__(self) -> None:
        """Initialize mock SDK with minimal configuration.

        Library methods use an in-memory tenant and never open a connection.
        """
        test_token = "dualeai_test_mock_token_12345_padded_to_32bytes"

        config = DualeAIConfig(
            endpoint="https://mock-bridge:8080",
            token=test_token,
            tenant_id="tenant-test",
            redis_url="redis://localhost:6379",
        )
        super().__init__(config=config, auto_start=False)
        self._libraries = MockLibrariesClient(tenant_id="tenant-test")
        self._mock_responses: dict[str, TestOutput] = {}

    def set_mock_response(self, action: str, response: TestOutput) -> None:
        """Set a mock response for a specific action."""
        self._mock_responses[action] = response

    async def mock_ask(self, action: str) -> TestOutput:
        """Return the response registered for ``action``, or a stable fallback."""
        if action in self._mock_responses:
            return self._mock_responses[action]
        return f"Mock response for: {action}"
