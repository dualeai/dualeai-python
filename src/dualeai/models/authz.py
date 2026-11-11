"""Auto-generated Pydantic models from the resolved schema compiler graph. Do not edit manually."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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
    "AuthzPermissionAction",
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
        return value
    if fullmatch("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", value) is None:
        raise ValueError("string is not a canonical uuid")
    return value


def _validate_string_constraints_3(value: object) -> object:
    if not isinstance(value, str):
        if not isinstance(value, datetime):
            raise ValueError("date-time input must be an RFC 3339 string or a datetime")
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
                "user:019f521c-f000-7000-8000-000000000201",
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
    """Effect applied when a grant matches. A matching deny takes precedence over matching allows."""

    allow = "allow"
    deny = "deny"

    @property
    def display_title(self) -> str:
        return {AuthzGrantDecision.allow: "Allow", AuthzGrantDecision.deny: "Deny"}[self]

    @property
    def description(self) -> str:
        return {
            AuthzGrantDecision.allow: "Permits the action when the grant matches.",
            AuthzGrantDecision.deny: "Refuses the action when the grant matches.",
        }[self]

    @staticmethod
    def __get_pydantic_json_schema__(core_schema: CoreSchema, handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        json_schema = _EnumSchemaMetadata(
            (
                ("allow", "Allow", "Permits the action when the grant matches."),
                ("deny", "Deny", "Refuses the action when the grant matches."),
            )
        ).__get_pydantic_json_schema__(core_schema, handler)
        json_schema.update(
            {
                "description": "Effect applied when a grant matches. A matching deny takes precedence over matching allows."
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
            description="Identity or identity pattern that can receive a grant. It accepts every AuthzGrantSubject form. A grant to one user can also name that user by an identity-provider alias, `user:` followed by the alias; a wildcard pattern cannot use an alias."
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


class AuthzPermissionAction(str, Enum):
    """One action that a policy can grant, named in full. A pattern such as `library:*` belongs in a grant, not here."""

    agent_continue_task = "agent:continue_task"
    agent_create = "agent:create"
    agent_create_task = "agent:create_task"
    agent_create_token = "agent:create_token"
    agent_delete = "agent:delete"
    agent_deregister_process = "agent:deregister_process"
    agent_heartbeat = "agent:heartbeat"
    agent_manage_tools = "agent:manage_tools"
    agent_mint_token = "agent:mint_token"
    agent_read = "agent:read"
    agent_read_task = "agent:read_task"
    agent_read_token = "agent:read_token"
    agent_register_tools = "agent:register_tools"
    agent_revoke_token = "agent:revoke_token"
    agent_stop_task = "agent:stop_task"
    agent_update = "agent:update"
    analytics_read_metrics = "analytics:read_metrics"
    audit_export_events = "audit:export_events"
    audit_read_events = "audit:read_events"
    audit_read_proof = "audit:read_proof"
    billing_read = "billing:read"
    billing_write = "billing:write"
    config_create_config = "config:create_config"
    config_delete_config = "config:delete_config"
    config_read_config = "config:read_config"
    config_update_config = "config:update_config"
    library_admin = "library:admin"
    library_delete = "library:delete"
    library_read = "library:read"
    library_upload = "library:upload"
    library_write = "library:write"
    profile_delete_group = "profile:delete_group"
    profile_delete_policy = "profile:delete_policy"
    profile_delete_tenant = "profile:delete_tenant"
    profile_manage_binding = "profile:manage_binding"
    profile_manage_group = "profile:manage_group"
    profile_manage_member = "profile:manage_member"
    profile_manage_policy = "profile:manage_policy"
    profile_manage_tenant = "profile:manage_tenant"
    profile_manage_tier = "profile:manage_tier"
    profile_read_agent = "profile:read_agent"
    profile_read_group = "profile:read_group"
    profile_read_member = "profile:read_member"
    profile_read_policy = "profile:read_policy"
    tools_read_catalog = "tools:read_catalog"

    @property
    def display_title(self) -> str:
        return {
            AuthzPermissionAction.agent_continue_task: "Continue a Task",
            AuthzPermissionAction.agent_create: "Create an Agent Identity",
            AuthzPermissionAction.agent_create_task: "Create a Task",
            AuthzPermissionAction.agent_create_token: "Create an agent API token",
            AuthzPermissionAction.agent_delete: "Delete an Agent Identity",
            AuthzPermissionAction.agent_deregister_process: "Deregister an SDK process",
            AuthzPermissionAction.agent_heartbeat: "Send an SDK heartbeat",
            AuthzPermissionAction.agent_manage_tools: "Assign Tool Sources to an Agent Identity",
            AuthzPermissionAction.agent_mint_token: "Obtain a platform token",
            AuthzPermissionAction.agent_read: "Read an Agent Identity",
            AuthzPermissionAction.agent_read_task: "Read a Task",
            AuthzPermissionAction.agent_read_token: "List agent API tokens",
            AuthzPermissionAction.agent_register_tools: "Register an SDK Tool catalog",
            AuthzPermissionAction.agent_revoke_token: "Revoke an agent API token",
            AuthzPermissionAction.agent_stop_task: "Stop a Task",
            AuthzPermissionAction.agent_update: "Update an Agent Identity",
            AuthzPermissionAction.analytics_read_metrics: "Read analytics",
            AuthzPermissionAction.audit_export_events: "Export the audit log",
            AuthzPermissionAction.audit_read_events: "Read the audit log",
            AuthzPermissionAction.audit_read_proof: "Read an audit record proof",
            AuthzPermissionAction.billing_read: "Read billing",
            AuthzPermissionAction.billing_write: "Change billing",
            AuthzPermissionAction.config_create_config: "Create a configuration",
            AuthzPermissionAction.config_delete_config: "Delete a configuration",
            AuthzPermissionAction.config_read_config: "Read configurations",
            AuthzPermissionAction.config_update_config: "Update a configuration",
            AuthzPermissionAction.library_admin: "Manage Library grants",
            AuthzPermissionAction.library_delete: "Delete Library content",
            AuthzPermissionAction.library_read: "Read a Library",
            AuthzPermissionAction.library_upload: "Stage a document upload",
            AuthzPermissionAction.library_write: "Write to a Library",
            AuthzPermissionAction.profile_delete_group: "Delete a group",
            AuthzPermissionAction.profile_delete_policy: "Delete a policy",
            AuthzPermissionAction.profile_delete_tenant: "Delete the tenant",
            AuthzPermissionAction.profile_manage_binding: "Bind policies to subjects",
            AuthzPermissionAction.profile_manage_group: "Manage groups",
            AuthzPermissionAction.profile_manage_member: "Manage members",
            AuthzPermissionAction.profile_manage_policy: "Manage policies",
            AuthzPermissionAction.profile_manage_tenant: "Manage the tenant",
            AuthzPermissionAction.profile_manage_tier: "Manage supplied tiers",
            AuthzPermissionAction.profile_read_agent: "Read the agent directory",
            AuthzPermissionAction.profile_read_group: "Read groups",
            AuthzPermissionAction.profile_read_member: "Read members",
            AuthzPermissionAction.profile_read_policy: "Read policies",
            AuthzPermissionAction.tools_read_catalog: "Read the Tool catalog",
        }[self]

    @property
    def description(self) -> str:
        return {
            AuthzPermissionAction.agent_continue_task: "Posts Tool results into a running Task, or starts a continuation Task that inherits its parent Task's history and runs under the parent's Agent Identity. Posting Tool results answers with the running Task's event stream; starting a continuation answers with the new Task's event stream. Each stream needs `agent:read_task` on its Task, and starting a continuation also needs `agent:read_task` on its parent.",
            AuthzPermissionAction.agent_create: "Creates one Agent Identity in the tenant.",
            AuthzPermissionAction.agent_create_task: "Starts a task on an Agent Identity. The request answers with the new task's event stream, so it also needs `agent:read_task` on that task.",
            AuthzPermissionAction.agent_create_token: "Creates one API token for an Agent Identity.",
            AuthzPermissionAction.agent_delete: "Deletes an Agent Identity, and recovers a deleted one.",
            AuthzPermissionAction.agent_deregister_process: "Reports that one SDK process is shutting down.",
            AuthzPermissionAction.agent_heartbeat: "Lets an SDK process keep its registered Tools available.",
            AuthzPermissionAction.agent_manage_tools: "Assigns and withdraws the Tool Sources that one Agent Identity can call.",
            AuthzPermissionAction.agent_mint_token: "Exchanges an Agent Identity's API token for a platform token.",
            AuthzPermissionAction.agent_read: "Reads one Agent Identity and the Tools assigned to it.",
            AuthzPermissionAction.agent_read_task: "Reads a task: its event stream, its result, and its Analytics records.",
            AuthzPermissionAction.agent_read_token: "Lists an Agent Identity's API tokens without their values.",
            AuthzPermissionAction.agent_register_tools: "Registers the complete Tool catalog an SDK process offers for an Agent Identity.",
            AuthzPermissionAction.agent_revoke_token: "Revokes one API token of an Agent Identity.",
            AuthzPermissionAction.agent_stop_task: "Requests a Stop of a Task and of the work started under it.",
            AuthzPermissionAction.agent_update: "Changes an Agent Identity's name or description.",
            AuthzPermissionAction.analytics_read_metrics: "Reads the tenant-wide aggregates: cost, provider, Tool and performance metrics. A task's own metrics and history need `agent:read_task` on that task.",
            AuthzPermissionAction.audit_export_events: "Streams the tenant's audit log as a JSONL or CSV attachment.",
            AuthzPermissionAction.audit_read_events: "Queries the tenant's audit log with filters and pagination.",
            AuthzPermissionAction.audit_read_proof: "Verifies one audit record's integrity and returns its proof.",
            AuthzPermissionAction.billing_read: "Reads the tenant's balance, invoices, meters, usage and fiscal identity.",
            AuthzPermissionAction.billing_write: "Changes the tenant's fiscal identity and auto-recharge, opens checkout and billing portal sessions, and runs the company and VAT lookups that fill those forms.",
            AuthzPermissionAction.config_create_config: "Creates one configuration item from data or a preset.",
            AuthzPermissionAction.config_delete_config: "Deletes one configuration item.",
            AuthzPermissionAction.config_read_config: "Reads configuration items and the preset catalog.",
            AuthzPermissionAction.config_update_config: "Changes one configuration item's values and preset ownership.",
            AuthzPermissionAction.library_admin: "Reads, writes and revokes the access grants on a Library's path, which every Library at that path shares.",
            AuthzPermissionAction.library_delete: "Deletes one document or one whole Library.",
            AuthzPermissionAction.library_read: "Reads a Library, its revisions, its documents and their previews.",
            AuthzPermissionAction.library_upload: "Starts a document upload in a tenant and lists the supported upload formats.",
            AuthzPermissionAction.library_write: "Creates a Library and its documents, changes metadata and tags, and restores trashed documents.",
            AuthzPermissionAction.profile_delete_group: "Deletes one tenant group.",
            AuthzPermissionAction.profile_delete_policy: "Deletes one tenant policy.",
            AuthzPermissionAction.profile_delete_tenant: "Deletes the tenant.",
            AuthzPermissionAction.profile_manage_binding: "Binds and unbinds a policy on one subject: a group, a Human Identity, or an Agent Identity. A grant on one subject delegates the bindings of that subject alone, whatever its kind.",
            AuthzPermissionAction.profile_manage_group: "Creates and renames groups, and adds and removes their members.",
            AuthzPermissionAction.profile_manage_member: "Invites people into the tenant, removes members, and recovers a removed membership.",
            AuthzPermissionAction.profile_manage_policy: "Creates policies and replaces their action list.",
            AuthzPermissionAction.profile_manage_tenant: "Changes the tenant's own fields, and recovers a deleted tenant.",
            AuthzPermissionAction.profile_manage_tier: "Adds and removes tenant members from platform-supplied capability tiers.",
            AuthzPermissionAction.profile_read_agent: "Lists the Agent Identities in the tenant directory.",
            AuthzPermissionAction.profile_read_group: "Reads one group and lists groups in the tenant directory.",
            AuthzPermissionAction.profile_read_member: "Reads one member's profile and lists members in the tenant directory.",
            AuthzPermissionAction.profile_read_policy: "Reads one policy and lists the tenant's policies.",
            AuthzPermissionAction.tools_read_catalog: "Reads the tenant's observed Tool Sources and their retained catalogs.",
        }[self]

    @staticmethod
    def __get_pydantic_json_schema__(core_schema: CoreSchema, handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        json_schema = _EnumSchemaMetadata(
            (
                (
                    "agent:continue_task",
                    "Continue a Task",
                    "Posts Tool results into a running Task, or starts a continuation Task that inherits its parent Task's history and runs under the parent's Agent Identity. Posting Tool results answers with the running Task's event stream; starting a continuation answers with the new Task's event stream. Each stream needs `agent:read_task` on its Task, and starting a continuation also needs `agent:read_task` on its parent.",
                ),
                ("agent:create", "Create an Agent Identity", "Creates one Agent Identity in the tenant."),
                (
                    "agent:create_task",
                    "Create a Task",
                    "Starts a task on an Agent Identity. The request answers with the new task's event stream, so it also needs `agent:read_task` on that task.",
                ),
                ("agent:create_token", "Create an agent API token", "Creates one API token for an Agent Identity."),
                ("agent:delete", "Delete an Agent Identity", "Deletes an Agent Identity, and recovers a deleted one."),
                (
                    "agent:deregister_process",
                    "Deregister an SDK process",
                    "Reports that one SDK process is shutting down.",
                ),
                (
                    "agent:heartbeat",
                    "Send an SDK heartbeat",
                    "Lets an SDK process keep its registered Tools available.",
                ),
                (
                    "agent:manage_tools",
                    "Assign Tool Sources to an Agent Identity",
                    "Assigns and withdraws the Tool Sources that one Agent Identity can call.",
                ),
                (
                    "agent:mint_token",
                    "Obtain a platform token",
                    "Exchanges an Agent Identity's API token for a platform token.",
                ),
                ("agent:read", "Read an Agent Identity", "Reads one Agent Identity and the Tools assigned to it."),
                (
                    "agent:read_task",
                    "Read a Task",
                    "Reads a task: its event stream, its result, and its Analytics records.",
                ),
                (
                    "agent:read_token",
                    "List agent API tokens",
                    "Lists an Agent Identity's API tokens without their values.",
                ),
                (
                    "agent:register_tools",
                    "Register an SDK Tool catalog",
                    "Registers the complete Tool catalog an SDK process offers for an Agent Identity.",
                ),
                ("agent:revoke_token", "Revoke an agent API token", "Revokes one API token of an Agent Identity."),
                ("agent:stop_task", "Stop a Task", "Requests a Stop of a Task and of the work started under it."),
                ("agent:update", "Update an Agent Identity", "Changes an Agent Identity's name or description."),
                (
                    "analytics:read_metrics",
                    "Read analytics",
                    "Reads the tenant-wide aggregates: cost, provider, Tool and performance metrics. A task's own metrics and history need `agent:read_task` on that task.",
                ),
                (
                    "audit:export_events",
                    "Export the audit log",
                    "Streams the tenant's audit log as a JSONL or CSV attachment.",
                ),
                (
                    "audit:read_events",
                    "Read the audit log",
                    "Queries the tenant's audit log with filters and pagination.",
                ),
                (
                    "audit:read_proof",
                    "Read an audit record proof",
                    "Verifies one audit record's integrity and returns its proof.",
                ),
                (
                    "billing:read",
                    "Read billing",
                    "Reads the tenant's balance, invoices, meters, usage and fiscal identity.",
                ),
                (
                    "billing:write",
                    "Change billing",
                    "Changes the tenant's fiscal identity and auto-recharge, opens checkout and billing portal sessions, and runs the company and VAT lookups that fill those forms.",
                ),
                (
                    "config:create_config",
                    "Create a configuration",
                    "Creates one configuration item from data or a preset.",
                ),
                ("config:delete_config", "Delete a configuration", "Deletes one configuration item."),
                ("config:read_config", "Read configurations", "Reads configuration items and the preset catalog."),
                (
                    "config:update_config",
                    "Update a configuration",
                    "Changes one configuration item's values and preset ownership.",
                ),
                (
                    "library:admin",
                    "Manage Library grants",
                    "Reads, writes and revokes the access grants on a Library's path, which every Library at that path shares.",
                ),
                ("library:delete", "Delete Library content", "Deletes one document or one whole Library."),
                ("library:read", "Read a Library", "Reads a Library, its revisions, its documents and their previews."),
                (
                    "library:upload",
                    "Stage a document upload",
                    "Starts a document upload in a tenant and lists the supported upload formats.",
                ),
                (
                    "library:write",
                    "Write to a Library",
                    "Creates a Library and its documents, changes metadata and tags, and restores trashed documents.",
                ),
                ("profile:delete_group", "Delete a group", "Deletes one tenant group."),
                ("profile:delete_policy", "Delete a policy", "Deletes one tenant policy."),
                ("profile:delete_tenant", "Delete the tenant", "Deletes the tenant."),
                (
                    "profile:manage_binding",
                    "Bind policies to subjects",
                    "Binds and unbinds a policy on one subject: a group, a Human Identity, or an Agent Identity. A grant on one subject delegates the bindings of that subject alone, whatever its kind.",
                ),
                (
                    "profile:manage_group",
                    "Manage groups",
                    "Creates and renames groups, and adds and removes their members.",
                ),
                (
                    "profile:manage_member",
                    "Manage members",
                    "Invites people into the tenant, removes members, and recovers a removed membership.",
                ),
                ("profile:manage_policy", "Manage policies", "Creates policies and replaces their action list."),
                (
                    "profile:manage_tenant",
                    "Manage the tenant",
                    "Changes the tenant's own fields, and recovers a deleted tenant.",
                ),
                (
                    "profile:manage_tier",
                    "Manage supplied tiers",
                    "Adds and removes tenant members from platform-supplied capability tiers.",
                ),
                (
                    "profile:read_agent",
                    "Read the agent directory",
                    "Lists the Agent Identities in the tenant directory.",
                ),
                ("profile:read_group", "Read groups", "Reads one group and lists groups in the tenant directory."),
                (
                    "profile:read_member",
                    "Read members",
                    "Reads one member's profile and lists members in the tenant directory.",
                ),
                ("profile:read_policy", "Read policies", "Reads one policy and lists the tenant's policies."),
                (
                    "tools:read_catalog",
                    "Read the Tool catalog",
                    "Reads the tenant's observed Tool Sources and their retained catalogs.",
                ),
            )
        ).__get_pydantic_json_schema__(core_schema, handler)
        json_schema.update(
            {
                "description": "One action that a policy can grant, named in full. A pattern such as `library:*` belongs in a grant, not here."
            }
        )
        return json_schema


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
        Annotated[StrictStr, Field(min_length=1, max_length=300, pattern="^[^{}]*$")],
        Field(
            description="Identifier of the resource covered by a grant. Treat the value as opaque. It cannot contain { or }."
        ),
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
        identity_module.CanonicalActor, Field(description="Canonical user or Agent Identity that created the grant.")
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
