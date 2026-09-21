"""Core Library and document management for the Python SDK."""

from __future__ import annotations

import asyncio
import builtins
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import TypeAlias
from uuid import UUID

from dualeai.attachments import PreparedAttachment, _validate_prepared_attachments, upload_attachments_to_library
from dualeai.events.client import CloudEventsClient, _raise_translated
from dualeai.events.http_transport import HTTPTransportError
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

EnsureEventsClient: TypeAlias = Callable[[], Awaitable[CloudEventsClient]]

_DOCUMENT_WAIT_TIMEOUT = timedelta(minutes=6)
"""Default bound for document ingestion polling."""

_DOCUMENT_POLL_INTERVAL = timedelta(seconds=5)
"""Fixed client-owned interval between document state reads."""


class LibrariesClient:
    """Core Library metadata and document operations."""

    def __init__(self, ensure_events_client: EnsureEventsClient) -> None:
        self._ensure_events_client = ensure_events_client

    async def create(self, request: LibraryCreateRequest) -> LibraryWithRevision:
        """Create a Library or return a writable active Library at the path.

        The request carries only ``path``: the server owns the Library
        identity. Paths are mutable, non-unique display metadata, so the
        returned Library id is the stable handle for later operations.

        A server-side Library id collision raises ``BusinessError``. A failed
        document-storage initialization raises ``DualeAIConnectionError``; the
        server then deletes the Library row it just wrote.
        """
        events = await self._ensure_events_client()
        try:
            return await events.transport.create_library(request)
        except HTTPTransportError as error:
            _raise_translated("Library create failed", error)

    async def list(self) -> LibraryListResponse:
        """List Libraries accessible to the configured token."""
        events = await self._ensure_events_client()
        try:
            return await events.transport.list_libraries()
        except HTTPTransportError as error:
            _raise_translated("Library list failed", error)

    async def get(self, request: LibraryGetRequest) -> LibraryWithRevision:
        """Get one Library by stable id."""
        events = await self._ensure_events_client()
        try:
            return await events.transport.get_library(request)
        except HTTPTransportError as error:
            _raise_translated("Library get failed", error)

    async def update(self, request: LibraryUpdateRequest) -> LibraryWithRevision:
        """Update a Library path, replace its tag map, or both.

        Set ``request.patch.tags`` to ``{}`` to clear all tags. The method does
        not merge keys with the current map.
        """
        events = await self._ensure_events_client()
        try:
            return await events.transport.update_library(request)
        except HTTPTransportError as error:
            _raise_translated("Library update failed", error)

    async def delete(self, request: LibraryDeleteRequest) -> None:
        """Delete one Library and its documents.

        A later delete of the same Library succeeds, and so does a delete of a
        Library that never existed. A Library without a valid document-tree
        activation stays active instead: the server answers 503, so every call
        raises ``DualeAIConnectionError``.
        """
        events = await self._ensure_events_client()
        try:
            await events.transport.delete_library(request)
        except HTTPTransportError as error:
            _raise_translated("Library delete failed", error)

    async def upload(
        self,
        library_id: str | UUID,
        attachments: builtins.list[PreparedAttachment],
    ) -> dict[str, LibraryDocumentCreateResponse]:
        """Upload prepared attachments and return queued receipts by key.

        The batch is concurrent, not transactional. A ``LibraryUploadError``
        carries receipts that completed before a local or object-store failure.
        """
        if not attachments:
            return {}
        _validate_prepared_attachments(attachments)
        events = await self._ensure_events_client()
        try:
            return await upload_attachments_to_library(
                events.transport,
                attachments,
                str(library_id),
            )
        except HTTPTransportError as error:
            _raise_translated("Library document upload failed", error)

    async def list_documents(self, request: LibraryDocumentListRequest) -> LibraryDocumentPage:
        """List one bounded page of live documents in one Library."""
        events = await self._ensure_events_client()
        try:
            return await events.transport.list_library_documents(request)
        except HTTPTransportError as error:
            _raise_translated("Library document page failed", error)

    async def get_document(self, request: LibraryDocumentGetRequest) -> PublicIndexedDocument:
        """Get one document's current public state."""
        events = await self._ensure_events_client()
        try:
            return await events.transport.get_library_document(request)
        except HTTPTransportError as error:
            _raise_translated("Library document get failed", error)

    async def wait_for_document(
        self,
        request: LibraryDocumentGetRequest,
        *,
        timeout: float = _DOCUMENT_WAIT_TIMEOUT.total_seconds(),
    ) -> PublicIndexedDocument:
        """Poll until document ingestion reaches ``ready`` or ``failed``."""
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")

        try:
            return await asyncio.wait_for(
                self._poll_document_until_terminal(request),
                timeout=timeout,
            )
        except asyncio.TimeoutError as error:
            raise TimeoutError(
                f"Library document {request.document_id} did not reach a terminal state within {timeout:g}s"
            ) from error

    async def _poll_document_until_terminal(
        self,
        request: LibraryDocumentGetRequest,
    ) -> PublicIndexedDocument:
        """Poll within the deadline owned by ``wait_for_document``."""
        events = await self._ensure_events_client()
        while True:
            try:
                document = await events.transport.get_library_document(request)
            except HTTPTransportError as error:
                _raise_translated("Library document poll failed", error)

            if document.status in {
                LibraryResponseDocumentStatus.ready,
                LibraryResponseDocumentStatus.failed,
            }:
                return document

            await asyncio.sleep(_DOCUMENT_POLL_INTERVAL.total_seconds())

    async def delete_document(self, request: LibraryDocumentDeleteRequest) -> None:
        """Move one document to trash; repeated deletes succeed."""
        events = await self._ensure_events_client()
        try:
            await events.transport.delete_library_document(request)
        except HTTPTransportError as error:
            _raise_translated("Library document delete failed", error)
