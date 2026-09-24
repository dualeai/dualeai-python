"""Core Library and document management for the Python SDK.

Public CRUD dispatch through the protected transport is covered by
``tests/test_http_transport_v3.py``. Upload request construction, polling, and
error translation are covered by ``tests/test_libraries_client.py``. Service-side
storage and retention behavior is not exercised by this repository's tests.
"""

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
    """Library metadata, document, upload, and ingestion-polling operations.

    Obtain this client from :attr:`dualeai.sdk.DualeAISDK.libraries`; its
    constructor callback is an SDK integration detail. HTTP failures are
    translated consistently: authentication rejection to ``DualeAIAuthError``,
    other rejected requests to ``BusinessError``, and transport or server
    failures to ``DualeAIConnectionError``. When present, the platform's
    ``ProblemDetails`` is attached to the exception.
    """

    def __init__(self, ensure_events_client: EnsureEventsClient) -> None:
        self._ensure_events_client = ensure_events_client

    async def create(self, request: LibraryCreateRequest) -> LibraryWithRevision:
        """Create or resolve a Library for ``request.path``.

        The request carries a path, while the response supplies the stable
        Library id used by later operations.

        Args:
            request: Validated Library creation request.

        Returns:
            The server-returned Library and revision metadata.
        """
        events = await self._ensure_events_client()
        try:
            return await events.transport.create_library(request)
        except HTTPTransportError as error:
            _raise_translated("Library create failed", error)

    async def list(self) -> LibraryListResponse:
        """List Libraries accessible to the configured credentials."""
        events = await self._ensure_events_client()
        try:
            return await events.transport.list_libraries()
        except HTTPTransportError as error:
            _raise_translated("Library list failed", error)

    async def get(self, request: LibraryGetRequest) -> LibraryWithRevision:
        """Get one Library by stable id.

        Args:
            request: Library identifier.

        Returns:
            The server-returned Library and revision metadata.
        """
        events = await self._ensure_events_client()
        try:
            return await events.transport.get_library(request)
        except HTTPTransportError as error:
            _raise_translated("Library get failed", error)

    async def update(self, request: LibraryUpdateRequest) -> LibraryWithRevision:
        """Update a Library path, replace its tag map, or both.

        Set ``request.patch.tags`` to ``{}`` to clear all tags. The method does
        not merge keys with the current map.

        Args:
            request: Library id and replacement patch fields.

        Returns:
            The updated server representation.
        """
        events = await self._ensure_events_client()
        try:
            return await events.transport.update_library(request)
        except HTTPTransportError as error:
            _raise_translated("Library update failed", error)

    async def delete(self, request: LibraryDeleteRequest) -> None:
        """Send a delete request for one Library.

        The method returns after a successful no-content HTTP response. Any
        idempotency, retention, or cascading behavior is defined by the service,
        not implemented by this client.

        Args:
            request: Library identifier to delete.
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

        The batch runs at most eight file pipelines and eight part requests at
        once; it is not transactional. A ``LibraryUploadError`` carries receipts
        that completed before a local or object-store failure.

        Args:
            library_id: Stable destination Library id.
            attachments: Prepared attachments to validate and upload.

        Returns:
            Queued document receipts keyed by attachment correlation key.

        Raises:
            FileNotFoundError: If a prepared path no longer exists.
            ValueError: If an attachment key is duplicated or a prepared file
                is no longer a regular non-empty file of the recorded size.
            LibraryUploadError: If local reading or an object-store upload
                fails. Inspect ``completed_receipts`` for partial completion.
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
        """List one bounded document page for a Library.

        Args:
            request: Library id, page bound, and optional cursor.

        Returns:
            Documents and the optional cursor for the next page.
        """
        events = await self._ensure_events_client()
        try:
            return await events.transport.list_library_documents(request)
        except HTTPTransportError as error:
            _raise_translated("Library document page failed", error)

    async def get_document(self, request: LibraryDocumentGetRequest) -> PublicIndexedDocument:
        """Get a document's current public ingestion state."""
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
        """Poll every five seconds until ingestion is ``ready`` or ``failed``.

        Both terminal states are returned; callers must inspect ``status`` and
        handle ``failed`` explicitly. The default deadline is 360 seconds.

        Args:
            request: Library and document identifiers to poll.
            timeout: Positive total wait in seconds.

        Returns:
            The first document state whose status is ``ready`` or ``failed``.

        Raises:
            ValueError: If ``timeout`` is not greater than zero.
            TimeoutError: If no terminal state arrives before ``timeout``.
            DualeAIAuthError: If a poll is unauthorized.
            BusinessError: If a poll request is rejected.
            DualeAIConnectionError: If polling encounters a transport or server
                failure.
        """
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
        """Send a delete request for one document.

        The method returns after a successful no-content HTTP response. Any
        trash, retention, or idempotency behavior is defined by the service.
        """
        events = await self._ensure_events_client()
        try:
            await events.transport.delete_library_document(request)
        except HTTPTransportError as error:
            _raise_translated("Library document delete failed", error)
