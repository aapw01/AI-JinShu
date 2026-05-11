"""Patch contracts (#8 precision rewrite)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EditSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v1"] = "v1"
    span_start: int
    span_end: int
    anchor_before: str
    anchor_after: str
    original_text: str


class PatchInstruction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v1"] = "v1"
    span: EditSpan
    instruction: str
    must_keep_characters: list[str] = Field(default_factory=list)
    forbid_new_characters: bool = True


class PatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v1"] = "v1"
    patched_text: str
    length_delta: int
    introduces_new_characters: list[str] = Field(default_factory=list)
