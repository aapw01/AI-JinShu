"""Event payloads for consistency_check agent (#1)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ReviseAttemptPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v1"] = "v1"
    attempt: int = Field(ge=1)
    blocker_categories: list[str] = Field(default_factory=list)
    blocker_count: int = Field(ge=0)
    outline_diff_chars: int = 0
    fallback_model_used: bool = False


class SaveBlockedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v1"] = "v1"
    final_blockers: list[str] = Field(default_factory=list)
    revise_attempts_total: int = Field(ge=0)
    downgrade_reason: Literal["max_revise_exceeded", "yaml_downgrade", "manual"]


class DowngradePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v1"] = "v1"
    from_mode: Literal["strict"]
    to_mode: Literal["warn", "off"]
    category: str
