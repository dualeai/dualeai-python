"""Auto-generated Pydantic models from the resolved schema compiler graph. Do not edit manually."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from re import fullmatch
from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import (
    AnyUrl,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    GetJsonSchemaHandler,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue, SkipJsonSchema
from pydantic_core import CoreSchema
from typing_extensions import TypeAliasType

from dualeai.models import authz as authz_module
from dualeai.models import document_id as document_id_module
from dualeai.models import library_id as library_id_module
from dualeai.models import problem_details as problem_details_module

__all__: list[str] = [
    "Library",
    "LibraryCreateRequest",
    "LibraryDeleteRequest",
    "LibraryDocumentCreateOperationRequest",
    "LibraryDocumentCreatePartRef",
    "LibraryDocumentCreateRequest",
    "LibraryDocumentCreateResponse",
    "LibraryDocumentDeleteRequest",
    "LibraryDocumentFailure",
    "LibraryDocumentGetRequest",
    "LibraryDocumentListRequest",
    "LibraryDocumentPage",
    "LibraryDocumentPreviewResponse",
    "LibraryDocumentTagsPatchRequest",
    "LibraryDocumentUploadPart",
    "LibraryDocumentUploadRequest",
    "LibraryDocumentUploadResponse",
    "LibraryErrorCode",
    "LibraryGetRequest",
    "LibraryListResponse",
    "LibraryPatchRequest",
    "LibraryPolicy",
    "LibraryResponseDocumentStatus",
    "LibraryRevision",
    "LibraryService",
    "LibrarySupportedImportFormatsResponse",
    "LibraryUpdateRequest",
    "LibraryWithRevision",
    "PublicIndexedDocument",
]
_PortableJsonValue = TypeAliasType(
    "_PortableJsonValue",
    Union[
        StrictBool,
        Annotated[StrictInt, Field(ge=-9007199254740991, le=9007199254740991)],
        Annotated[StrictFloat, Field(ge=-9007199254740991, le=9007199254740991, allow_inf_nan=False)],
        StrictStr,
        Annotated[list["_PortableJsonValue"], Field(strict=True)],
        Annotated[dict[StrictStr, "_PortableJsonValue"], Field(strict=True)],
        None,
    ],
)


def _validate_string_constraints_2(value: object) -> object:
    if not isinstance(value, str):
        if not isinstance(value, datetime):
            raise ValueError("date-time input must be an RFC 3339 string or a datetime")
        return value
    if fullmatch("\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d+)?(?:Z|[+-]\\d{2}:\\d{2})", value) is None:
        raise ValueError("string is not a canonical date-time")
    return value


def _validate_string_constraints_3(value: object) -> object:
    if not isinstance(value, str):
        return value
    if fullmatch("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", value) is None:
        raise ValueError("string is not a canonical uuid")
    return value


def _reject_explicit_null(value: object) -> object:
    if value is None:
        raise ValueError("explicit null is not allowed; omit the field instead")
    return value


@dataclass(frozen=True)
class _EnumSchemaMetadata:
    choices: tuple[tuple[str, Union[str, None], Union[str, None]], ...]

    def __get_pydantic_json_schema__(self, core_schema: CoreSchema, handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        json_schema = handler.resolve_ref_schema(handler(core_schema)).copy()
        json_schema.pop("enum", None)
        json_schema.pop("const", None)
        branches: list[JsonSchemaValue] = []
        for value, title, description in self.choices:
            branch: JsonSchemaValue = {"const": value}
            if title is not None:
                branch["title"] = title
            if description is not None:
                branch["description"] = description
            branches.append(branch)
        json_schema["oneOf"] = branches
        return json_schema


LibraryService = TypeAliasType(
    "LibraryService",
    Annotated[
        _PortableJsonValue,
        Field(title="LibraryService", description="Models for working with Libraries and their documents."),
    ],
)


class Library(BaseModel):
    """Library identity and current metadata."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    id: Annotated[
        library_id_module.LibraryId,
        Field(description="Stable Library identifier. It does not change when the path changes."),
    ]
    current_path: Annotated[
        Annotated[StrictStr, Field(min_length=1, max_length=300)],
        Field(
            description="Current free-form folder path. It can contain slash-separated segments and can be changed. Permissions name the path, and several Libraries can share one path and its permissions."
        ),
    ]
    current_tags: Annotated[
        Annotated[
            dict[
                StrictStr,
                Union[
                    StrictStr,
                    Annotated[StrictInt, Field(ge=-9007199254740991, le=9007199254740991)],
                    Annotated[StrictFloat, Field(ge=-9007199254740991, le=9007199254740991, allow_inf_nan=False)],
                    StrictBool,
                ],
            ],
            Field(strict=True),
        ],
        Field(
            description="Current tags. Values must be strings, integers, numbers, or booleans. The serialized object must not exceed 4 KB."
        ),
    ]
    created_at: Annotated[
        Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_2)],
        Field(description="Time when the Library was created."),
    ]
    deleted_at: Annotated[
        Union[Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_2)], None],
        Field(description="Time when the Library was deleted, or null while it is active."),
    ]


