"""Spacetime contracts (#4)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class SpacetimeAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v1"] = "v1"
    chapter_num: int
    when: str | None = None
    where: str | None = None
    who: list[str] = []
    duration_minutes: int | None = None
    relative_to_prev: Literal["before", "after", "concurrent", "unknown"] = "unknown"
