"""Fact extractor self-heal runner (#11 §11.4).

实现 ``app.tasks.extractor_recovery._runner_impl``。给定一行
``fact_extraction_failures``：

1. 读出 ``failure_kind`` / ``error_payload``：从 ``error_payload['chapter_text']``
   取章节正文（如果失败时持久化了，参见 §11.4 prompt-tuning PR；本基线提供
   ``chapter_text`` 缺失时 → False 让其继续累计 retry）。
2. 调 ``FactExtractorAgent.run`` 使用 fallback 配置（见 ``presets/strategies/
   <key>.yaml`` Fallback Chain v2，#4.8）。
3. 如果抽取成功：把 ``facts`` 落到 ``story_facts``，``confidence`` 默认 0.5，
   ``source_kind=extractor``，``extractor_model`` 写当前 fallback 模型名。
4. 返回 True 让 recovery loop 标记为 ``recovered``。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.novel import FactExtractionFailure, StoryEntity, StoryFact
from app.tasks import extractor_recovery

logger = logging.getLogger(__name__)


def _persist_facts(
    db: Session,
    *,
    novel_id: int,
    novel_version_id: int | None,
    chapter_num: int,
    extractor_model: str,
    extracted: dict[str, Any],
) -> int:
    facts = extracted.get("facts") or []
    if not isinstance(facts, list):
        return 0
    written = 0
    for fact in facts[:40]:
        if not isinstance(fact, dict):
            continue
        entity_name = str(fact.get("entity_name") or fact.get("entity") or "").strip()
        fact_type = str(fact.get("fact_type") or fact.get("type") or "").strip()
        if not entity_name or not fact_type:
            continue
        # find or create entity by name within novel
        entity = (
            db.execute(
                select(StoryEntity)
                .where(StoryEntity.novel_id == novel_id)
                .where(StoryEntity.name == entity_name)
            )
            .scalars()
            .first()
        )
        if entity is None:
            entity = StoryEntity(
                novel_id=novel_id,
                novel_version_id=novel_version_id,
                entity_type=str(fact.get("entity_type") or "character"),
                name=entity_name,
            )
            db.add(entity)
            db.flush()
        row = StoryFact(
            novel_id=novel_id,
            novel_version_id=novel_version_id,
            entity_id=entity.id,
            fact_type=fact_type,
            value_json={"v": fact.get("value") or fact.get("description") or ""},
            chapter_from=int(fact.get("chapter_from") or chapter_num),
            chapter_to=fact.get("chapter_to"),
            source_chapter=chapter_num,
            source_kind="extractor",
            confidence=float(fact.get("confidence") or 0.5),
            extractor_model=extractor_model,
            is_active=1,
        )
        db.add(row)
        written += 1
    return written


def _self_heal_runner(row: FactExtractionFailure) -> bool:
    """Real recovery runner. ``error_payload['chapter_text']`` is required;
    otherwise we cannot retry meaningfully."""
    payload = row.error_payload or {}
    chapter_text = str(payload.get("chapter_text") or "").strip()
    if not chapter_text:
        return False

    try:
        from app.services.generation.agents import FactExtractorAgent
    except Exception:
        logger.exception("self_heal: FactExtractorAgent import failed")
        return False

    fallback_model = str(payload.get("fallback_model") or "")
    fallback_provider = str(payload.get("fallback_provider") or "")
    try:
        agent = FactExtractorAgent()
        extracted = agent.run(
            chapter_num=int(row.chapter_num),
            content=chapter_text,
            outline=payload.get("outline") or {},
            language=str(payload.get("language") or "zh"),
            provider=fallback_provider or None,
            model=fallback_model or None,
        )
    except Exception:
        logger.exception("self_heal: extractor invocation failed for row=%s", row.id)
        return False

    if not isinstance(extracted, dict):
        return False

    db = SessionLocal()
    try:
        try:
            written = _persist_facts(
                db,
                novel_id=int(row.novel_id),
                novel_version_id=row.novel_version_id,
                chapter_num=int(row.chapter_num),
                extractor_model=fallback_model or "primary",
                extracted=extracted,
            )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("self_heal: persist failed for row=%s", row.id)
            return False
    finally:
        db.close()

    return True


def install() -> None:
    """Wire this runner into the extractor recovery loop."""
    extractor_recovery.register_runner(_self_heal_runner)


__all__ = ["install"]
