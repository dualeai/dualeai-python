"""Auto-generated Pydantic models from the resolved schema compiler graph. Do not edit manually."""

from __future__ import annotations

from enum import Enum
from re import fullmatch
from typing import Annotated, Union
from uuid import UUID

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

from dualeai.models import identity as identity_module
from dualeai.models import tenant_id as tenant_id_module

__all__: list[str] = [
    "Authz",
    "AuthzBindingSubject",
    "AuthzGrantAction",
    "AuthzGrantDecision",
    "AuthzGrantInput",
    "AuthzGrantSubject",
    "AuthzGrantTargetSubject",
    "AuthzGrantWarning",
    "AuthzGrantWriteResponse",
    "AuthzGroupName",
    "AuthzPolicyName",
    "AuthzResourceGrant",
    "AuthzResourceGrantListResponse",
    "AuthzResourceId",
    "AuthzResourceType",
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
        return value
    if fullmatch("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", value) is None:
        raise ValueError("string is not a canonical uuid")
    return value


def _validate_string_constraints_3(value: object) -> object:
    if not isinstance(value, str):
        return value
    if fullmatch("\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d+)?(?:Z|[+-]\\d{2}:\\d{2})", value) is None:
        raise ValueError("string is not a canonical date-time")
    return value


Authz = TypeAliasType(
    "Authz",
    Annotated[
        _PortableJsonValue,
        Field(title="Authz", description="Authorization grants and identifiers for describing access to resources."),
    ],
)
AuthzBindingSubject = TypeAliasType(
    "AuthzBindingSubject",
    Annotated[
        Annotated[
            StrictStr, Field(min_length=3, max_length=300, pattern="^(group|user|agent):[A-Za-z0-9][A-Za-z0-9_-]*$")
        ],
        Field(
            description="Identity or group that receives a policy binding. Use a group, user, or agent prefix followed by one identifier. This value names one subject; wildcard patterns are not accepted.",
            examples=[
                "group:019f521c-f000-7000-8000-000000000101",
                "user:019ee9b4-9a94-7db3-97c5-35a7a8ecfd42",
                "agent:finance",
            ],
        ),
    ],
)
AuthzGrantAction = TypeAliasType(
    "AuthzGrantAction",
    Annotated[
        Annotated[StrictStr, Field(min_length=1, max_length=100, pattern="^(\\*|[A-Za-z0-9][A-Za-z0-9:_-]*\\*?)$")],
        Field(
            description="Action pattern covered by a grant. Use `*` for every action, a complete action such as `library:read`, or one trailing `*` for an action prefix such as `library:*`."
        ),
    ],
)


class AuthzGrantDecision(str, Enum):
    """Effect applied when a grant matches: `allow` permits the action and `deny` refuses it. A matching deny takes precedence over matching allows."""

    allow = "allow"
    deny = "deny"

    @staticmethod
    def __get_pydantic_json_schema__(core_schema: CoreSchema, handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        json_schema = handler.resolve_ref_schema(handler(core_schema))
        json_schema.update(
            {
                "description": "Effect applied when a grant matches: `allow` permits the action and `deny` refuses it. A matching deny takes precedence over matching allows."
            }
        )
        return json_schema


AuthzGrantTargetSubject = TypeAliasType(
    "AuthzGrantTargetSubject",
    Annotated[
        Annotated[
            StrictStr,
            Field(
                min_length=1,
                max_length=300,
                pattern="^(\\*|user:[A-Za-z0-9][A-Za-z0-9|_-]*|(user|agent):([A-Za-z0-9][A-Za-z0-9_-]*\\*?|\\*))$",
            ),
        ],
        Field(
            description="Identity or identity pattern that can receive a grant. It accepts every AuthzGrantSubject form. A concrete user may also use an identity-provider alias such as `user:auth0|123`; user wildcard patterns cannot include an identity-provider alias."
        ),
    ],
)


class AuthzGrantInput(BaseModel):
    """Grant to apply to a resource, including its subject, action pattern, and effect."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    subject: Annotated[
        AuthzGrantTargetSubject,
        Field(
            description="Identity or identity pattern that receives the grant. A concrete user may use an identity-provider alias."
        ),
    ]
    action: Annotated[AuthzGrantAction, Field(description="Action or action prefix covered by the grant.")]
    effect: Annotated[
        AuthzGrantDecision,
        Field(description="Whether matching access is allowed or denied. A matching deny takes precedence."),
    ]


AuthzGrantSubject = TypeAliasType(
    "AuthzGrantSubject",
    Annotated[
        Annotated[
            StrictStr,
            Field(min_length=1, max_length=300, pattern="^(\\*|(user|agent):([A-Za-z0-9][A-Za-z0-9_-]*\\*?|\\*))$"),
        ],
        Field(
            description="Canonical identity or identity pattern named by a grant. Use `*` for all identities, `{kind}:*` for every identity of one kind, `{kind}:{identifier}` for one user or agent, or a single trailing `*` to match an identifier prefix. Group subjects and identity-provider aliases are not accepted."
        ),
    ],
)


class AuthzGrantWarning(str, Enum):
    """Non-blocking condition reported after a grant is created. The grant succeeded; use the warning to review its effect."""

    deny_covers_the_writer = "deny_covers_the_writer"

    @staticmethod
    def __get_pydantic_json_schema__(core_schema: CoreSchema, handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        json_schema = handler.resolve_ref_schema(handler(core_schema))
        json_schema.update(
            {
                "description": "Non-blocking condition reported after a grant is created. The grant succeeded; use the warning to review its effect."
            }
        )
        return json_schema


class AuthzGrantWriteResponse(BaseModel):
    """Result of creating a grant, including its identifier, creation time, and any non-blocking warnings."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    id: Annotated[
        Annotated[UUID, BeforeValidator(_validate_string_constraints_2)],
        Field(description="Unique identifier assigned to the grant."),
    ]
    created_at: Annotated[
        Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_3)],
        Field(description="Time when the grant was created."),
    ]
    warnings: Annotated[
        Annotated[list[AuthzGrantWarning], Field(strict=True)],
        Field(
            description="Non-blocking conditions to review after the grant is created. The list is empty when no warning applies."
        ),
    ]


