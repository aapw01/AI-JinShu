"""Outline contracts (#7 outline_audit)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OutlineContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v1"] = "v1"
    chapter_num: int
    chapter_objective: str
    required_new_information: list[str] = Field(default_factory=list)
    payoff: str | None = None
    opening_scene: str | None = None
    transition_mode: Literal["direct", "continuous", "jump", "flashback", ""] = ""
    forbidden_repeats: list[str] = Field(default_factory=list)
    relationship_delta: str | None = None


class OutlinePromiseVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    fulfilled: Literal["yes", "partial", "no"]
    evidence_span: tuple[int, int] | None = None
    note: str | None = None


class OutlineAuditReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v1"] = "v1"
    chapter_num: int
    promises: list[OutlinePromiseVerdict] = Field(default_factory=list)
    must_fix_count: int = 0
