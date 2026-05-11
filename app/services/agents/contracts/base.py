"""Base agent input/output contracts (Phase 0 §4.3)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentInput(BaseModel):
    """Common input header. Subclasses add task-specific fields."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v1"] = "v1"
    trace_id: str | None = None
    novel_id: int
    novel_version_id: int | None = None
    chapter_num: int | None = None


class AgentOutput(BaseModel):
    """Common output header. Use ``issues`` for warn/fail metadata."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v1"] = "v1"
    verdict: Literal["pass", "warn", "fail", "skipped"] = "pass"
    issues: list[dict[str, Any]] = Field(default_factory=list)
