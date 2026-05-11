"""Event payloads for fact_extractor agent (#11)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class FactFailurePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v1"] = "v1"
    failure_kind: Literal["llm_error", "parse_error", "schema_violation", "timeout"]
    extractor_model: str | None = None
    retry_count: int = 0
    error_message: str = ""


class FactRetryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v1"] = "v1"
    attempt: int
    fallback_model: str
    outcome: Literal["recovered", "failed", "escalated"]
