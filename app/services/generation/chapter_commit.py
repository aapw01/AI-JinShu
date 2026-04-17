"""Chapter commit service — all post-finalize memory write-back in one place.

Consolidates summary, character state, character profile, bible store,
quality report, and checkpoint writes that were previously scattered
inside _node_finalize / _write_longform_artifacts.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from app.services.generation.common import REVIEW_SCORE_THRESHOLD
from app.services.generation.events import EventBus, GenerationEvent
from app.services.generation.heuristics import extract_item_mentions, extract_timeline_markers
from app.services.generation.progress import volume_no_for_chapter
from app.services.generation.state import GenerationState
from app.services.memory.retriever import KnowledgeRetriever
from app.services.memory.progression_state import ProgressionMemoryManager

logger = logging.getLogger(__name__)


def _build_fact_delta_summary(
    extracted_facts: dict[str, Any],
    chapter_num: int,
    *,
    fallback: str = "",
) -> str:
    """生成一行高信号事实摘要，避免写入低信息量标签。"""
    parts: list[str] = []
    for event in (extracted_facts.get("events") or [])[:2]:
        if not isinstance(event, dict):
            continue
        title = str(event.get("title") or event.get("summary") or "").strip()
        if title:
            parts.append(f"事件:{title}")
    for fact in (extracted_facts.get("facts") or [])[:2]:
        if not isinstance(fact, dict):
            continue
        entity_name = str(fact.get("entity_name") or "").strip()
        fact_type = str(fact.get("fact_type") or "").strip()
        value = str(fact.get("value") or "").strip()
        summary = " / ".join(part for part in [entity_name, fact_type, value] if part)
        if summary:
            parts.append(f"事实:{summary}")
    if not parts and str(fallback or "").strip():
        parts.append(str(fallback).strip())
    if not parts:
        parts.append(f"第{chapter_num}章新增事实")
    return "；".join(parts)[:220]


def _build_continuity_summary(
    *,
    chapter_num: int,
    summary_text: str,
    outline: dict[str, Any],
    progression_payload: dict[str, Any],
) -> str:
    """生成一行高信号连续性摘要，优先保留目标、变化和承接点。"""
    advancement = progression_payload.get("advancement") if isinstance(progression_payload.get("advancement"), dict) else {}
    transition = progression_payload.get("transition") if isinstance(progression_payload.get("transition"), dict) else {}
    parts: list[str] = []
    objective = str(
        advancement.get("chapter_objective")
        or outline.get("chapter_objective")
        or outline.get("purpose")
        or ""
    ).strip()
    if objective:
        parts.append(f"目标:{objective}")
    required_new_information = advancement.get("new_information") or outline.get("required_new_information") or []
    compact_new_information = "、".join(
        str(item).strip()
        for item in required_new_information[:2]
        if str(item).strip()
    )
    if compact_new_information:
        parts.append(f"新信息:{compact_new_information}")
    relationship_delta = str(
        advancement.get("relationship_delta")
        or outline.get("relationship_delta")
        or ""
    ).strip()
    if relationship_delta:
        parts.append(f"关系变化:{relationship_delta}")
    transition_line = str(
        transition.get("last_action")
        or transition.get("ending_scene")
        or outline.get("opening_scene")
        or ""
    ).strip()
    if transition_line:
        parts.append(f"承接点:{transition_line}")
    if not parts and str(summary_text or "").strip():
        parts.append(str(summary_text).strip())
    if not parts:
        parts.append(f"第{chapter_num}章连续性要点")
    return "；".join(parts)[:220]


def _render_fact_delta_text(extracted_facts: dict[str, Any], chapter_num: int) -> str:
    """把 facts/events 压成适合检索的事实增量文本。"""
    parts: list[str] = [f"第{chapter_num}章事实增量"]
    for event in (extracted_facts.get("events") or [])[:5]:
        if not isinstance(event, dict):
            continue
        title = str(event.get("title") or "").strip()
        summary = str(event.get("summary") or event.get("description") or "").strip()
        line = " / ".join(part for part in [title, summary] if part)
        if line:
            parts.append(f"- 事件: {line}")
    for fact in (extracted_facts.get("facts") or [])[:8]:
        if not isinstance(fact, dict):
            continue
        entity_name = str(fact.get("entity_name") or "").strip()
        fact_type = str(fact.get("fact_type") or "").strip()
        value = str(fact.get("value") or "").strip()
        line = " / ".join(part for part in [entity_name, fact_type, value] if part)
        if line:
            parts.append(f"- 事实: {line}")
    return "\n".join(parts)


def _render_continuity_text(
    *,
    chapter_num: int,
    summary_text: str,
    outline: dict[str, Any],
    progression_payload: dict[str, Any],
) -> str:
    """把连续性约束相关信息压成一段可检索文本。"""
    advancement = progression_payload.get("advancement") if isinstance(progression_payload.get("advancement"), dict) else {}
    transition = progression_payload.get("transition") if isinstance(progression_payload.get("transition"), dict) else {}
    required_new_information = advancement.get("new_information") or outline.get("required_new_information") or []
    relationship_delta = advancement.get("relationship_delta") or outline.get("relationship_delta") or ""
    conflict_axis = advancement.get("conflict_axis") or outline.get("conflict_axis") or outline.get("chapter_objective") or ""
    parts = [
        f"第{chapter_num}章连续性摘要",
        f"章节目标: {str(advancement.get('chapter_objective') or outline.get('chapter_objective') or outline.get('purpose') or '').strip()}",
        f"连续性回顾: {summary_text.strip()}",
        f"新信息: {'；'.join(str(item) for item in required_new_information[:4] if str(item).strip())}",
        f"关系变化: {str(relationship_delta).strip()}",
        f"冲突轴: {str(conflict_axis).strip()}",
        f"开场场景: {str(outline.get('opening_scene') or '').strip()}",
        f"开场时间状态: {str(outline.get('opening_time_state') or '').strip()}",
        f"上一章结束场景: {str(transition.get('ending_scene') or '').strip()}",
        f"上一章最后动作: {str(transition.get('last_action') or '').strip()}",
    ]
    return "\n".join(line for line in parts if line.split(":", 1)[-1].strip())


def _chapter_finalized_story_bible_handler(event: GenerationEvent) -> None:
    """执行 chapter finalized story bible handler 相关辅助逻辑。"""
    payload = event.payload
    state: GenerationState = payload["state"]
    bible = payload["bible"]
    db = payload.get("db")
    chapter_num = int(payload["chapter_num"])
    summary_text = str(payload.get("summary_text") or "")
    final_content = str(payload.get("final_content") or "")
    outline = payload.get("outline") or {}

    if chapter_num == state.get("start_chapter", 1):
        chars = ((state.get("prewrite") or {}).get("specification") or {}).get("characters") or []
        if isinstance(chars, list):
            for c in chars[:80]:
                if isinstance(c, dict) and c.get("name"):
                    bible.upsert_entity(
                        novel_id=state["novel_id"],
                        novel_version_id=state.get("novel_version_id"),
                        entity_type="character",
                        name=str(c["name"]),
                        status="alive",
                        summary=str(c.get("description") or c.get("role") or "")[:300],
                        metadata={"source": "specification"},
                        db=db,
                    )

    event_id = f"EV-{chapter_num:04d}"
    bible.add_event(
        novel_id=state["novel_id"],
        novel_version_id=state.get("novel_version_id"),
        event_id=event_id,
        chapter_num=chapter_num,
        title=outline.get("title") or f"第{chapter_num}章",
        event_type="chapter",
        payload={
            "summary": summary_text[:600],
            "review_score": state.get("score"),
            "language_score": payload.get("language_score"),
        },
        db=db,
    )

    foreshadowing = outline.get("foreshadowing")
    if foreshadowing:
        items = foreshadowing if isinstance(foreshadowing, list) else [foreshadowing]
        for idx, item in enumerate(items[:8], start=1):
            text = str(item)
            matched = re.findall(r"[A-Z]-\d+", text)
            fs_id = matched[0] if matched else f"FS-{chapter_num:04d}-{idx}"
            bible.upsert_foreshadow(
                novel_id=state["novel_id"],
                novel_version_id=state.get("novel_version_id"),
                foreshadow_id=fs_id,
                title=text[:200],
                planted_chapter=chapter_num,
                state="planted",
                payload={"outline": outline.get("title")},
                db=db,
            )

    payoff = str(outline.get("payoff") or "").strip()
    if payoff:
        matched = re.findall(r"[A-Z]-\d+", payoff)
        for fs_id in matched[:5]:
            bible.upsert_foreshadow(
                novel_id=state["novel_id"],
                novel_version_id=state.get("novel_version_id"),
                foreshadow_id=fs_id,
                title=payoff[:200],
                planted_chapter=max(1, chapter_num - 1),
                state="resolved",
                resolved_chapter=chapter_num,
                payload={"payoff": payoff[:500]},
                db=db,
            )

    extracted = payload.get("extracted_facts") or {}
    extracted_entities: dict[str, Any] = {}
    for ev in (extracted.get("events") or [])[:20]:
        if not isinstance(ev, dict):
            continue
        eid = str(ev.get("id") or f"EVX-{chapter_num:04d}-{abs(hash(str(ev))) % 100000:05d}")[:64]
        bible.add_event(
            novel_id=state["novel_id"],
            novel_version_id=state.get("novel_version_id"),
            event_id=eid,
            chapter_num=chapter_num,
            title=str(ev.get("title") or "")[:255] or f"第{chapter_num}章事件",
            event_type=str(ev.get("type") or "extracted")[:100],
            actors=[str(x) for x in (ev.get("actors") or [])][:10],
            payload={
                "summary": str(ev.get("summary") or "")[:500],
                "time_marker": str(ev.get("time_marker") or "")[:80],
            },
            db=db,
        )

    for ent in (extracted.get("entities") or [])[:30]:
        if not isinstance(ent, dict) or not ent.get("name"):
            continue
        entity = bible.upsert_entity(
            novel_id=state["novel_id"],
            novel_version_id=state.get("novel_version_id"),
            entity_type=str(ent.get("entity_type") or "unknown")[:50],
            name=str(ent.get("name"))[:255],
            status=str(ent.get("status") or "active")[:50],
            summary=str(ent.get("summary") or "")[:300],
            metadata={"source": "fact_extractor"},
            db=db,
        )
        extracted_entities[entity.name] = entity

    for fact in (extracted.get("facts") or [])[:40]:
        if not isinstance(fact, dict):
            continue
        entity_name = str(fact.get("entity_name") or "").strip()
        if not entity_name:
            continue
        entity = extracted_entities.get(entity_name)
        if entity is None:
            entity = bible.upsert_entity(
                novel_id=state["novel_id"],
                novel_version_id=state.get("novel_version_id"),
                entity_type="unknown",
                name=entity_name[:255],
                status="active",
                summary="",
                metadata={"source": "fact_extractor_fact_only"},
                db=db,
            )
        bible.add_fact(
            novel_id=state["novel_id"],
            novel_version_id=state.get("novel_version_id"),
            entity_id=entity.id,
            fact_type=str(fact.get("fact_type") or "attribute")[:100],
            value_json={"value": fact.get("value"), "chapter_num": chapter_num},
            chapter_from=chapter_num,
            db=db,
        )

    for marker in extract_timeline_markers(final_content):
        bible.add_event(
            novel_id=state["novel_id"],
            novel_version_id=state.get("novel_version_id"),
            event_id=f"TL-{chapter_num:04d}-{abs(hash(marker)) % 10000:04d}",
            chapter_num=chapter_num,
            title=f"时间标记:{marker}",
            event_type="timeline_marker",
            payload={"marker": marker},
            db=db,
        )

    for item_name in extract_item_mentions(final_content):
        item_entity = bible.upsert_entity(
            novel_id=state["novel_id"],
            novel_version_id=state.get("novel_version_id"),
            entity_type="item",
            name=item_name,
            status="active",
            summary=f"在第{chapter_num}章出现",
            metadata={"source": "chapter_content"},
            db=db,
        )
        bible.add_fact(
            novel_id=state["novel_id"],
            novel_version_id=state.get("novel_version_id"),
            entity_id=item_entity.id,
            fact_type="mentioned_in_chapter",
            value_json={"chapter_num": chapter_num, "context": final_content[:300]},
            chapter_from=chapter_num,
            db=db,
        )

    chars = bible.list_entities(
        state["novel_id"],
        novel_version_id=state.get("novel_version_id"),
        entity_type="character",
        db=db,
    )
    appeared = [c for c in chars if c.name and c.name in final_content]
    for i in range(len(appeared)):
        for j in range(i + 1, len(appeared)):
            a = appeared[i]
            b = appeared[j]
            bible.add_event(
                novel_id=state["novel_id"],
                novel_version_id=state.get("novel_version_id"),
                event_id=f"REL-{chapter_num:04d}-{a.id}-{b.id}",
                chapter_num=chapter_num,
                title=f"{a.name}/{b.name} 关系信号",
                event_type="relationship_signal",
                actors=[a.name, b.name],
                payload={"signal": "co_appearance"},
                db=db,
            )


def _chapter_finalized_progression_handler(event: GenerationEvent) -> None:
    """执行 chapter finalized progression handler 相关辅助逻辑。"""
    payload = event.payload
    state: GenerationState = payload["state"]
    progression = payload["progression"]
    db = payload.get("db")
    chapter_num = int(payload["chapter_num"])
    volume_no = int(payload["volume_no"])
    promotion_score = float(payload.get("promotion_score") or 0.0)
    progression_payload = payload.get("progression_payload") or {}
    advancement = progression_payload.get("advancement") if isinstance(progression_payload.get("advancement"), dict) else {}
    transition = progression_payload.get("transition") if isinstance(progression_payload.get("transition"), dict) else {}
    if advancement:
        progression.save_chapter_advancement(
            state["novel_id"],
            chapter_num,
            advancement,
            novel_version_id=state.get("novel_version_id"),
            volume_no=volume_no,
            promotion_score=promotion_score,
            db=db,
        )
        payload["volume_arc_state"] = progression.merge_volume_arc_state(
            state["novel_id"],
            volume_no,
            chapter_num,
            advancement,
            novel_version_id=state.get("novel_version_id"),
            promotion_score=promotion_score,
            db=db,
        )
        payload["book_progression_state"] = progression.merge_book_progression_state(
            state["novel_id"],
            chapter_num,
            advancement,
            novel_version_id=state.get("novel_version_id"),
            promotion_score=promotion_score,
            db=db,
        )
    else:
        payload["volume_arc_state"] = progression.get_volume_arc_state(
            state["novel_id"],
            volume_no,
            novel_version_id=state.get("novel_version_id"),
            db=db,
        ) or {}
        payload["book_progression_state"] = progression.get_book_progression_state(
            state["novel_id"],
            novel_version_id=state.get("novel_version_id"),
            db=db,
        ) or {}
    if transition:
        progression.save_chapter_transition(
            state["novel_id"],
            chapter_num,
            transition,
            novel_version_id=state.get("novel_version_id"),
            volume_no=volume_no,
            promotion_score=promotion_score,
            db=db,
        )


def _chapter_finalized_quality_report_handler(event: GenerationEvent) -> None:
    """执行 chapter finalized quality report handler 相关辅助逻辑。"""
    payload = event.payload
    state: GenerationState = payload["state"]
    quality = payload["quality"]
    db = payload.get("db")
    chapter_num = int(payload["chapter_num"])
    volume_no = int(payload["volume_no"])
    advancement = payload.get("progression_payload", {}).get("advancement") or {}
    transition = payload.get("progression_payload", {}).get("transition") or {}
    factual_score = float(state.get("factual_score", 0.0) or 0.0)
    progression_score = float(state.get("progression_score", 0.0) or 0.0)
    reviewer_aesthetic = float(state.get("aesthetic_review_score", 0.0) or 0.0)
    verdict = "pass" if (
        state.get("score", 0.0) >= REVIEW_SCORE_THRESHOLD
        and factual_score >= 0.65
        and progression_score >= 0.62
        and float(payload.get("language_score") or 0.0) >= 0.6
        and reviewer_aesthetic >= 0.6
    ) else "warning"
    quality.add_report(
        novel_id=state["novel_id"],
        novel_version_id=state.get("novel_version_id"),
        scope="chapter",
        scope_id=str(chapter_num),
        metrics_json={
            "review_score": state.get("score", 0.0),
            "factual_score": factual_score,
            "progression_score": progression_score,
            "language_score": payload.get("language_score"),
            "aesthetic_review_score": reviewer_aesthetic,
            "aesthetic_score": payload.get("aesthetic_score_val"),
            "revision_count": payload.get("revision_count"),
            "volume_no": volume_no,
            "duplication_risk": round(max(0.0, 1.0 - progression_score), 4),
            "no_new_delta": bool(advancement and not str(advancement.get("irreversible_change") or "").strip()),
            "transition_conflict_risk": round(
                1.0 if transition and not str(transition.get("scene_exit") or "").strip() and str(transition.get("ending_scene") or "").strip() else 0.0,
                4,
            ),
            "volume_repeat_risk": round(
                min(1.0, max(0.0, len((payload.get("volume_arc_state") or {}).get("forbidden_repeats") or []) / 10.0)),
                4,
            ),
            "consistency_scorecard": state.get("consistency_scorecard") or {},
            "review_gate": state.get("review_gate") or {},
            "context_sources": list((state.get("context") or {}).get("context_sources") or []),
        },
        verdict=verdict,
        db=db,
    )


def _chapter_finalized_checkpoint_handler(event: GenerationEvent) -> None:
    """执行 chapter finalized checkpoint handler 相关辅助逻辑。"""
    payload = event.payload
    state: GenerationState = payload["state"]
    checkpoint = payload["checkpoint"]
    db = payload.get("db")
    chapter_num = int(payload["chapter_num"])
    volume_no = int(payload["volume_no"])
    if state.get("task_id"):
        checkpoint.save_checkpoint(
            task_id=state["task_id"],
            novel_id=state["novel_id"],
            volume_no=volume_no,
            chapter_num=chapter_num,
            node="chapter_done",
            state_json={
                "chapter_num": chapter_num,
                "volume_no": volume_no,
                "review_score": state.get("score", 0.0),
                "language_score": payload.get("language_score"),
                "progression_score": state.get("progression_score", 0.0),
                "consistency_scorecard": state.get("consistency_scorecard") or {},
                "review_gate": state.get("review_gate") or {},
                "summary": str(payload.get("summary_text") or "")[:400],
                "content_preview": str(payload.get("final_content") or "")[:400],
                "chapter_advancement": (payload.get("progression_payload") or {}).get("advancement") or {},
                "chapter_transition": (payload.get("progression_payload") or {}).get("transition") or {},
                "book_progression_state": payload.get("book_progression_state") or {},
            },
            db=db,
        )


def _chapter_finalized_telemetry_handler(event: GenerationEvent) -> None:
    """执行 chapter finalized telemetry handler 相关辅助逻辑。"""
    _ = event  # telemetry placeholder for future extension


def _chapter_finalized_knowledge_chunk_handler(event: GenerationEvent) -> None:
    """章节完成后写入 summary / continuity / fact_delta 三类检索 chunk。"""
    payload = event.payload
    state: GenerationState = payload["state"]
    db = payload.get("db")
    chapter_num = int(payload["chapter_num"])
    summary_text = str(payload.get("summary_text") or "").strip()
    final_content = str(payload.get("final_content") or "").strip()
    outline = payload.get("outline") or {}
    progression_payload = payload.get("progression_payload") or {}
    extracted_facts = payload.get("extracted_facts") or {}
    retriever = KnowledgeRetriever()

    retriever.upsert_chunk(
        novel_id=state["novel_id"],
        novel_version_id=state.get("novel_version_id"),
        source_type="chapter_summary",
        source_key=f"chapter_summary:{chapter_num}",
        chapter_num=chapter_num,
        summary=summary_text[:220],
        content=summary_text or final_content[:1000],
        importance_score=0.62,
        metadata={"chapter_num": chapter_num},
        db=db,
    )
    retriever.upsert_chunk(
        novel_id=state["novel_id"],
        novel_version_id=state.get("novel_version_id"),
        source_type="chapter_continuity",
        source_key=f"chapter_continuity:{chapter_num}",
        chapter_num=chapter_num,
        summary=_build_continuity_summary(
            chapter_num=chapter_num,
            summary_text=summary_text,
            outline=outline,
            progression_payload=progression_payload if isinstance(progression_payload, dict) else {},
        ),
        content=_render_continuity_text(
            chapter_num=chapter_num,
            summary_text=summary_text,
            outline=outline,
            progression_payload=progression_payload if isinstance(progression_payload, dict) else {},
        ),
        importance_score=0.95,
        metadata={"chapter_num": chapter_num, "kind": "continuity"},
        db=db,
    )
    retriever.upsert_chunk(
        novel_id=state["novel_id"],
        novel_version_id=state.get("novel_version_id"),
        source_type="chapter_fact_delta",
        source_key=f"chapter_fact_delta:{chapter_num}",
        chapter_num=chapter_num,
        summary=_build_fact_delta_summary(
            extracted_facts if isinstance(extracted_facts, dict) else {},
            chapter_num,
            fallback=summary_text,
        ),
        content=_render_fact_delta_text(
            extracted_facts if isinstance(extracted_facts, dict) else {},
            chapter_num,
        ) or (summary_text or final_content[:1000]),
        importance_score=0.9,
        metadata={"chapter_num": chapter_num, "kind": "fact_delta"},
        db=db,
    )


def _build_chapter_finalized_event_bus() -> EventBus:
    """构建章节finalized事件bus。"""
    bus = EventBus()
    bus.register("chapter.finalized", _chapter_finalized_story_bible_handler, required=True)
    bus.register("chapter.finalized", _chapter_finalized_progression_handler, required=True)
    bus.register("chapter.finalized", _chapter_finalized_knowledge_chunk_handler, required=True)
    bus.register("chapter.finalized", _chapter_finalized_quality_report_handler, required=True)
    bus.register("chapter.finalized", _chapter_finalized_checkpoint_handler, required=True)
    bus.register("chapter.finalized", _chapter_finalized_telemetry_handler, required=False)
    return bus


def write_longform_artifacts(
    state: GenerationState,
    chapter_num: int,
    summary_text: str,
    final_content: str,
    language_score: float,
    aesthetic_score_val: float,
    revision_count: int,
    extracted_facts: dict[str, Any] | None = None,
    progression_memory: dict[str, Any] | None = None,
    progression_promotion: dict[str, Any] | None = None,
    db: Session | None = None,
) -> None:
    """Persist all post-chapter artifacts to bible/quality/checkpoint stores."""
    bible = state["bible_store"]
    quality = state["quality_store"]
    checkpoint = state["checkpoint_store"]
    progression = state.get("progression_mgr") or ProgressionMemoryManager()
    volume_no = int(state.get("volume_no") or volume_no_for_chapter(state, chapter_num))
    progression_payload = progression_memory if isinstance(progression_memory, dict) else {}
    promotion_payload = progression_promotion if isinstance(progression_promotion, dict) else {}
    promotion_score = float(promotion_payload.get("promotion_score") or 0.0)
    payload: dict[str, Any] = {
        "state": state,
        "db": db,
        "chapter_num": chapter_num,
        "volume_no": volume_no,
        "summary_text": summary_text,
        "final_content": final_content,
        "language_score": language_score,
        "aesthetic_score_val": aesthetic_score_val,
        "revision_count": revision_count,
        "extracted_facts": extracted_facts or {},
        "progression_payload": progression_payload,
        "promotion_score": promotion_score,
        "outline": state.get("outline") or {},
        "bible": bible,
        "quality": quality,
        "checkpoint": checkpoint,
        "progression": progression,
        "volume_arc_state": {},
        "book_progression_state": {},
    }
    bus = _build_chapter_finalized_event_bus()
    dispatch_result = bus.dispatch(GenerationEvent(name="chapter.finalized", payload=payload))
    for failure in dispatch_result.get("failures") or []:
        logger.warning(
            "Optional chapter.finalized handler failed",
            extra={
                "handler": failure.get("handler"),
                "chapter_num": chapter_num,
                "volume_no": volume_no,
                "required": failure.get("required"),
                "error": failure.get("error"),
            },
        )

    is_volume_end = int(chapter_num) == int(state.get("segment_end_chapter") or state["end_chapter"])
    if is_volume_end:
        _write_volume_report(state, chapter_num, volume_no, quality, bible, checkpoint, db=db)


def _write_volume_report(
    state: GenerationState,
    chapter_num: int,
    volume_no: int,
    quality: Any,
    bible: Any,
    checkpoint: Any,
    db: Session | None = None,
) -> None:
    """Write end-of-volume quality report and snapshot."""
    chapter_reports = quality.list_reports(
        novel_id=state["novel_id"],
        novel_version_id=state.get("novel_version_id"),
        scope="chapter",
        db=db,
    )
    current_volume_reports = [r for r in chapter_reports if int((r.metrics_json or {}).get("volume_no") or 0) == volume_no]
    review_scores = [
        float((r.metrics_json or {}).get("review_score") or 0.0)
        for r in current_volume_reports
        if (r.metrics_json or {}).get("review_score") is not None
    ]
    language_scores = [
        float((r.metrics_json or {}).get("language_score") or 0.0)
        for r in current_volume_reports
        if (r.metrics_json or {}).get("language_score") is not None
    ]
    aesthetic_scores = [
        float((r.metrics_json or {}).get("aesthetic_score") or 0.0)
        for r in current_volume_reports
        if (r.metrics_json or {}).get("aesthetic_score") is not None
    ]
    blocked_count = sum(1 for r in current_volume_reports if bool((r.metrics_json or {}).get("blocked")))
    avg_review = round(sum(review_scores) / max(len(review_scores), 1), 4)
    avg_language = round(sum(language_scores) / max(len(language_scores), 1), 4)
    avg_aesthetic = round(sum(aesthetic_scores) / max(len(aesthetic_scores), 1), 4)
    progression_scores = [
        float((r.metrics_json or {}).get("progression_score") or 0.0)
        for r in current_volume_reports
        if (r.metrics_json or {}).get("progression_score") is not None
    ]
    avg_progression = round(sum(progression_scores) / max(len(progression_scores), 1), 4)
    duplication_risks = [
        float((r.metrics_json or {}).get("duplication_risk") or 0.0)
        for r in current_volume_reports
        if (r.metrics_json or {}).get("duplication_risk") is not None
    ]
    avg_duplication_risk = round(sum(duplication_risks) / max(len(duplication_risks), 1), 4)
    evidence_chain: list[dict[str, Any]] = []
    if blocked_count > 0:
        evidence_chain.append({"metric": "blocked_chapters", "value": blocked_count, "threshold": 0, "status": "fail"})
    if avg_review < REVIEW_SCORE_THRESHOLD:
        evidence_chain.append(
            {
                "metric": "avg_review_score",
                "value": avg_review,
                "threshold": REVIEW_SCORE_THRESHOLD,
                "status": "warning" if avg_review >= 0.6 else "fail",
            }
        )
    if avg_language < 0.65:
        evidence_chain.append(
            {
                "metric": "avg_language_score",
                "value": avg_language,
                "threshold": 0.65,
                "status": "warning" if avg_language >= 0.58 else "fail",
            }
        )
    if avg_aesthetic < 0.62:
        evidence_chain.append(
            {
                "metric": "avg_aesthetic_score",
                "value": avg_aesthetic,
                "threshold": 0.62,
                "status": "warning",
            }
        )
    if avg_progression < 0.62:
        evidence_chain.append(
            {
                "metric": "avg_progression_score",
                "value": avg_progression,
                "threshold": 0.62,
                "status": "warning" if avg_progression >= 0.5 else "fail",
            }
        )
    if blocked_count > 0 or avg_review < 0.6 or avg_language < 0.58:
        volume_verdict = "fail"
    elif avg_review < REVIEW_SCORE_THRESHOLD or avg_language < 0.65 or avg_aesthetic < 0.62 or avg_progression < 0.62:
        volume_verdict = "warning"
    else:
        volume_verdict = "pass"

    progression = state.get("progression_mgr") or ProgressionMemoryManager()
    volume_arc_state = progression.get_volume_arc_state(
        state["novel_id"],
        volume_no,
        novel_version_id=state.get("novel_version_id"),
        db=db,
    ) or {}
    book_progression_state = progression.get_book_progression_state(
        state["novel_id"],
        novel_version_id=state.get("novel_version_id"),
        db=db,
    ) or {}

    bible.save_snapshot(
        novel_id=state["novel_id"],
        novel_version_id=state.get("novel_version_id"),
        volume_no=volume_no,
        chapter_end=chapter_num,
        snapshot_json={
            "chapter_end": chapter_num,
            "token_usage_input": state.get("total_input_tokens", 0),
            "token_usage_output": state.get("total_output_tokens", 0),
            "estimated_cost": state.get("estimated_cost", 0.0),
            "avg_review_score": avg_review,
            "avg_language_score": avg_language,
            "avg_aesthetic_score": avg_aesthetic,
            "avg_progression_score": avg_progression,
            "avg_duplication_risk": avg_duplication_risk,
            "blocked_chapters": blocked_count,
            "evidence_chain": evidence_chain,
            "volume_arc_state": volume_arc_state,
            "book_progression_state": book_progression_state,
        },
        db=db,
    )
    quality.add_report(
        novel_id=state["novel_id"],
        novel_version_id=state.get("novel_version_id"),
        scope="volume",
        scope_id=str(volume_no),
        metrics_json={
            "chapter_end": chapter_num,
            "volume_no": volume_no,
            "estimated_cost": state.get("estimated_cost", 0.0),
            "avg_review_score": avg_review,
            "avg_language_score": avg_language,
            "avg_aesthetic_score": avg_aesthetic,
            "avg_progression_score": avg_progression,
            "avg_duplication_risk": avg_duplication_risk,
            "blocked_chapters": blocked_count,
            "chapter_count": len(current_volume_reports),
            "gate_triggered": volume_verdict != "pass",
            "evidence_chain": evidence_chain,
            "volume_arc_state": volume_arc_state,
            "book_progression_state": book_progression_state,
        },
        verdict=volume_verdict,
        db=db,
    )
    if state.get("task_id"):
        checkpoint.save_checkpoint(
            task_id=state["task_id"],
            novel_id=state["novel_id"],
            volume_no=volume_no,
            chapter_num=chapter_num,
            node="volume_gate",
            state_json={
                "volume_no": volume_no,
                "chapter_end": chapter_num,
                "verdict": volume_verdict,
                "evidence_chain": evidence_chain,
                "next_replan_mode": "aggressive" if volume_verdict == "fail" else ("focus" if volume_verdict == "warning" else "baseline"),
                "book_progression_state": book_progression_state,
            },
            db=db,
        )
