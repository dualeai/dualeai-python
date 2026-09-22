"""Prepare local files and upload them as Library documents for a Task.

Preparation records path, name, description, and size without reading file
contents. Upload validates the recorded files again, resolves a Task-scoped
Library, streams object-store parts in 64 KiB chunks, and queues one Library
document per file.

One batch runs at most eight file pipelines and eight part requests at once.
Transient client errors, timeouts, and HTTP 500/502/503/504 responses receive up
to five attempts. Other object-store failures raise ``LibraryUploadError``
immediately. A batch is not transactional; the error exposes receipts that
completed before a sibling failed.

The local file, retry, concurrency, and partial-receipt behavior is covered by
``tests/test_attachments.py`` and ``tests/test_attachments_s3.py``.
"""

import asyncio
import hashlib
from collections.abc import AsyncIterator, Awaitable, Iterator, Sequence
from http import HTTPStatus
from pathlib import Path
from typing import Protocol, TypeVar
from uuid import UUID

import aiofiles
import aiohttp
from pydantic import BaseModel, ConfigDict, Field
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter
from uuid_utils import uuid7

from dualeai.exceptions import LibraryUploadError
from dualeai.models.library import (
    LibraryDocumentCreateOperationRequest,
    LibraryDocumentCreatePartRef,
    LibraryDocumentCreateRequest,
    LibraryDocumentCreateResponse,
    LibraryDocumentUploadPart,
    LibraryDocumentUploadRequest,
    LibraryDocumentUploadResponse,
)

_STREAM_CHUNK_SIZE = 64 * 1024
"""Sub-chunk size for streaming file reads: 64 KB."""

_MAX_CONCURRENT_UPLOADS = 8
"""Bound active file pipelines and presigned part PUTs per batch."""

_GatherT = TypeVar("_GatherT")


class _LibraryUploadTransport(Protocol):
    """Library calls required by the attachment upload pipeline."""

    async def create_document_upload(
        self,
        request: LibraryDocumentUploadRequest,
    ) -> LibraryDocumentUploadResponse: ...

    async def create_library_document(
        self,
        request: LibraryDocumentCreateOperationRequest,
    ) -> LibraryDocumentCreateResponse: ...


async def _gather_related(awaitables: Sequence[Awaitable[_GatherT]]) -> list[_GatherT]:
    """Run related work and drain sibling cancellation after any failure."""
    tasks = [asyncio.ensure_future(awaitable) for awaitable in awaitables]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


class PreparedAttachment(BaseModel):
    """Prepared attachment metadata for upload and task submission.

    Created by :func:`prepare_attachments`. Pass the values to
    :meth:`dualeai.sdk.DualeAISDK.upload_attachments` for upload and then to
    :meth:`dualeai.sdk.DualeAISDK.submit_task` for Task context.

    ``key`` correlates the prepared item with its upload receipt and task/cache
    metadata. The Library assigns the stable ``document_id`` returned by the
    upload call.
    """

    model_config = ConfigDict(frozen=True)

    key: str = Field(min_length=1, max_length=128)
    """SDK-side correlation key, normally a uuid7."""

    path: Path
    """Local file path."""

    filename: str = Field(min_length=1, max_length=500)
    """Original filename (from path.name)."""

    description: str = Field(max_length=500)
    """Functional description for agent orientation."""

    size: int = Field(ge=1)
    """File size in bytes."""


def prepare_attachments(files: list[tuple[Path, str]]) -> list[PreparedAttachment]:
    """Prepare attachment metadata for upload and task submission.

    This performs filesystem existence, file-type, and size checks. It does not
    open or read file contents; upload repeats the checks to detect stale
    metadata.

    Args:
        files: List of ``(path, description)`` tuples.

    Returns:
        List of :class:`PreparedAttachment` with key, filename, description, size.

    Raises:
        FileNotFoundError: If any path does not exist.
        ValueError: If a path is not a regular, non-empty file.
        pydantic.ValidationError: If a filename or description violates
            ``PreparedAttachment`` bounds.
    """
    attachments: list[PreparedAttachment] = []
    for path, description in files:
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        if not path.is_file():
            raise ValueError(f"Attachment path is not a regular file: {path}")

        size = path.stat().st_size
        if size == 0:
            raise ValueError(f"Attachment file is empty: {path}")

        attachments.append(
            PreparedAttachment(
                key=str(uuid7()),
                path=path,
                filename=path.name,
                description=description,
                size=size,
            )
        )

    return attachments


