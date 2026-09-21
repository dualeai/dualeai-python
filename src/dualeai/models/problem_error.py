"""Auto-generated Pydantic models from the resolved schema compiler graph. Do not edit manually."""

from __future__ import annotations

from typing import Annotated, Union

from pydantic import BaseModel, ConfigDict, Field, StrictStr

__all__: list[str] = ["ProblemError"]


class ProblemError(BaseModel):
    """Individual error item with AI guidance. Used inside ProblemDetails.errors[] for per-error context. Cloudflare RFC 9457 extension fields (retryable, retry_after_seconds, owner_action_required, error_category) live on the ProblemDetails envelope per RFC 9457 §3.2, not here."""

    model_config = ConfigDict(extra="forbid", title="ProblemError", json_schema_extra=None)
    message: Annotated[Annotated[StrictStr, Field(max_length=1000)], Field(description="Human-readable error message")]
    details: Annotated[
        Annotated[StrictStr, Field(max_length=4000)], Field(description="Technical explanation of the error")
    ]
    field: Annotated[
        Union[StrictStr, None],
        Field(description="Field path if validation error (e.g. 'body.email')", json_schema_extra={"default": None}),
    ] = None
    ai_hints: Annotated[
        Union[Annotated[StrictStr, Field(max_length=2000)], None],
        Field(description="Guidance for AI agents on resolution", json_schema_extra={"default": None}),
    ] = None
