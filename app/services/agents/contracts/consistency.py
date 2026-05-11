"""Consistency contracts (#1 hard gate)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class BlockerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str
    message: str
    chapter_ref: int | None = None
    first_seen_attempt: int = 0


class ConsistencyReportV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v2"] = "v2"
    chapter_num: int
    blockers: list[BlockerEntry] = Field(default_factory=list)
    warnings: list[BlockerEntry] = Field(default_factory=list)
    outline_revise_attempts: int = 0
    final_decision: Literal["passed", "downgraded", "save_blocked"] = "passed"
