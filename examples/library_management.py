"""Manage a persistent Library with a synthetic document.

This live example requires ``DUALEAI_TOKEN`` and ``DUALEAI_TENANT_ID`` plus
Library access, but no Agent identifier. Run it with
``python examples/library_management.py``. It creates and later deletes a
Library in the configured environment.

The automated test suite does not execute this example.
"""

import asyncio
import contextlib
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from dualeai import (
    LibraryCreateRequest,
    LibraryDeleteRequest,
    LibraryDocumentGetRequest,
    LibraryGetRequest,
    LibraryResponseDocumentStatus,
    create_sdk,
    prepare_attachments,
)


async def main() -> None:
    """Create, upload to, inspect, and clean up one persistent Library."""
    with TemporaryDirectory(prefix="dualeai-library-") as directory:
        source = Path(directory) / "synthetic-policy.txt"
        source.write_text(
            "Synthetic retention policy\nKeep example records for 30 days.\n",
            encoding="utf-8",
        )
        attachment = prepare_attachments([(source, "Synthetic retention policy")])[0]

        async with create_sdk() as sdk:
            library = await sdk.libraries.create(LibraryCreateRequest(path=f"examples/library-management/{uuid4()}"))
            print(f"Created Library {library.id}")  # noqa: T201

            try:
                fetched = await sdk.libraries.get(LibraryGetRequest(library_id=library.id))
                print(f"Library path: {fetched.path}")  # noqa: T201

                receipts = await sdk.libraries.upload(library.id, [attachment])
                receipt = receipts[attachment.key]
                request = LibraryDocumentGetRequest(
                    library_id=library.id,
                    document_id=receipt.document_id,
                )
                document = await sdk.libraries.wait_for_document(request, timeout=360)
                if document.status is LibraryResponseDocumentStatus.failed:
                    raise RuntimeError(f"Document ingestion failed: {document.failure}")

                current = await sdk.libraries.get_document(request)
                print(f"Document {current.document_id} is {current.status.value}")  # noqa: T201
            finally:
                await sdk.libraries.delete(LibraryDeleteRequest(library_id=library.id))
                print(f"Deleted Library {library.id}")  # noqa: T201


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