def _validate_prepared_attachments(attachments: list[PreparedAttachment]) -> None:
    """Reject stale or ambiguous prepared inputs before opening a connection."""
    seen_keys: set[str] = set()
    for attachment in attachments:
        if attachment.key in seen_keys:
            raise ValueError(f"Duplicate attachment key: {attachment.key}")
        seen_keys.add(attachment.key)

        if not attachment.path.exists():
            raise FileNotFoundError(f"File not found: {attachment.path}")
        if not attachment.path.is_file():
            raise ValueError(f"Attachment path is not a regular file: {attachment.path}")
        current_size = attachment.path.stat().st_size
        if current_size == 0:
            raise ValueError(f"Attachment file is empty: {attachment.path}")
        if current_size != attachment.size:
            raise ValueError(
                f"Attachment changed after preparation: {attachment.path} "
                f"(expected {attachment.size} bytes, found {current_size})"
            )


async def _stream_file_part(path: Path, offset: int, length: int) -> AsyncIterator[bytes]:
    """Stream a file part as 64 KB sub-chunks via aiofiles.

    Non-blocking file I/O via threadpool. Peak memory: 64 KB per active stream.
    Called fresh on each retry attempt (generators are single-use).
    """
    async with aiofiles.open(path, "rb") as f:
        await f.seek(offset)
        remaining = length
        while remaining > 0:
            read_size = min(_STREAM_CHUNK_SIZE, remaining)
            data = await f.read(read_size)
            if not data:
                break
            yield data
            remaining -= len(data)


