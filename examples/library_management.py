"""Manage a persistent Library with a synthetic document.

This live example requires ``DUALEAI_TOKEN`` and ``DUALEAI_TENANT_ID``, but no
Agent identifier. The caller needs Tenant-scoped ``library:upload`` and access
from which ``library:write`` and ``library:read`` can be derived for the new
Library. Run it with ``python examples/library_management.py``.

The Library and its document remain until a Human Identity deletes the Library in the
Dashboard with ``library:delete`` and valid ``aal3`` authentication. An SDK API-token
session has AAL2 and cannot perform whole-Library deletion.

The automated test suite does not execute this example.
"""

import asyncio
import contextlib
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from dualeai import (
    LibraryCreateRequest,
    LibraryDocumentGetRequest,
    LibraryGetRequest,
    LibraryResponseDocumentStatus,
    create_sdk,
    prepare_attachments,
)


async def main() -> None:
    """Create, upload to, and inspect a Library; print the cleanup handoff."""
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
                print(  # noqa: T201
                    f"Cleanup required: Library {library.id} ({library.path}) remains. "
                    "Ask a Human Identity with library:delete to delete it in the Dashboard "
                    "with valid aal3 authentication; sign in when prompted."
                )


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