class LibraryCreateRequest(BaseModel):
    """Creates a Library at the requested path, or returns an active Library already using it, after IAM allows write on that path. Paths are not unique: Libraries at the same path share the permissions IAM gives that path."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    path: Annotated[
        Annotated[StrictStr, Field(min_length=1, max_length=300, pattern="^[^{}*]*$")],
        Field(
            description="Initial free-form folder path. It can contain slash-separated segments and does not require a prefix. It cannot contain {, } or *: IAM binds {self} and {tenant_id} in a path it checks, its resource contract refuses any other brace, and a policy pattern reads a trailing * as a wildcard."
        ),
    ]


class LibraryDeleteRequest(BaseModel):
    """Request to delete one Library by its stable identifier."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    library_id: Annotated[library_id_module.LibraryId, Field(description="Stable identifier of the Library to delete.")]


class LibraryDocumentCreatePartRef(BaseModel):
    """Receipt for one uploaded file part."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    part_number: Annotated[
        Annotated[StrictInt, Field(ge=-9007199254740991, le=9007199254740991), Field(ge=1, le=10000)],
        Field(description="1-indexed part number."),
    ]
    etag: Annotated[
        Annotated[StrictStr, Field(min_length=1)],
        Field(description="Entity tag returned by the corresponding part upload. Pass it back unchanged."),
    ]


class LibraryDocumentCreateRequest(BaseModel):
    """Adds an uploaded file to a Library and queues it for ingestion."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    upload_id: Annotated[
        Annotated[UUID, BeforeValidator(_validate_string_constraints_3)],
        Field(description="Identifier returned when the upload session was created."),
    ]
    filename: Annotated[
        Annotated[StrictStr, Field(min_length=1, max_length=500)],
        Field(description="Original filename supplied by the caller."),
    ]
    description: Annotated[
        Union[Annotated[StrictStr, Field(max_length=500)], None],
        Field(description="Optional description of the document. It does not affect document identity."),
    ] = None
    tags: Annotated[
        Union[
            Annotated[
                dict[
                    StrictStr,
                    Union[
                        StrictStr,
                        Annotated[StrictInt, Field(ge=-9007199254740991, le=9007199254740991)],
                        Annotated[StrictFloat, Field(ge=-9007199254740991, le=9007199254740991, allow_inf_nan=False)],
                        StrictBool,
                    ],
                ],
                Field(strict=True),
            ],
            SkipJsonSchema[None],
        ],
        BeforeValidator(_reject_explicit_null),
        Field(
            description="Optional tags. Values must be strings, integers, numbers, or booleans. The serialized object must not exceed 4 KB.",
            json_schema_extra={"default": {}},
        ),
    ] = Field(default_factory=lambda: None, validate_default=False, exclude_if=lambda value: value is None)
    content_sha256: Annotated[
        Annotated[StrictStr, Field(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$")],
        Field(description="Lower-case hex SHA-256 of the uploaded source bytes."),
    ]
    size_bytes: Annotated[
        Annotated[StrictInt, Field(ge=-9007199254740991, le=9007199254740991), Field(ge=1)],
        Field(description="Uploaded source size in bytes."),
    ]
    parts: Annotated[
        Annotated[list[LibraryDocumentCreatePartRef], Field(strict=True, min_length=1)],
        Field(description="Uploaded part references in order."),
    ]


class LibraryDocumentCreateOperationRequest(BaseModel):
    """Request to add one uploaded document to a Library."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    library_id: Annotated[
        library_id_module.LibraryId, Field(description="Stable identifier of the Library that will own the document.")
    ]
    document: LibraryDocumentCreateRequest


class LibraryDocumentCreateResponse(BaseModel):
    """Receipt for a document accepted for asynchronous ingestion."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    document_id: Annotated[document_id_module.DocumentId, Field(description="Identifier assigned to the document.")]
    library_id: Annotated[
        library_id_module.LibraryId, Field(description="Identifier of the Library that contains the document.")
    ]
    status: Annotated[
        Literal["queued"],
        Field(description="Initial ingestion status. It is always queued for a newly added document."),
    ]
    location: Annotated[
        Annotated[StrictStr, Field(min_length=1)],
        Field(
            description="Relative URI reference to read the document and poll its ingestion status. Resolve it against the create or restore request URL. It matches the Location response header."
        ),
    ]


class LibraryDocumentDeleteRequest(BaseModel):
    """Request deletion of one document by its Library and document identifiers."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    library_id: Annotated[
        library_id_module.LibraryId, Field(description="Stable identifier of the Library that owns the document.")
    ]
    document_id: Annotated[
        document_id_module.DocumentId, Field(description="Stable identifier of the document to delete.")
    ]


class LibraryErrorCode(str, Enum):
    """Stable code for a terminal document ingestion failure. Use the enclosing problem details for the specific cause and next action. EXPIRED_UPLOAD, HASH_MISMATCH, and SIZE_MISMATCH require a new or corrected upload; LIBRARY_DELETED and LIBRARY_ACCESS_REVOKED require choosing an active Library or restoring write access."""

    expired_upload = "EXPIRED_UPLOAD"
    hash_mismatch = "HASH_MISMATCH"
    size_mismatch = "SIZE_MISMATCH"
    format_unsupported = "FORMAT_UNSUPPORTED"
    corrupt_document = "CORRUPT_DOCUMENT"
    document_too_large = "DOCUMENT_TOO_LARGE"
    encrypted_document = "ENCRYPTED_DOCUMENT"
    extraction_failed = "EXTRACTION_FAILED"
    processing_timeout = "PROCESSING_TIMEOUT"
    document_processing_failed = "DOCUMENT_PROCESSING_FAILED"
    malware_detected = "MALWARE_DETECTED"
    library_deleted = "LIBRARY_DELETED"
    library_access_revoked = "LIBRARY_ACCESS_REVOKED"

    @property
    def display_title(self) -> Union[str, None]:
        return {
            LibraryErrorCode.expired_upload: "Upload Session Expired",
            LibraryErrorCode.hash_mismatch: None,
            LibraryErrorCode.size_mismatch: None,
            LibraryErrorCode.format_unsupported: None,
            LibraryErrorCode.corrupt_document: None,
            LibraryErrorCode.document_too_large: None,
            LibraryErrorCode.encrypted_document: None,
            LibraryErrorCode.extraction_failed: None,
            LibraryErrorCode.processing_timeout: None,
            LibraryErrorCode.document_processing_failed: "Document processing failed",
            LibraryErrorCode.malware_detected: "Malware Detected",
            LibraryErrorCode.library_deleted: "Library Deleted",
            LibraryErrorCode.library_access_revoked: "Library access revoked",
        }[self]

    @property
    def description(self) -> Union[str, None]:
        return {
            LibraryErrorCode.expired_upload: "Upload session expired before ingest could promote it",
            LibraryErrorCode.hash_mismatch: "A new or corrected upload is required.",
            LibraryErrorCode.size_mismatch: "A new or corrected upload is required.",
            LibraryErrorCode.format_unsupported: None,
            LibraryErrorCode.corrupt_document: None,
            LibraryErrorCode.document_too_large: None,
            LibraryErrorCode.encrypted_document: None,
            LibraryErrorCode.extraction_failed: None,
            LibraryErrorCode.processing_timeout: None,
            LibraryErrorCode.document_processing_failed: "The document could not be processed. Upload it again.",
            LibraryErrorCode.malware_detected: None,
            LibraryErrorCode.library_deleted: "Choose an active Library.",
            LibraryErrorCode.library_access_revoked: "The identity that uploaded the document lost write access to the library.",
        }[self]

    @staticmethod
    def __get_pydantic_json_schema__(core_schema: CoreSchema, handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        json_schema = _EnumSchemaMetadata(
            (
                ("EXPIRED_UPLOAD", "Upload Session Expired", "Upload session expired before ingest could promote it"),
                ("HASH_MISMATCH", None, "A new or corrected upload is required."),
                ("SIZE_MISMATCH", None, "A new or corrected upload is required."),
                ("FORMAT_UNSUPPORTED", None, None),
                ("CORRUPT_DOCUMENT", None, None),
                ("DOCUMENT_TOO_LARGE", None, None),
                ("ENCRYPTED_DOCUMENT", None, None),
                ("EXTRACTION_FAILED", None, None),
                ("PROCESSING_TIMEOUT", None, None),
                (
                    "DOCUMENT_PROCESSING_FAILED",
                    "Document processing failed",
                    "The document could not be processed. Upload it again.",
                ),
                ("MALWARE_DETECTED", "Malware Detected", None),
                ("LIBRARY_DELETED", "Library Deleted", "Choose an active Library."),
                (
                    "LIBRARY_ACCESS_REVOKED",
                    "Library access revoked",
                    "The identity that uploaded the document lost write access to the library.",
                ),
            )
        ).__get_pydantic_json_schema__(core_schema, handler)
        json_schema.update(
            {
                "description": "Stable code for a terminal document ingestion failure. Use the enclosing problem details for the specific cause and next action. EXPIRED_UPLOAD, HASH_MISMATCH, and SIZE_MISMATCH require a new or corrected upload; LIBRARY_DELETED and LIBRARY_ACCESS_REVOKED require choosing an active Library or restoring write access."
            }
        )
        return json_schema


class LibraryDocumentFailure(BaseModel):
    """Typed problem details for a terminal document ingestion failure."""

    model_config = ConfigDict(extra="allow", title=None, json_schema_extra=None)
    __pydantic_extra__: dict[str, _PortableJsonValue] = Field(init=False)
    type: Annotated[
        Union[StrictStr, SkipJsonSchema[None]],
        BeforeValidator(_reject_explicit_null),
        Field(
            description="URI identifying the problem type. 'about:blank' means that no dedicated problem type is available.",
            json_schema_extra={"default": "about:blank"},
        ),
    ] = Field(default_factory=lambda: None, validate_default=False, exclude_if=lambda value: value is None)
    title: Annotated[
        Annotated[StrictStr, Field(max_length=200)],
        Field(description="Stable human-readable summary of the failure type."),
    ]
    status: Annotated[
        Annotated[StrictInt, Field(ge=-9007199254740991, le=9007199254740991), Field(ge=100, le=599)],
        Field(description="HTTP-style status code classifying the failure."),
    ]
    detail: Annotated[
        Annotated[StrictStr, Field(max_length=1000)],
        Field(description="Human-readable explanation specific to this failure."),
    ]
    instance: Annotated[
        Union[StrictStr, None],
        Field(description="URI or path identifying this specific occurrence.", json_schema_extra={"default": None}),
    ] = None
    error_code: LibraryErrorCode


class LibraryDocumentGetRequest(BaseModel):
    """Request one document by its Library and document identifiers."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    library_id: Annotated[
        library_id_module.LibraryId, Field(description="Stable identifier of the Library that owns the document.")
    ]
    document_id: Annotated[
        document_id_module.DocumentId, Field(description="Stable identifier of the document to read.")
    ]


class LibraryDocumentListRequest(BaseModel):
    """Request one bounded page of documents from a Library."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    library_id: Annotated[
        library_id_module.LibraryId, Field(description="Stable identifier of the Library whose documents to list.")
    ]
    limit: Annotated[
        Annotated[StrictInt, Field(ge=-9007199254740991, le=9007199254740991), Field(ge=1, le=500)],
        Field(description="Maximum number of documents to return."),
    ]
    cursor: Annotated[
        Union[Annotated[StrictStr, Field(min_length=1, max_length=512)], None],
        Field(description="Opaque cursor from the prior page, or null for the first page."),
    ]


class LibraryResponseDocumentStatus(str, Enum):
    """Document ingestion status. Inspect failure for details when available."""

    queued = "queued"
    processing = "processing"
    ready = "ready"
    failed = "failed"

    @property
    def display_title(self) -> str:
        return {
            LibraryResponseDocumentStatus.queued: "Queued",
            LibraryResponseDocumentStatus.processing: "Processing",
            LibraryResponseDocumentStatus.ready: "Ready",
            LibraryResponseDocumentStatus.failed: "Failed",
        }[self]

    @property
    def description(self) -> str:
        return {
            LibraryResponseDocumentStatus.queued: "The document was accepted and is waiting to start.",
            LibraryResponseDocumentStatus.processing: "Document ingestion has started.",
            LibraryResponseDocumentStatus.ready: "Document ingestion completed.",
            LibraryResponseDocumentStatus.failed: "Document ingestion ended unsuccessfully.",
        }[self]

    @staticmethod
    def __get_pydantic_json_schema__(core_schema: CoreSchema, handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        json_schema = _EnumSchemaMetadata(
            (
                ("queued", "Queued", "The document was accepted and is waiting to start."),
                ("processing", "Processing", "Document ingestion has started."),
                ("ready", "Ready", "Document ingestion completed."),
                ("failed", "Failed", "Document ingestion ended unsuccessfully."),
            )
        ).__get_pydantic_json_schema__(core_schema, handler)
        json_schema.update({"description": "Document ingestion status. Inspect failure for details when available."})
        return json_schema


class PublicIndexedDocument(BaseModel):
    """Document metadata returned by Library document operations. It reports ingestion through status, progress_pct, and failure without returning source or extracted content."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    document_id: Annotated[
        document_id_module.DocumentId,
        Field(description="Deterministic document identifier (UUIDv5). Unique within the parent Library."),
    ]
    library_id: Annotated[library_id_module.LibraryId, Field(description="Library identifier that owns this document.")]
    filename: Annotated[
        Annotated[StrictStr, Field(min_length=1, max_length=500)], Field(description="Name of the uploaded file.")
    ]
    description: Annotated[
        Union[Annotated[StrictStr, Field(max_length=500)], None],
        Field(description="Description supplied when the document was added, or null when none was supplied."),
    ]
    content_type: Annotated[
        Union[StrictStr, None],
        Field(description="Detected document type, or null while the document is being processed."),
    ]
    size_bytes: Annotated[
        Annotated[StrictInt, Field(ge=-9007199254740991, le=9007199254740991), Field(ge=0)],
        Field(description="Original file size in bytes."),
    ]
    page_count: Annotated[
        Union[Annotated[StrictInt, Field(ge=-9007199254740991, le=9007199254740991), Field(ge=0)], None],
        Field(description="Number of pages, or null while the document is being processed."),
    ]
    created_at: Annotated[
        Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_2)],
        Field(description="Time when the document was added to the Library."),
    ]
    deleted_at: Annotated[
        Union[Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_2)], None],
        Field(
            description="Deletion time for a trashed document, or null for a live document. Trashed documents remain restorable for 30 days."
        ),
    ]
    status: LibraryResponseDocumentStatus
    progress_pct: Annotated[
        Union[Annotated[StrictInt, Field(ge=-9007199254740991, le=9007199254740991), Field(ge=0, le=100)], None],
        Field(
            description="Processing progress from 0 to 100. queued reports null, ready reports 100, and processing or failed reports the latest available value."
        ),
    ]
    failure: Annotated[
        Union[problem_details_module.ProblemDetails, None],
        Field(
            description="Problem details for a failed document. Usually null unless status is failed; it can remain null when no detailed cause is available."
        ),
    ]
    tags: Annotated[
        Annotated[
            dict[
                StrictStr,
                Union[
                    StrictStr,
                    Annotated[StrictInt, Field(ge=-9007199254740991, le=9007199254740991)],
                    Annotated[StrictFloat, Field(ge=-9007199254740991, le=9007199254740991, allow_inf_nan=False)],
                    StrictBool,
                ],
            ],
            Field(strict=True),
        ],
        Field(
            description="Document tags. Values must be strings, integers, numbers, or booleans. The serialized object must not exceed 4 KB."
        ),
    ]