async def _compute_content_sha256(path: Path) -> str:
    """Compute hex SHA-256 of ``path`` streaming 64 KB at a time.

    Single full-file pass via aiofiles — peak memory bounded by sub-chunk size.
    """
    digest = hashlib.sha256()
    async with aiofiles.open(path, "rb") as f:
        while True:
            chunk = await f.read(_STREAM_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


class S3UploadError(Exception):
    """Internal retry sentinel for transient object-store status codes."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(message)


async def _upload_part(
    s3_session: aiohttp.ClientSession,
    attachment: PreparedAttachment,
    part: LibraryDocumentUploadPart,
    semaphore: asyncio.Semaphore,
) -> LibraryDocumentCreatePartRef:
    """Upload one presigned part within the batch concurrency bound."""
    try:
        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type((aiohttp.ClientError, TimeoutError, S3UploadError)),
            wait=wait_exponential_jitter(initial=0.5, max=30.0, jitter=2.0),
            stop=stop_after_attempt(5),
            reraise=True,
        ):
            with attempt:
                async with semaphore:
                    async with s3_session.put(
                        str(part.upload_url),
                        data=_stream_file_part(attachment.path, part.offset, part.length),
                        headers={"Content-Length": str(part.length)},
                    ) as response:
                        if response.status in {
                            HTTPStatus.INTERNAL_SERVER_ERROR,
                            HTTPStatus.BAD_GATEWAY,
                            HTTPStatus.SERVICE_UNAVAILABLE,
                            HTTPStatus.GATEWAY_TIMEOUT,
                        }:
                            raise S3UploadError(response.status, f"S3 returned {response.status}")
                        if response.status != HTTPStatus.OK:
                            raise LibraryUploadError(
                                f"Object-store upload returned HTTP {response.status}",
                                attachment_key=attachment.key,
                                part_number=part.part_number,
                            )
                        etag = response.headers.get("ETag")
                        if etag is None:
                            raise LibraryUploadError(
                                "Object-store upload response omitted ETag",
                                attachment_key=attachment.key,
                                part_number=part.part_number,
                            )
                        return LibraryDocumentCreatePartRef(
                            part_number=part.part_number,
                            etag=etag,
                        )
    except (aiohttp.ClientError, TimeoutError, S3UploadError) as error:
        raise LibraryUploadError(
            "Object-store upload failed after five attempts",
            attachment_key=attachment.key,
            part_number=part.part_number,
        ) from error
    raise RuntimeError("S3 upload retry loop exited without a result")


def _call_scope_library_path(agent_id: str, task_id: str) -> str:
    """Build the call-scope Library path for flat attachment-helper routing.

    ``agent/{agent_id}/task/{task_id}`` is an SDK convention, not a reserved
    grammar for arbitrary Library paths. The identifiers select a path; they do
    not grant permission to it.
    """
    return f"agent/{agent_id}/task/{task_id}"


async def upload_attachment_to_library(
    transport: _LibraryUploadTransport,
    s3_session: aiohttp.ClientSession,
    attachment: PreparedAttachment,
    library_id: str,
    part_semaphore: asyncio.Semaphore,
) -> LibraryDocumentCreateResponse:
    """Upload one prepared attachment to an existing Library.

    Flow:

    1. Request an upload id and presigned part URLs using the recorded size.
    2. Stream each part with an explicit ``Content-Length`` and bounded retries.
    3. Compute SHA-256 in a separate streaming pass while parts upload.
    4. Create a queued Library document from the upload id, metadata, digest,
       size, and returned part ETags.

    File I/O is non-blocking and bounded: peak memory per concurrent slot is
    ``_STREAM_CHUNK_SIZE`` (64 KB).

    Args:
        transport: Transport implementing the required Library calls.
        s3_session: aiohttp session for presigned object-store URLs.
        attachment: Prepared attachment metadata.
        library_id: Stable destination Library identifier.
        part_semaphore: Batch-wide bound for concurrent presigned PUTs.

    Returns:
        Queued document identifiers, initial status, and polling location.

    Raises:
        LibraryUploadError: If a file read or object-store upload cannot
            complete. The exception identifies the attachment and, for part
            failures, the part number.
    """
    # 1. Request presigned URLs (size-only payload — caller identity is in JWT).
    upload_request = LibraryDocumentUploadRequest(size_bytes=attachment.size)
    response = await transport.create_document_upload(upload_request)

    # Compute content sha256 in parallel with part uploads. This adds one
    # bounded streaming pass without buffering the full file.
    sha_task = asyncio.create_task(_compute_content_sha256(attachment.path))
    parts_task = asyncio.create_task(
        _gather_related([_upload_part(s3_session, attachment, part, part_semaphore) for part in response.parts])
    )

    try:
        content_sha256, completed_parts = await asyncio.gather(sha_task, parts_task)
    except BaseException as error:
        # A file-read or part failure stops the sibling work. Draining both
        # tasks lets aiofiles and aiohttp release their resources first.
        for task in (sha_task, parts_task):
            task.cancel()
        await asyncio.gather(sha_task, parts_task, return_exceptions=True)
        if isinstance(error, asyncio.CancelledError | LibraryUploadError):
            raise
        raise LibraryUploadError(
            "Could not read the prepared attachment",
            attachment_key=attachment.key,
        ) from error

    # Sort into a local: pydantic's generated `__init__` still admits a mapping
    # for a nested-model field (validation rejects one under `strict=True`), and
    # solving `sorted` against that wider target widens the key parameter.
    ordered_parts: list[LibraryDocumentCreatePartRef] = sorted(completed_parts, key=lambda p: p.part_number)
    create_request = LibraryDocumentCreateRequest(
        upload_id=response.upload_id,
        filename=attachment.filename,
        description=attachment.description,
        content_sha256=content_sha256,
        size_bytes=attachment.size,
        parts=ordered_parts,
    )
    return await transport.create_library_document(
        LibraryDocumentCreateOperationRequest(
            library_id=UUID(str(library_id)),
            document=create_request,
        )
    )


async def _upload_attachment_worker(
    work: Iterator[tuple[int, PreparedAttachment]],
    receipts: list[LibraryDocumentCreateResponse | None],
    transport: _LibraryUploadTransport,
    s3_session: aiohttp.ClientSession,
    library_id: str,
    part_semaphore: asyncio.Semaphore,
) -> None:
    """Consume attachment work without allocating one task per file."""
    while True:
        try:
            index, attachment = next(work)
        except StopIteration:
            return
        receipts[index] = await upload_attachment_to_library(
            transport,
            s3_session,
            attachment,
            library_id,
            part_semaphore,
        )


async def upload_attachments_to_library(
    transport: _LibraryUploadTransport,
    attachments: list[PreparedAttachment],
    library_id: str,
) -> dict[str, LibraryDocumentCreateResponse]:
    """Upload a bounded batch and key queued receipts by attachment key.

    At most eight attachment workers and eight part requests run concurrently.
    On ``LibraryUploadError``, sibling work is cancelled and the re-raised error
    contains ``completed_receipts`` for documents already queued.
    """
    if not attachments:
        return {}

    _validate_prepared_attachments(attachments)

    part_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_UPLOADS)
    receipt_slots: list[LibraryDocumentCreateResponse | None] = [None] * len(attachments)
    work = iter(enumerate(attachments))

    async with aiohttp.ClientSession() as s3_session:
        try:
            await _gather_related(
                [
                    _upload_attachment_worker(
                        work,
                        receipt_slots,
                        transport,
                        s3_session,
                        library_id,
                        part_semaphore,
                    )
                    for _ in range(min(_MAX_CONCURRENT_UPLOADS, len(attachments)))
                ]
            )
        except LibraryUploadError as error:
            completed = {
                attachment.key: receipt
                for attachment, receipt in zip(attachments, receipt_slots, strict=True)
                if receipt is not None
            }
            raise error.with_completed_receipts(completed) from error

    completed_receipts: dict[str, LibraryDocumentCreateResponse] = {}
    for attachment, receipt in zip(attachments, receipt_slots, strict=True):
        if receipt is None:
            raise RuntimeError("Attachment worker exited without a receipt")
        completed_receipts[attachment.key] = receipt
    return completed_receipts
