"""Reader lens contracts (#12)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ReaderLensVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v1"] = "v1"
    chapter_num: int
    first_read_fluency: float = Field(ge=0.0, le=1.0)
    info_density: float = Field(ge=0.0, le=1.0)
    missing_setups: list[str] = Field(default_factory=list)
    model: str
    sampled_at_chapter: int
