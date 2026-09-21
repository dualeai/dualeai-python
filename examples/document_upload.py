"""Upload a document and ask the agent to analyze it through Library APIs.

Usage:
    export DUALEAI_TOKEN=dualeai_your-token-here
    export DUALEAI_TENANT_ID=tenant_example
    export DUALEAI_AGENT_ID=agent_example
    # Optional for local or test stacks; DualeAIConfig defaults to https://api.duale.ai.
    export DUALEAI_ENDPOINT=http://localhost:8000
    uv run python examples/document_upload.py path/to/document.pdf
"""

import asyncio
import sys
import time
from pathlib import Path
from uuid import uuid4

from dualeai import (
    DualeAIConfig,
    DualeAISDK,
    LibraryDocumentGetRequest,
    LibraryResponseDocumentStatus,
    ask,
)


def parse_args() -> Path:
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
    file_path = parse_args()

    async with DualeAISDK(config=DualeAIConfig(), auto_start=False) as sdk:
        # Step 1: Prepare attachment metadata (instant, no I/O beyond stat)
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
            agent_id=sdk.agent_id,
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

        # Step 3: Ask only after the document is readable. When an image-capable
        # model handles the task, images embedded in the document (charts,
        # diagrams, scanned figures) reach the model as input, not only their
        # extracted text — so a question about a figure is answered from the
        # figure itself.
        print("\nAsking agent to summarize the document and its figures...")  # noqa: T201
        prompt = (
            "Summarize the attached document. If it contains figures, charts,"
            " or diagrams, describe each one and what it shows."
        )
        response = await ask(
            action=prompt,
            attachments=attachments,
            streaming=True,
            request_id=task_id,
            sdk=sdk,
        )

        # Step 4: Wait for result
        print("\n--- Agent response ---")  # noqa: T201
        result = await response.model()
        print(result)  # noqa: T201

        print(f"\n--- Done (task_id={response.task_id}) ---")  # noqa: T201


if __name__ == "__main__":
    asyncio.run(main())