class LibraryDocumentPage(BaseModel):
    """One bounded page of Library documents. Pass next_cursor unchanged to continue the same stable scan."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    documents: Annotated[
        Annotated[list[PublicIndexedDocument], Field(strict=True, max_length=500)],
        Field(description="Documents in stable cursor order. The array contains at most the requested limit."),
    ]
    next_cursor: Annotated[
        Union[Annotated[StrictStr, Field(min_length=1, max_length=512)], None],
        Field(description="Opaque cursor for the next page, or null when the scan is complete."),
    ]


class LibraryDocumentPreviewResponse(BaseModel):
    """Preview of a ready document's extracted text, limited to 250 characters."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    snippet: Annotated[
        StrictStr, Field(description="First 250 characters of extracted text, or all available text when shorter.")
    ]
    truncated: Annotated[
        StrictBool,
        Field(description="True when more than 250 characters are available; false at 250 characters or fewer."),
    ]


class LibraryDocumentTagsPatchRequest(BaseModel):
    """Replaces all tags on a Library document."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    tags: Annotated[
        Annotated[
            dict[
                StrictStr,
                Union[
                    StrictStr,
                    Annotated[StrictInt, Field(ge=-9007199254740991, le=9007199254740991)],
                    Annotated[StrictFloat, Field(ge=-9007199254740991, le=9007199254740991, allow_inf_nan=False)],
                    StrictBool,
                ],
            ],
            Field(strict=True),
        ],
        Field(
            description="Replacement tags. Values must be strings, integers, numbers, or booleans. The serialized object must not exceed 4 KB."
        ),
    ]


class LibraryDocumentUploadPart(BaseModel):
    """Instructions for uploading one byte range of a document file."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    part_number: Annotated[
        Annotated[StrictInt, Field(ge=-9007199254740991, le=9007199254740991), Field(ge=1, le=10000)],
        Field(description="1-indexed part number."),
    ]
    upload_url: Annotated[
        AnyUrl, Field(description="URL to which the client uploads this part with an HTTP PUT request.")
    ]
    offset: Annotated[
        Annotated[StrictInt, Field(ge=-9007199254740991, le=9007199254740991), Field(ge=0)],
        Field(description="Byte offset in the source file where this part starts."),
    ]
    length: Annotated[
        Annotated[StrictInt, Field(ge=-9007199254740991, le=9007199254740991), Field(ge=1)],
        Field(description="Number of bytes to read from the source file for this part."),
    ]


