"""Contracts and validators for chapter-body LLM outputs."""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from app.services.generation.common import detect_chapter_content_contamination


class ChapterBodySchema(BaseModel):
    """Strict payload contract for chapter prose generation."""

    model_config = ConfigDict(extra="forbid")
    chapter_body: str = Field(min_length=1)


@dataclass
class OutputContractError(RuntimeError):
    """Typed exception for output-contract failures."""

    code: str
    stage: str
    chapter_num: int | None = None
    provider: str | None = None
    model: str | None = None
    detail: str | None = None
    retryable: bool = True

    def __post_init__(self) -> None:
        """执行 post init   相关辅助逻辑。"""
        parts = [f"code={self.code}", f"stage={self.stage}"]
        if self.chapter_num is not None:
            parts.append(f"chapter={self.chapter_num}")
        if self.provider:
            parts.append(f"provider={self.provider}")
        if self.model:
            parts.append(f"model={self.model}")
        if self.detail:
            parts.append(f"detail={self.detail}")
        RuntimeError.__init__(self, " ".join(parts))


def validate_chapter_body(
    text: str | None,
    *,
    stage: str,
    chapter_num: int | None,
    provider: str | None,
    model: str | None,
    min_chars: int = 120,
) -> str:
    """Validate chapter body semantics without mutating content."""
    body = str(text or "").strip()
    if not body:
        raise OutputContractError(
            code="MODEL_OUTPUT_SCHEMA_INVALID",
            stage=stage,
            chapter_num=chapter_num,
            provider=provider,
            model=model,
            detail="chapter_body is empty",
            retryable=True,
        )

    contamination = detect_chapter_content_contamination(body, chapter_num=chapter_num)
    if contamination.get("contaminated"):
        raise OutputContractError(
            code="MODEL_OUTPUT_POLICY_VIOLATION",
            stage=stage,
            chapter_num=chapter_num,
            provider=provider,
            model=model,
            detail=f"contamination={contamination.get('reasons')}",
            retryable=False,
        )

    if len(body) < max(0, int(min_chars)):
        raise OutputContractError(
            code="MODEL_OUTPUT_POLICY_VIOLATION",
            stage=stage,
            chapter_num=chapter_num,
            provider=provider,
            model=model,
            detail=f"chapter_body_too_short len={len(body)} min_chars={int(min_chars)}",
            retryable=False,
        )
    return body
