"""Foreshadow contracts (#6 lifecycle)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class ForeshadowLifecycleEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v1"] = "v1"
    foreshadow_id: str
    state: Literal["planned", "planted", "paid", "stale"]
    plant_chapter: int | None = None
    payoff_chapter: int | None = None
    confidence: float = 0.5


class PlantPayoffMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v1"] = "v1"
    foreshadow_id: str
    matched: bool
    confidence: float = 0.0
    method: Literal["embedding", "llm_semantic", "substring", "manual"] = "substring"
    evidence_span: tuple[int, int] | None = None