class LibraryDocumentUploadRequest(BaseModel):
    """Starts an upload for a document file. The returned upload must be completed before adding the document to a Library."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    size_bytes: Annotated[
        Annotated[StrictInt, Field(ge=-9007199254740991, le=9007199254740991), Field(ge=1)],
        Field(description="Size of the file to upload, in bytes."),
    ]


class LibraryDocumentUploadResponse(BaseModel):
    """Upload session and part instructions used to add a document to a Library."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    upload_id: Annotated[
        Annotated[UUID, BeforeValidator(_validate_string_constraints_3)],
        Field(description="Upload session identifier to pass when adding the uploaded file to a Library."),
    ]
    parts: Annotated[
        Annotated[list[LibraryDocumentUploadPart], Field(strict=True, min_length=1)],
        Field(description="Presigned URLs and byte ranges for each required upload part."),
    ]
    expires_at: Annotated[
        Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_2)],
        Field(description="Time after which the upload session can no longer be used to add a document."),
    ]
    parts_expires_at: Annotated[
        Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_2)],
        Field(description="Time after which the part upload URLs can no longer be used."),
    ]


class LibraryGetRequest(BaseModel):
    """Request to read one Library by its stable identifier."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    library_id: Annotated[library_id_module.LibraryId, Field(description="Stable identifier of the Library to read.")]


class LibraryWithRevision(BaseModel):
    """Current Library metadata returned by create, list, read, and update operations."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    id: Annotated[
        library_id_module.LibraryId,
        Field(description="Stable Library identifier. It does not change when the path changes."),
    ]
    path: Annotated[
        Annotated[StrictStr, Field(min_length=1, max_length=300)],
        Field(
            description="Current free-form folder path. Permissions name the path, and several Libraries can share one path and its permissions."
        ),
    ]
    tags: Annotated[
        Annotated[
            dict[
                StrictStr,
                Union[
                    StrictStr,
                    Annotated[StrictInt, Field(ge=-9007199254740991, le=9007199254740991)],
                    Annotated[StrictFloat, Field(ge=-9007199254740991, le=9007199254740991, allow_inf_nan=False)],
                    StrictBool,
                ],
            ],
            Field(strict=True),
        ],
        Field(description="Current Library tags."),
    ]
    updated_by: Annotated[
        Annotated[StrictStr, Field(min_length=1, max_length=200)],
        Field(description="Identity that made the latest update."),
    ]
    updated_at: Annotated[
        Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_2)],
        Field(description="Time of the latest update."),
    ]
    created_at: Annotated[
        Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_2)],
        Field(description="Time when the Library was created."),
    ]
    deleted_at: Annotated[
        Union[Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_2)], None],
        Field(description="Time when the Library was deleted, or null while it is active."),
    ]


