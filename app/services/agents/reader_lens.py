"""Reader lens evaluator (#12)."""

from __future__ import annotations

import logging

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.feature_flags import is_enabled
from app.models.novel import ReaderLensReportRow
from app.services.agents.contracts.reader_lens import ReaderLensVerdict
from app.services.agents.llm_agent import run_llm_agent

logger = logging.getLogger(__name__)


class _LLMReaderOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    first_read_fluency: float = Field(ge=0.0, le=1.0)
    info_density: float = Field(ge=0.0, le=1.0)
    missing_setups: list[str] = Field(default_factory=list)


def evaluate_chapter(
    db: Session,
    *,
    novel_id: int,
    novel_version_id: int | None,
    chapter_num: int,
    chapter_text: str,
    prior_summary: str | None = None,
    model_label: str = "primary",
) -> ReaderLensVerdict | None:
    """flag-controlled：跑 reader-lens 评估并落 ``reader_lens_reports``。"""
    if not is_enabled("quality.reader_lens_audit", novel_id=novel_id):
        return None

    parsed = run_llm_agent(
        agent_name="reader_lens",
        event_type="audit",
        template="reader_lens",
        template_kwargs={
            "chapter_text": chapter_text or "",
            "prior_summary": prior_summary or "",
        },
        schema=_LLMReaderOutput,
        novel_id=novel_id,
        chapter_num=chapter_num,
        novel_version_id=novel_version_id,
    )
    if parsed is None:
        return None

    verdict = ReaderLensVerdict(
        chapter_num=chapter_num,
        first_read_fluency=parsed.first_read_fluency,
        info_density=parsed.info_density,
        missing_setups=list(parsed.missing_setups or []),
        model=model_label,
        sampled_at_chapter=chapter_num,
    )

    try:
        from app.core.metrics import (
            reader_lens_audit_total,
            reader_lens_first_read_fluency,
            reader_lens_info_density,
        )

        reader_lens_audit_total.inc()
        reader_lens_first_read_fluency.observe(verdict.first_read_fluency)
        reader_lens_info_density.observe(verdict.info_density)
    except Exception:
        pass

    if novel_version_id is not None:
        existing = db.execute(
            select(ReaderLensReportRow)
            .where(ReaderLensReportRow.novel_version_id == novel_version_id)
            .where(ReaderLensReportRow.chapter_num == chapter_num)
        ).scalar_one_or_none()
        row = existing or ReaderLensReportRow(
            novel_version_id=novel_version_id,
            chapter_num=chapter_num,
            sampled_at_chapter=chapter_num,
        )
        row.first_read_fluency = float(verdict.first_read_fluency)
        row.info_density = float(verdict.info_density)
        row.missing_setups = list(verdict.missing_setups or [])
        row.model = model_label
        if existing is None:
            db.add(row)
        try:
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("reader_lens: persist failed ch=%s", chapter_num)
            return None
    return verdict


__all__ = ["evaluate_chapter"]
