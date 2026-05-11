"""Voice contracts (#5 voice drift)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class VoiceFingerprint(BaseModel):
    """字段命名说明：``voice_register`` 是为了避开 Pydantic BaseModel 的
    ``register`` 类方法名，YAML/JSON 序列化里仍用业务习惯的 ``voice_register``。
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v1"] = "v1"
    character_key: str
    avg_sentence_len: float
    formality_score: float = Field(ge=0.0, le=1.0)
    voice_register: Literal["high", "neutral", "low", "mixed"] = "neutral"
    sample_chapter_range: tuple[int, int] | None = None


class VoiceDriftReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v1"] = "v1"
    character_key: str
    drift_score: float
    threshold: float
    triggered: bool
    diff_dimensions: list[str] = Field(default_factory=list)
