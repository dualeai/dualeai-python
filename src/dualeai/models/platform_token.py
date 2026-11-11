"""Auto-generated Pydantic models from the resolved schema compiler graph. Do not edit manually."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from re import fullmatch
from typing import Annotated, Union

from pydantic import (
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
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema
from typing_extensions import TypeAliasType

from dualeai.models import tenant_id as tenant_id_module

__all__: list[str] = [
    "AssuranceLevel",
    "PlatformToken",
    "PlatformTokenIssueRequest",
    "PlatformTokenIssueResponse",
    "PlatformTokenKeyIdentifier",
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


def _validate_string_constraints_2(value: object) -> object:
    if not isinstance(value, str):
        if not isinstance(value, datetime):
            raise ValueError("date-time input must be an RFC 3339 string or a datetime")
        return value
    if fullmatch("\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d+)?(?:Z|[+-]\\d{2}:\\d{2})", value) is None:
        raise ValueError("string is not a canonical date-time")
    return value


PlatformToken = TypeAliasType(
    "PlatformToken",
    Annotated[
        _PortableJsonValue,
        Field(
            title="PlatformToken",
            description="Issuance of the platform token and the values that travel with it. The caller derives the token secret instead of receiving it.",
        ),
    ],
)


class AssuranceLevel(str, Enum):
    """Duale AI assurance level of the sign-in that obtained this token. An Agent Identity reaches at most 'aal2'."""

    aal1 = "aal1"
    aal2 = "aal2"
    aal3 = "aal3"

    @property
    def display_title(self) -> Union[str, None]:
        return {AssuranceLevel.aal1: None, AssuranceLevel.aal2: None, AssuranceLevel.aal3: None}[self]

    @property
    def description(self) -> Union[str, None]:
        return {
            AssuranceLevel.aal1: None,
            AssuranceLevel.aal2: None,
            AssuranceLevel.aal3: "Highest Duale AI assurance level: public-key multi-factor authentication.",
        }[self]

    @staticmethod
    def __get_pydantic_json_schema__(core_schema: CoreSchema, handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        json_schema = _EnumSchemaMetadata(
            (
                ("aal1", None, None),
                ("aal2", None, None),
                ("aal3", None, "Highest Duale AI assurance level: public-key multi-factor authentication."),
            )
        ).__get_pydantic_json_schema__(core_schema, handler)
        json_schema.update(
            {
                "description": "Duale AI assurance level of the sign-in that obtained this token. An Agent Identity reaches at most 'aal2'."
            }
        )
        return json_schema


class PlatformTokenIssueRequest(BaseModel):
    """Ask for a platform token. A Human Identity identifies with an identity-provider access token; an Agent Identity identifies with its API token. The caller supplies the public half of a new ephemeral key pair."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    tenant_id: Annotated[
        Union[tenant_id_module.TenantId, None],
        Field(
            description="The tenant this token acts within. Required for a Human Identity, which can hold several tenant memberships. Optional for an Agent Identity, whose API token already identifies its single tenant. An explicit tenant must match that token's tenant."
        ),
    ] = None
    client_public_key: Annotated[
        Annotated[StrictStr, Field(pattern="^[0-9a-f]{64}$")],
        Field(
            description="The caller's ephemeral X25519 public key, 32 bytes as 64 lowercase hex characters. A fresh pair per issuance: reusing one makes two tokens share a pre-shared key."
        ),
    ]


PlatformTokenKeyIdentifier = TypeAliasType(
    "PlatformTokenKeyIdentifier",
    Annotated[
        Annotated[StrictStr, Field(pattern="^[0-9a-f]{128}$")],
        Field(
            description="Public identifier of one platform token, sent with every request made with it. It is not a secret."
        ),
    ],
)


class PlatformTokenIssueResponse(BaseModel):
    """An issued platform token. The caller derives the token's pre-shared key from `issuer_public_key` and its own ephemeral private key, then sends `psk_id` with that key on each request."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    psk_id: PlatformTokenKeyIdentifier
    issuer_public_key: Annotated[
        Annotated[StrictStr, Field(pattern="^[0-9a-f]{64}$")],
        Field(
            description="The issuance public key, 32 bytes as 64 lowercase hex characters. The caller completes the exchange with this and its own ephemeral private key."
        ),
    ]
    identity: Annotated[
        StrictStr,
        Field(
            description="The canonical subject this token acts as, resolved at issuance: 'user:{user_id}' or 'agent:{agent_id}'. It never holds an identity-provider alias."
        ),
    ]
    tenant_id: Annotated[
        tenant_id_module.TenantId, Field(description="The tenant every request on this token acts within.")
    ]
    assurance_level: AssuranceLevel
    valid_until: Annotated[
        Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_2)],
        Field(
            description="When the token expires. A revoked token stops working earlier, so a caller must handle a refusal at any time."
        ),
    ]