class LibraryListResponse(BaseModel):
    """Public response body for listing the Libraries that the caller can access."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    libraries: Annotated[
        Annotated[list[LibraryWithRevision], Field(strict=True)],
        Field(description="Accessible Libraries ordered by creation time, newest first."),
    ]


class LibraryPatchRequest(BaseModel):
    """Updates a Library path, tags, or both. Omitted fields keep their current values."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra={"minProperties": 1})
    path: Annotated[
        Union[Annotated[StrictStr, Field(min_length=1, max_length=300, pattern="^[^{}*]*$")], SkipJsonSchema[None]],
        BeforeValidator(_reject_explicit_null),
        Field(
            description="New free-form folder path. It can contain slash-separated segments and does not require a prefix. It cannot contain {, } or *: IAM binds {self} and {tenant_id} in a path it checks, its resource contract refuses any other brace, and a policy pattern reads a trailing * as a wildcard."
        ),
    ] = Field(default_factory=lambda: None, validate_default=False, exclude_if=lambda value: value is None)
    tags: Annotated[
        Union[
            Annotated[
                dict[
                    StrictStr,
                    Union[
                        StrictStr,
                        Annotated[StrictInt, Field(ge=-9007199254740991, le=9007199254740991)],
                        Annotated[StrictFloat, Field(ge=-9007199254740991, le=9007199254740991, allow_inf_nan=False)],
                        StrictBool,
                    ],
                ],
                Field(strict=True),
            ],
            SkipJsonSchema[None],
        ],
        BeforeValidator(_reject_explicit_null),
        Field(
            description="Replacement tags. Values must be strings, integers, numbers, or booleans. The serialized object must not exceed 4 KB."
        ),
    ] = Field(default_factory=lambda: None, validate_default=False, exclude_if=lambda value: value is None)

    @model_validator(mode="before")
    @staticmethod
    def _validate_property_count(value: object) -> object:
        if isinstance(value, Mapping) and len(value) < 1:
            raise ValueError("object has fewer than minProperties")
        return value


