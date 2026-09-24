"""Upload a document and reference it in one live Task.

Requires ``DUALEAI_TOKEN``, ``DUALEAI_TENANT_ID``, ``DUALEAI_AGENT_ID``,
access to a configured model, and a synthetic input file. Run with
``python examples/document_upload.py path/to/document.pdf``. The example creates
a call-scoped Library and uploads the file; it is not executed by the automated
test suite.

Set ``DUALEAI_ENDPOINT`` only when your access instructions name a non-default
environment.
"""

import asyncio
import contextlib
import sys
import time
from pathlib import Path
from uuid import uuid4

from dualeai import (
    LibraryDocumentGetRequest,
    LibraryResponseDocumentStatus,
    ask,
    create_sdk,
)


def parse_args() -> Path:
    """Return the existing file path supplied on the command line."""
    if len(sys.argv) < 2:  # noqa: PLR2004
        print("Usage: python examples/document_upload.py <file_path>")  # noqa: T201
        sys.exit(1)

    file_path = Path(sys.argv[1])

    if not file_path.exists():
        print(f"File not found: {file_path}")  # noqa: T201
        sys.exit(1)

    file_size_mb = file_path.stat().st_size / 1024 / 1024
    print(f"File: {file_path.name} ({file_size_mb:.1f} MB)")  # noqa: T201

    return file_path


async def main() -> None:
    """Upload, await ingestion, and submit one Task referencing the document."""
    file_path = parse_args()

    async with create_sdk() as sdk:
        # Step 1: Validate the path and record attachment metadata without
        # reading the file contents.
        attachments = sdk.prepare_attachments(
            [
                (file_path, f"Document: {file_path.name}"),
            ]
        )
        task_id = str(uuid4())
        print(f"Prepared: key={attachments[0].key}, task_id={task_id}")  # noqa: T201

        # Step 2: Resolve the call-scope Library, create an upload session, then
        # stream file parts to the presigned S3 URLs.
        start = time.monotonic()
        receipts = await sdk.upload_attachments(
            task_id=task_id,
            attachments=attachments,
        )
        print(f"Uploaded in {time.monotonic() - start:.1f}s")  # noqa: T201

        receipt = receipts[attachments[0].key]
        document = await sdk.libraries.wait_for_document(
            LibraryDocumentGetRequest(
                library_id=receipt.library_id,
                document_id=receipt.document_id,
            )
        )
        if document.status is LibraryResponseDocumentStatus.failed:
            raise RuntimeError(f"Document ingestion failed: {document.failure}")

        # Step 3: Ask only after the document is readable. This example proves
        # upload and Task-reference wiring; interpretation depends on the
        # provisioned Platform/model capabilities.
        print("\nAsking agent to summarize the document...")  # noqa: T201
        prompt = "Summarize the attached document and list its main sections."
        response = await ask(
            action=prompt,
            attachments=attachments,
            request_id=task_id,
            sdk=sdk,
        )

        # Step 4: Wait for the authoritative result.
        print("\n--- Task result ---")  # noqa: T201
        result = await response.model()
        print(result)  # noqa: T201

        print(f"\n--- Done (task_id={response.task_id}) ---")  # noqa: T201


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
