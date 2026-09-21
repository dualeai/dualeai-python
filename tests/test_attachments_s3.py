"""Public Library upload integration tests against a local S3 service."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Generator, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple, Protocol

import aioboto3
import pytest
from aiohttp import web

from dualeai import DualeAISDK, prepare_attachments
from dualeai.config import DualeAIConfig
from dualeai.models.library import LibraryDocumentCreateRequest

TEST_BUCKET = "test-sdk-upload"
TEST_TENANT_ID = "tenant-test"
TEST_LIBRARY_ID = "018f35a8-2e90-7f2c-a6bb-9426f9c1f501"
TEST_TOKEN = "dualeai_test_token_12345_padded_to_32bytes"
MULTIPART_FIRST_PART_SIZE = 5 * 1024 * 1024


def _test_uuid(suffix: int) -> str:
    return f"018f35a8-2e90-7f2c-a6bb-9426f9c1f{suffix:03x}"


class S3Body(Protocol):
    async def read(self) -> bytes: ...


class S3Client(Protocol):
    async def create_bucket(self, *, Bucket: str) -> object: ...  # noqa: N803

    async def create_multipart_upload(self, *, Bucket: str, Key: str) -> Mapping[str, object]: ...  # noqa: N803

    async def complete_multipart_upload(
        self,
        *,
        Bucket: str,  # noqa: N803
        Key: str,  # noqa: N803
        UploadId: str,  # noqa: N803
        MultipartUpload: Mapping[str, object],  # noqa: N803
    ) -> object: ...

    async def generate_presigned_url(
        self,
        *,
        ClientMethod: str,  # noqa: N803
        Params: Mapping[str, object],  # noqa: N803
        ExpiresIn: int,  # noqa: N803
    ) -> str: ...

    async def get_object(self, *, Bucket: str, Key: str) -> Mapping[str, S3Body]: ...  # noqa: N803


class MotoS3(NamedTuple):
    client: S3Client
    bucket: str


class UploadRecord(NamedTuple):
    object_key: str
    multipart_upload_id: str | None


class DocumentCreateCall(NamedTuple):
    library_id: str
    document_id: str
    body: dict[str, object]


class LibraryHarness(NamedTuple):
    sdk: DualeAISDK
    boundary: LibraryBoundary


def _assert_tenant_path(request: web.Request) -> None:
    if request.match_info["tenant_id"] != TEST_TENANT_ID:
        raise AssertionError(f"Unexpected tenant path: {request.match_info['tenant_id']!r}")


async def _json_object_body(request: web.Request) -> dict[str, object]:
    body = await request.json()
    if not isinstance(body, dict):
        raise TypeError("Library request body must be a JSON object")
    return {str(key): value for key, value in body.items()}


class LibraryBoundary:
    """Local Library HTTP boundary that issues real moto presigned URLs."""

    def __init__(self, moto_s3: MotoS3) -> None:
        self._moto_s3 = moto_s3
        self._next_upload = 1
        self.uploads: dict[str, UploadRecord] = {}
        self.document_create_calls: list[DocumentCreateCall] = []

    async def create_upload(self, request: web.Request) -> web.Response:
        _assert_tenant_path(request)
        body = await _json_object_body(request)
        if set(body) != {"size_bytes"} or not isinstance(body["size_bytes"], int):
            raise AssertionError(f"Unexpected upload request: {body!r}")

        size_bytes = body["size_bytes"]
        upload_id = _test_uuid(0x600 + self._next_upload)
        self._next_upload += 1
        object_key = f"uploads/{upload_id}"
        if size_bytes > MULTIPART_FIRST_PART_SIZE:
            multipart = await self._moto_s3.client.create_multipart_upload(
                Bucket=self._moto_s3.bucket,
                Key=object_key,
            )
            multipart_upload_id = multipart.get("UploadId")
            if not isinstance(multipart_upload_id, str):
                raise AssertionError("S3 multipart creation omitted UploadId")
            part_lengths = [MULTIPART_FIRST_PART_SIZE, size_bytes - MULTIPART_FIRST_PART_SIZE]
            parts = []
            offset = 0
            for part_number, length in enumerate(part_lengths, start=1):
                url = await self._moto_s3.client.generate_presigned_url(
                    ClientMethod="upload_part",
                    Params={
                        "Bucket": self._moto_s3.bucket,
                        "Key": object_key,
                        "UploadId": multipart_upload_id,
                        "PartNumber": part_number,
                    },
                    ExpiresIn=3600,
                )
                parts.append(
                    {
                        "part_number": part_number,
                        "upload_url": url,
                        "offset": offset,
                        "length": length,
                    }
                )
                offset += length
        else:
            multipart_upload_id = None
            url = await self._moto_s3.client.generate_presigned_url(
                ClientMethod="put_object",
                Params={"Bucket": self._moto_s3.bucket, "Key": object_key},
                ExpiresIn=3600,
            )
            parts = [{"part_number": 1, "upload_url": url, "offset": 0, "length": size_bytes}]

        self.uploads[upload_id] = UploadRecord(object_key, multipart_upload_id)
        now = datetime.now(timezone.utc).isoformat()
        return web.json_response(
            {
                "upload_id": upload_id,
                "parts": parts,
                "expires_at": now,
                "parts_expires_at": now,
            },
            status=201,
        )

    async def create_document(self, request: web.Request) -> web.Response:
        _assert_tenant_path(request)
        body = await _json_object_body(request)
        document_request = LibraryDocumentCreateRequest.model_validate(body)
        upload_id = str(document_request.upload_id)
        if upload_id not in self.uploads:
            raise AssertionError(f"Unknown upload id: {upload_id!r}")
        upload = self.uploads[upload_id]
        if upload.multipart_upload_id is not None:
            completed_parts = [{"ETag": part.etag, "PartNumber": part.part_number} for part in document_request.parts]
            await self._moto_s3.client.complete_multipart_upload(
                Bucket=self._moto_s3.bucket,
                Key=upload.object_key,
                UploadId=upload.multipart_upload_id,
                MultipartUpload={"Parts": completed_parts},
            )

        library_id = request.match_info["library_id"]
        document_id = _test_uuid(0x700 + len(self.document_create_calls) + 1)
        self.document_create_calls.append(DocumentCreateCall(library_id, document_id, body))
        return web.json_response(
            {
                "document_id": document_id,
                "library_id": library_id,
                "status": "queued",
                "location": f"/v1/tenants/{TEST_TENANT_ID}/{library_id}/documents/{document_id}",
            },
            status=202,
        )

    async def read_uploaded_bytes(self, call: DocumentCreateCall) -> bytes:
        upload_id = call.body["upload_id"]
        if not isinstance(upload_id, str):
            raise AssertionError("Document create upload_id must be a string")
        response = await self._moto_s3.client.get_object(
            Bucket=self._moto_s3.bucket,
            Key=self.uploads[upload_id].object_key,
        )
        return await response["Body"].read()


@pytest.fixture(scope="module")
def moto_server() -> Generator[str]:
    from moto.server import ThreadedMotoServer

    server = ThreadedMotoServer(port=0)
    server.start()
    # CONNECT TO LOOPBACK, NOT TO THE BIND HOST. `get_host_and_port()` reports moto's
    # BIND address, which defaults to the `0.0.0.0` wildcard, and a wildcard is not a
    # routable destination: `connect()` to it is undefined and macOS intermittently
    # answers `OSError: [Errno 49] Can't assign requested address` under xdist load.
    # Observed once as a lone `ClientConnectorError: Cannot connect to host 0.0.0.0`
    # in a suite that otherwise passed.
    _, port = server.get_host_and_port()
    yield f"http://127.0.0.1:{port}"
    server.stop()


@pytest.fixture
async def moto_s3(moto_server: str, request: pytest.FixtureRequest) -> AsyncIterator[MotoS3]:
    test_name_hash = hashlib.sha256(request.node.name.encode()).hexdigest()[:12]
    bucket = f"{TEST_BUCKET}-{test_name_hash}"
    session = aioboto3.Session()
    async with session.client(
        "s3",
        endpoint_url=moto_server,
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    ) as client:
        await client.create_bucket(Bucket=bucket)
        yield MotoS3(client=client, bucket=bucket)


@pytest.fixture
async def library_harness(moto_s3: MotoS3, aiohttp_server) -> AsyncIterator[LibraryHarness]:
    boundary = LibraryBoundary(moto_s3)
    application = web.Application()
    application.router.add_post("/libraries/v1/tenants/{tenant_id}/document-uploads", boundary.create_upload)
    application.router.add_post(
        "/libraries/v1/tenants/{tenant_id}/{library_id}/documents",
        boundary.create_document,
    )
    server = await aiohttp_server(application)
    sdk = DualeAISDK(
        config=DualeAIConfig(
            endpoint=f"http://{server.host}:{server.port}",
            token=TEST_TOKEN,
            tenant_id=TEST_TENANT_ID,
        ),
        auto_start=False,
    )
    try:
        yield LibraryHarness(sdk=sdk, boundary=boundary)
    finally:
        await sdk.cleanup()


def _call_for_filename(boundary: LibraryBoundary, filename: str) -> DocumentCreateCall:
    return next(call for call in boundary.document_create_calls if call.body["filename"] == filename)


@pytest.mark.integration
async def test_public_upload_preserves_bytes_and_document_metadata(
    library_harness: LibraryHarness,
    tmp_path: Path,
) -> None:
    varied_content = bytes(range(256)) * 513 + b"final"
    varied_path = tmp_path / "varied.bin"
    varied_path.write_bytes(varied_content)
    one_byte_path = tmp_path / "one.bin"
    one_byte_path.write_bytes(b"\xff")
    attachments = prepare_attachments(
        [
            (varied_path, "Varied binary content"),
            (one_byte_path, "One byte"),
        ]
    )

    receipts = await library_harness.sdk.libraries.upload(TEST_LIBRARY_ID, attachments)

    assert list(receipts) == [attachment.key for attachment in attachments]
    for attachment in attachments:
        call = _call_for_filename(library_harness.boundary, attachment.filename)
        stored = await library_harness.boundary.read_uploaded_bytes(call)
        assert stored == attachment.path.read_bytes()
        assert call.library_id == TEST_LIBRARY_ID
        assert call.body["description"] == attachment.description
        assert call.body["size_bytes"] == attachment.size
        assert call.body["content_sha256"] == hashlib.sha256(stored).hexdigest()
        assert str(receipts[attachment.key].document_id) == call.document_id


@pytest.mark.integration
async def test_public_upload_preserves_true_multipart_bytes_and_opaque_etags(
    library_harness: LibraryHarness,
    tmp_path: Path,
) -> None:
    content = bytes(range(256)) * ((MULTIPART_FIRST_PART_SIZE // 256) + 513) + b"multipart-final"
    path = tmp_path / "multipart.bin"
    path.write_bytes(content)
    attachment = prepare_attachments([(path, "Multipart payload")])[0]

    receipts = await library_harness.sdk.libraries.upload(TEST_LIBRARY_ID, [attachment])

    call = _call_for_filename(library_harness.boundary, attachment.filename)
    stored = await library_harness.boundary.read_uploaded_bytes(call)
    document_request = LibraryDocumentCreateRequest.model_validate(call.body)
    assert stored == content
    assert [part.part_number for part in document_request.parts] == [1, 2]
    assert all(part.etag for part in document_request.parts)
    assert str(receipts[attachment.key].document_id) == call.document_id