LibraryPolicy = TypeAliasType(
    "LibraryPolicy",
    Annotated[
        authz_module.AuthzGrantInput,
        Field(
            description="Access grant on a Library's current path, shared by every Library at that path. Use library:read to read the Library, its revisions, its documents, and their previews; library:write to add documents, change metadata and tags, and restore trashed documents; library:delete to delete documents or the Library; and library:admin to manage its grants. library:* covers these four operations but not tenant-scoped library:upload."
        ),
    ],
)


class LibraryRevision(BaseModel):
    """A recorded version of a Library path and tags."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    library_id: Annotated[
        library_id_module.LibraryId, Field(description="Identifier of the Library this revision belongs to.")
    ]
    path: Annotated[
        Annotated[StrictStr, Field(min_length=1, max_length=300)],
        Field(description="Free-form folder path recorded in this revision."),
    ]
    tags: Annotated[
        Annotated[
            dict[
                StrictStr,
                Union[
                    StrictStr,
                    Annotated[StrictInt, Field(ge=-9007199254740991, le=9007199254740991)],
                    Annotated[StrictFloat, Field(ge=-9007199254740991, le=9007199254740991, allow_inf_nan=False)],
                    StrictBool,
                ],
            ],
            Field(strict=True),
        ],
        Field(description="Tags recorded in this revision. Values are strings, integers, numbers, or booleans."),
    ]
    updated_by: Annotated[
        Annotated[StrictStr, Field(min_length=1, max_length=200)], Field(description="Identity that made this update.")
    ]
    updated_at: Annotated[
        Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_2)],
        Field(description="Time when this revision was created."),
    ]


class Formats(BaseModel):
    model_config = ConfigDict(extra="forbid", title="Formats", json_schema_extra=None)
    extensions: Annotated[
        Annotated[list[StrictStr], Field(strict=True)],
        Field(description="File extensions (with leading dot), canonical first."),
    ]
    mimes: Annotated[
        Annotated[list[StrictStr], Field(strict=True)], Field(description="MIME types mapping to this category.")
    ]


class LibrarySupportedImportFormatsResponse(BaseModel):
    """Supported document upload formats grouped by category for use in file pickers."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    formats: Annotated[
        Annotated[dict[StrictStr, Formats], Field(strict=True)],
        Field(description="Map from each format category to its file extensions and MIME types."),
    ]


class LibraryUpdateRequest(BaseModel):
    """Request to update one Library by its stable identifier."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    library_id: Annotated[library_id_module.LibraryId, Field(description="Stable identifier of the Library to update.")]
    patch: LibraryPatchRequest
