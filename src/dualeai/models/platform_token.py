"""Auto-generated Pydantic models from the resolved schema compiler graph. Do not edit manually."""

from __future__ import annotations

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
            description="Issuance of the platform token, and the vocabulary that travels with it. One token opens every Duale AI service, and the caller derives its secret rather than receiving one.",
        ),
    ],
)


class AssuranceLevel(str, Enum):
    """The assurance certified at issuance. An Agent Identity is capped at 'aal2'. A Human Identity can obtain 'aal1', 'aal2' or 'aal3' from qualified original authentication evidence. Each route retains its minimum level and IAM permission checks. Product 'aal3' requires public-key multi-factor authentication within its original twelve-hour window; it permits synced/exportable passkeys and waives authenticator FIPS certification, so it does not claim NIST AAL3 conformity."""

    aal1 = "aal1"
    aal2 = "aal2"
    aal3 = "aal3"

    @staticmethod
    def __get_pydantic_json_schema__(core_schema: CoreSchema, handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        json_schema = handler.resolve_ref_schema(handler(core_schema))
        json_schema.update(
            {
                "description": "The assurance certified at issuance. An Agent Identity is capped at 'aal2'. A Human Identity can obtain 'aal1', 'aal2' or 'aal3' from qualified original authentication evidence. Each route retains its minimum level and IAM permission checks. Product 'aal3' requires public-key multi-factor authentication within its original twelve-hour window; it permits synced/exportable passkeys and waives authenticator FIPS certification, so it does not claim NIST AAL3 conformity."
            }
        )
        return json_schema


class PlatformTokenIssueRequest(BaseModel):
    """Ask for a platform token. A Human Identity identifies with a Keycloak access token; an Agent Identity identifies with its API token. The caller supplies the public half of a new ephemeral key pair. Both parties derive the pre-shared key, which is absent from the public request and response."""

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
            description="Public handle for one platform token, carried in the transport's pre-shared-key identifier field with every protected request. It is random and unrelated to the key, and it correlates requests made with that token. Knowing it permits withdrawal of the token, but does not permit protected requests: those require ciphertext that the resolved key opens."
        ),
    ],
)


class PlatformTokenIssueResponse(BaseModel):
    """An issued platform token. It carries NO secret: the caller derives the pre-shared key itself from 'issuer_public_key' and the private half it kept. Present 'psk_id' as the hpke-http key identifier and the derived key as the pre-shared key."""

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
            description="The canonical subject this token acts as, resolved at issuance: 'user:{user_id}' or 'agent:{agent_id}'. Resolved here rather than at each use, so a downstream service never sees an identity-provider alias."
        ),
    ]
    tenant_id: Annotated[
        tenant_id_module.TenantId, Field(description="The tenant every request on this token acts within.")
    ]
    assurance_level: AssuranceLevel
    valid_until: Annotated[
        Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_2)],
        Field(
            description="When the token stops resolving. Revocation deletes the record and takes effect before this instant, so a client must handle a refusal at any time."
        ),
    ]
