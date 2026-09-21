"""Auto-generated Pydantic models from the resolved schema compiler graph. Do not edit manually."""

from __future__ import annotations

from typing import Annotated, Union

from pydantic import Field, StrictBool, StrictFloat, StrictInt, StrictStr
from typing_extensions import TypeAliasType

__all__: list[str] = ["JsonValue"]
JsonValue = TypeAliasType(
    "JsonValue",
    Annotated[
        Union[
            Union[
                StrictStr,
                Annotated[StrictInt, Field(ge=-9007199254740991, le=9007199254740991)],
                Annotated[StrictFloat, Field(ge=-9007199254740991, le=9007199254740991, allow_inf_nan=False)],
                StrictBool,
                Annotated[list["JsonValue"], Field(strict=True)],
                Annotated[dict[StrictStr, "JsonValue"], Field(strict=True)],
            ],
            None,
        ],
        Field(title="JsonValue", description="A recursive JSON value used for intentionally open payload fields"),
    ],
)
