"""Fact contracts (#9 fact arbitration / #11 self-heal)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FactRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v1"] = "v1"
    novel_id: int
    novel_version_id: int | None = None
    entity_id: int
    fact_type: str
    value_json: dict[str, Any] = Field(default_factory=dict)
    chapter_from: int
    chapter_to: int | None = None
    source_chapter: int
    source_run_id: str | None = None
    source_kind: Literal["writer", "reviewer", "extractor", "manual", "legacy"] = "extractor"
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    extractor_model: str | None = None
    verified_chapter: int | None = None


class FactArbitrationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v1"] = "v1"
    decision: Literal["keep", "supersede", "warn", "reject"]
    superseded_id: int | None = None
    new_id: int | None = None
    reason: str