AuthzGroupName = TypeAliasType(
    "AuthzGroupName",
    Annotated[
        Annotated[StrictStr, Field(min_length=1, max_length=255, pattern="^[a-z0-9]+(?:[_-][a-z0-9]+)*$")],
        Field(
            description="Lowercase name of an authorization group. Separate words with hyphens or underscores.",
            examples=["team-developers", "eng-oncall", "billing_admins"],
        ),
    ],
)
AuthzPolicyName = TypeAliasType(
    "AuthzPolicyName",
    Annotated[
        Annotated[StrictStr, Field(min_length=1, max_length=255, pattern="^[a-z0-9]+(?:[_-][a-z0-9]+)*$")],
        Field(
            description="Lowercase name of an authorization policy. Separate words with hyphens or underscores.",
            examples=["team-admin", "tenant-viewer", "billing_readonly"],
        ),
    ],
)
AuthzResourceId = TypeAliasType(
    "AuthzResourceId",
    Annotated[
        Annotated[StrictStr, Field(min_length=1, max_length=300)],
        Field(description="Identifier of the resource covered by a grant. Treat the value as opaque."),
    ],
)
AuthzResourceType = TypeAliasType(
    "AuthzResourceType",
    Annotated[
        Annotated[StrictStr, Field(pattern="^[a-z][a-z0-9_]*$")],
        Field(description="Resource category used in authorization checks, such as `library`."),
    ],
)


class AuthzResourceGrant(BaseModel):
    """Grant currently associated with one resource. It identifies the tenant, resource, subject, action pattern, effect, creator, and creation time."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    tenant_id: Annotated[tenant_id_module.TenantId, Field(description="Tenant that owns the resource and grant.")]
    resource_type: Annotated[
        AuthzResourceType, Field(description="Category of resource covered by the grant, such as `library`.")
    ]
    resource_id: Annotated[
        AuthzResourceId,
        Field(description="Identifier of the resource covered by the grant. Treat the value as opaque."),
    ]
    subject: Annotated[
        AuthzGrantSubject, Field(description="Canonical identity or identity pattern that receives the grant.")
    ]
    action: Annotated[AuthzGrantAction, Field(description="Action or action prefix covered by the grant.")]
    effect: Annotated[
        AuthzGrantDecision,
        Field(description="Whether matching access is allowed or denied. A matching deny takes precedence."),
    ]
    id: Annotated[
        Annotated[UUID, BeforeValidator(_validate_string_constraints_2)],
        Field(description="Unique identifier of this grant."),
    ]
    created_by: Annotated[
        identity_module.CanonicalActor,
        Field(
            description="Canonical user or agent that created the grant. An interservice credential is not a tenant actor."
        ),
    ]
    created_at: Annotated[
        Annotated[AwareDatetime, BeforeValidator(_validate_string_constraints_3)],
        Field(description="Time when the grant was created."),
    ]


class AuthzResourceGrantListResponse(BaseModel):
    """Active grants associated with one resource."""

    model_config = ConfigDict(extra="forbid", title=None, json_schema_extra=None)
    grants: Annotated[
        Annotated[list[AuthzResourceGrant], Field(strict=True)],
        Field(description="Active grants associated with the requested resource."),
    ]
