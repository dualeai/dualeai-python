"""Auto-generated Pydantic models from the resolved schema compiler graph. Do not edit manually."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictStr

__all__: list[str] = ["Attachment"]


class Attachment(BaseModel):
    """Document attached to a Task: the key that links it to an uploaded document, its filename, and its description for the model."""

    model_config = ConfigDict(extra="forbid", title="Attachment", json_schema_extra=None)
    key: Annotated[
        StrictStr,
        Field(
            description="Correlation key, chosen by the developer or the SDK, that links this Task attachment to its upload receipt."
        ),
    ]
    filename: Annotated[StrictStr, Field(description="Original filename.")]
    description: Annotated[
        Annotated[StrictStr, Field(max_length=500)], Field(description="Model-facing description of the attachment.")
    ]
