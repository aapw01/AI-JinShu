"""Spacetime extractor (#4)."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.feature_flags import is_enabled
from app.models.novel import SpacetimeAnchorRow
from app.services.agents.contracts.spacetime import SpacetimeAnchor
from app.services.agents.llm_agent import run_llm_agent

logger = logging.getLogger(__name__)


class _LLMSpacetimeOutput(SpacetimeAnchor):
    """LLM-side schema = SpacetimeAnchor 直接复用。chapter_num 由调用方覆写。"""


def extract_and_persist(
    db: Session,
    *,
    novel_id: int,
    novel_version_id: int | None,
    chapter_num: int,
    chapter_text: str,
    prev_anchor: str | None = None,
) -> SpacetimeAnchor | None:
    """flag-controlled：抽取 + 落 ``spacetime_anchors``。返回 None 表示降级。"""
    if not is_enabled("consistency.spacetime_v1", novel_id=novel_id):
        return None

    parsed = run_llm_agent(
        agent_name="spacetime_extractor",
        event_type="extract",
        template="spacetime_extract",
        template_kwargs={
            "chapter_text": chapter_text or "",
            "prev_anchor": prev_anchor or "",
        },
        schema=_LLMSpacetimeOutput,
        novel_id=novel_id,
        chapter_num=chapter_num,
        novel_version_id=novel_version_id,
    )
    if parsed is None:
        return None

    # chapter_num 在 prompt 里没指定，强制用调用方传入
    parsed_dict = parsed.model_dump()
    parsed_dict["chapter_num"] = chapter_num
    final = SpacetimeAnchor.model_validate(parsed_dict)

    try:
        from app.core.metrics import spacetime_extract_success_rate

        spacetime_extract_success_rate.set(1.0)
    except Exception:
        pass

    if novel_version_id is not None:
        # upsert：UNIQUE(novel_version_id, chapter_num)
        existing = db.execute(
            select(SpacetimeAnchorRow)
            .where(SpacetimeAnchorRow.novel_version_id == novel_version_id)
            .where(SpacetimeAnchorRow.chapter_num == chapter_num)
        ).scalar_one_or_none()
        row = existing or SpacetimeAnchorRow(
            novel_version_id=novel_version_id,
            chapter_num=chapter_num,
        )
        row.when_text = final.when
        row.where_text = final.where
        row.who_keys = list(final.who or [])
        row.duration_minutes = final.duration_minutes
        row.relative_to_prev = final.relative_to_prev
        row.payload = final.model_dump()
        if existing is None:
            db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            logger.warning("spacetime_extractor: integrity error for ch=%s", chapter_num)
            return None
    return final


__all__ = ["extract_and_persist"]
