"""LangGraph orchestration for novel generation.

Graph is compiled once at module level (singleton) to avoid per-invocation overhead.
All shared helpers are imported from common.py — no circular dependency with pipeline.py.
"""
import time
from typing import Any, Callable, TypedDict
import re

from langgraph.graph import END, StateGraph
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.i18n import evaluate_language_quality, get_native_style_profile
from app.core.strategy import get_model_for_stage
from app.models.novel import Chapter, GenerationTask, Novel, GenerationCheckpoint, NovelFeedback
from app.services.generation.agents import (
    FactExtractorAgent,
    FinalizerAgent,
    FinalReviewerAgent,
    OutlinerAgent,
    PrewritePlannerAgent,
    ReviewerAgent,
    WriterAgent,
)
from app.services.generation.common import (
    MAX_RETRIES,
    REVIEW_SCORE_THRESHOLD,
    estimate_tokens,
    generate_chapter_summary,
    logger,
    save_full_outlines,
    save_prewrite_artifacts,
    update_character_states_from_content,
)
from app.services.memory.character_state import CharacterStateManager
from app.services.memory.summary_manager import SummaryManager
from app.services.memory.story_bible import StoryBibleStore, CheckpointStore, QualityReportStore


# ---------------------------------------------------------------------------
# State schema
# ---------------------------------------------------------------------------

class GenerationState(TypedDict, total=False):
    novel_id: int
    num_chapters: int
    start_chapter: int
    current_chapter: int
    end_chapter: int
    task_id: str | None
    progress_callback: Callable[..., None]
    strategy: str
    target_language: str
    native_style_profile: str
    novel_info: dict[str, Any]
    prewrite: dict[str, Any]
    full_outlines: list[dict[str, Any]]
    summary_mgr: SummaryManager
    char_mgr: CharacterStateManager
    prewrite_agent: PrewritePlannerAgent
    outliner: OutlinerAgent
    writer: WriterAgent
    reviewer: ReviewerAgent
    finalizer: FinalizerAgent
    final_reviewer: FinalReviewerAgent
    fact_extractor: FactExtractorAgent
    outline: dict[str, Any]
    context: dict[str, Any]
    consistency_report: Any
    draft: str
    candidate_drafts: list[dict[str, Any]]
    feedback: str
    factual_feedback: str
    aesthetic_feedback: str
    score: float
    factual_score: float
    aesthetic_review_score: float
    review_attempt: int
    rerun_count: int
    chapter_token_snapshot: dict[str, int]
    total_input_tokens: int
    total_output_tokens: int
    estimated_cost: float
    volume_size: int
    bible_store: StoryBibleStore
    checkpoint_store: CheckpointStore
    quality_store: QualityReportStore
    quality_passed: bool
    volume_no: int
    volume_plan: dict[str, Any]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _volume_no_for_chapter(state: GenerationState, chapter: int) -> int:
    volume_size = max(int(state.get("volume_size") or 30), 1)
    start = state.get("start_chapter") or 1
    offset = max(0, chapter - start)
    return (offset // volume_size) + 1


def _progress(state: GenerationState, step: str, chapter: int, pct: float, msg: str, meta: dict | None = None) -> None:
    cb = state.get("progress_callback")
    if cb:
        payload = dict(meta or {})
        if chapter > 0:
            payload.setdefault("volume_no", _volume_no_for_chapter(state, chapter))
            payload.setdefault("volume_size", int(state.get("volume_size") or 30))
        cb(step, chapter, pct, msg, payload)


def _chapter_progress(state: GenerationState, phase_ratio: float) -> float:
    total = max(state["num_chapters"], 1)
    idx = max(0, state["current_chapter"] - state["start_chapter"])
    base_pct = 20 + (idx / total) * 70
    span = 70 / total
    return base_pct + span * phase_ratio


def _is_volume_start(state: GenerationState, chapter: int) -> bool:
    volume_size = max(int(state.get("volume_size") or 30), 1)
    start = state.get("start_chapter") or 1
    return (chapter - start) % volume_size == 0


def _aesthetic_score(text: str) -> float:
    """Heuristic readability/aesthetic score in 0-1."""
    if not text:
        return 0.0
    paragraphs = [p for p in text.splitlines() if p.strip()]
    sentence_count = max(1, len(re.findall(r"[。！？!?\.]", text)))
    avg_sentence_len = len(text) / sentence_count
    paragraph_bonus = min(len(paragraphs) / 12.0, 1.0) * 0.2
    rhythm = 1.0 - min(abs(avg_sentence_len - 28) / 60.0, 1.0)
    return max(0.0, min(1.0, 0.55 + paragraph_bonus + rhythm * 0.25))


def _extract_timeline_markers(text: str) -> list[str]:
    patterns = [
        r"第[一二三四五六七八九十百\d]+天",
        r"次日",
        r"翌日",
        r"当晚",
        r"[一二三四五六七八九十\d]+日后",
        r"[一二三四五六七八九十\d]+小时后",
    ]
    results: list[str] = []
    for p in patterns:
        for m in re.findall(p, text):
            if m not in results:
                results.append(m)
    return results[:10]


def _extract_item_mentions(text: str) -> list[str]:
    quoted = re.findall(r"[“\"]([^”\"]{2,12})[”\"]", text)
    item_like = [x.strip() for x in quoted if any(k in x for k in ["剑", "刀", "符", "印", "戒", "卷", "令", "丹", "石"])]
    dedup: list[str] = []
    for x in item_like:
        if x and x not in dedup:
            dedup.append(x)
    return dedup[:10]


def _write_longform_artifacts(
    state: GenerationState,
    chapter_num: int,
    summary_text: str,
    final_content: str,
    language_score: float,
    aesthetic_score: float,
    revision_count: int,
    extracted_facts: dict[str, Any] | None = None,
) -> None:
    bible = state["bible_store"]
    quality = state["quality_store"]
    checkpoint = state["checkpoint_store"]

    outline = state.get("outline") or {}
    volume_no = _volume_no_for_chapter(state, chapter_num)

    # Seed core character entities once at pipeline start.
    if chapter_num == state.get("start_chapter", 1):
        chars = ((state.get("prewrite") or {}).get("specification") or {}).get("characters") or []
        if isinstance(chars, list):
            for c in chars[:80]:
                if isinstance(c, dict) and c.get("name"):
                    bible.upsert_entity(
                        novel_id=state["novel_id"],
                        entity_type="character",
                        name=str(c["name"]),
                        status="alive",
                        summary=str(c.get("description") or c.get("role") or "")[:300],
                        metadata={"source": "specification"},
                    )

    event_id = f"EV-{chapter_num:04d}"
    bible.add_event(
        novel_id=state["novel_id"],
        event_id=event_id,
        chapter_num=chapter_num,
        title=outline.get("title") or f"第{chapter_num}章",
        event_type="chapter",
        payload={
            "summary": summary_text[:600],
            "review_score": state.get("score"),
            "language_score": language_score,
        },
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
                foreshadow_id=fs_id,
                title=text[:200],
                planted_chapter=chapter_num,
                state="planted",
                payload={"outline": outline.get("title")},
            )

    payoff = str(outline.get("payoff") or "").strip()
    if payoff:
        matched = re.findall(r"[A-Z]-\d+", payoff)
        for fs_id in matched[:5]:
            bible.upsert_foreshadow(
                novel_id=state["novel_id"],
                foreshadow_id=fs_id,
                title=payoff[:200],
                planted_chapter=max(1, chapter_num - 1),
                state="resolved",
                resolved_chapter=chapter_num,
                payload={"payoff": payoff[:500]},
            )

    extracted = extracted_facts or {}
    for ev in (extracted.get("events") or [])[:20]:
        if not isinstance(ev, dict):
            continue
        eid = str(ev.get("id") or f"EVX-{chapter_num:04d}-{abs(hash(str(ev))) % 100000:05d}")[:64]
        bible.add_event(
            novel_id=state["novel_id"],
            event_id=eid,
            chapter_num=chapter_num,
            title=str(ev.get("title") or "")[:255] or f"第{chapter_num}章事件",
            event_type=str(ev.get("type") or "extracted")[:100],
            actors=[str(x) for x in (ev.get("actors") or [])][:10],
            payload={
                "summary": str(ev.get("summary") or "")[:500],
                "time_marker": str(ev.get("time_marker") or "")[:80],
            },
        )

    extracted_entities: dict[str, Any] = {}
    for ent in (extracted.get("entities") or [])[:30]:
        if not isinstance(ent, dict) or not ent.get("name"):
            continue
        entity = bible.upsert_entity(
            novel_id=state["novel_id"],
            entity_type=str(ent.get("entity_type") or "unknown")[:50],
            name=str(ent.get("name"))[:255],
            status=str(ent.get("status") or "active")[:50],
            summary=str(ent.get("summary") or "")[:300],
            metadata={"source": "fact_extractor"},
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
                entity_type="unknown",
                name=entity_name[:255],
                status="active",
                summary="",
                metadata={"source": "fact_extractor_fact_only"},
            )
        bible.add_fact(
            novel_id=state["novel_id"],
            entity_id=entity.id,
            fact_type=str(fact.get("fact_type") or "attribute")[:100],
            value_json={"value": fact.get("value"), "chapter_num": chapter_num},
            chapter_from=chapter_num,
        )

    # Fact writeback: timeline markers and key item mentions.
    for marker in _extract_timeline_markers(final_content):
        bible.add_event(
            novel_id=state["novel_id"],
            event_id=f"TL-{chapter_num:04d}-{abs(hash(marker)) % 10000:04d}",
            chapter_num=chapter_num,
            title=f"时间标记:{marker}",
            event_type="timeline_marker",
            payload={"marker": marker},
        )

    for item_name in _extract_item_mentions(final_content):
        item_entity = bible.upsert_entity(
            novel_id=state["novel_id"],
            entity_type="item",
            name=item_name,
            status="active",
            summary=f"在第{chapter_num}章出现",
            metadata={"source": "chapter_content"},
        )
        bible.add_fact(
            novel_id=state["novel_id"],
            entity_id=item_entity.id,
            fact_type="mentioned_in_chapter",
            value_json={"chapter_num": chapter_num, "context": final_content[:300]},
            chapter_from=chapter_num,
        )

    # Relationship signal writeback by character co-occurrence.
    chars = bible.list_entities(state["novel_id"], entity_type="character")
    appeared = [c for c in chars if c.name and c.name in final_content]
    for i in range(len(appeared)):
        for j in range(i + 1, len(appeared)):
            a = appeared[i]
            b = appeared[j]
            bible.add_event(
                novel_id=state["novel_id"],
                event_id=f"REL-{chapter_num:04d}-{a.id}-{b.id}",
                chapter_num=chapter_num,
                title=f"{a.name}/{b.name} 关系信号",
                event_type="relationship_signal",
                actors=[a.name, b.name],
                payload={"signal": "co_appearance"},
            )

    factual_score = float(state.get("factual_score", 0.0) or 0.0)
    reviewer_aesthetic = float(state.get("aesthetic_review_score", 0.0) or 0.0)
    verdict = "pass" if (
        state.get("score", 0.0) >= REVIEW_SCORE_THRESHOLD
        and factual_score >= 0.65
        and language_score >= 0.6
        and reviewer_aesthetic >= 0.6
    ) else "warning"
    quality.add_report(
        novel_id=state["novel_id"],
        scope="chapter",
        scope_id=str(chapter_num),
        metrics_json={
            "review_score": state.get("score", 0.0),
            "factual_score": factual_score,
            "language_score": language_score,
            "aesthetic_review_score": reviewer_aesthetic,
            "aesthetic_score": aesthetic_score,
            "revision_count": revision_count,
            "volume_no": volume_no,
        },
        verdict=verdict,
    )

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
                "language_score": language_score,
                "summary": summary_text[:400],
                "content_preview": final_content[:400],
            },
        )

    volume_size = max(int(state.get("volume_size") or 30), 1)
    is_volume_end = (chapter_num - state.get("start_chapter", 1) + 1) % volume_size == 0 or chapter_num == state["end_chapter"]
    if is_volume_end:
        chapter_reports = quality.list_reports(novel_id=state["novel_id"], scope="chapter")
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
        if blocked_count > 0 or avg_review < 0.6 or avg_language < 0.58:
            volume_verdict = "fail"
        elif avg_review < REVIEW_SCORE_THRESHOLD or avg_language < 0.65 or avg_aesthetic < 0.62:
            volume_verdict = "warning"
        else:
            volume_verdict = "pass"

        bible.save_snapshot(
            novel_id=state["novel_id"],
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
                "blocked_chapters": blocked_count,
                "evidence_chain": evidence_chain,
            },
        )
        quality.add_report(
            novel_id=state["novel_id"],
            scope="volume",
            scope_id=str(volume_no),
            metrics_json={
                "chapter_end": chapter_num,
                "volume_no": volume_no,
                "estimated_cost": state.get("estimated_cost", 0.0),
                "avg_review_score": avg_review,
                "avg_language_score": avg_language,
                "avg_aesthetic_score": avg_aesthetic,
                "blocked_chapters": blocked_count,
                "chapter_count": len(current_volume_reports),
                "gate_triggered": volume_verdict != "pass",
                "evidence_chain": evidence_chain,
            },
            verdict=volume_verdict,
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
                },
            )


# ---------------------------------------------------------------------------
# Graph nodes
# ---------------------------------------------------------------------------

def _node_init(state: GenerationState) -> GenerationState:
    db = SessionLocal()
    try:
        novel_stmt = select(Novel).where(Novel.id == state["novel_id"])
        novel = db.execute(novel_stmt).scalar_one_or_none()
        if not novel:
            raise ValueError(f"Novel {state['novel_id']} not found")
        strategy = novel.strategy or "web-novel"
        target_language = novel.target_language or "zh"
        volume_size = int(((novel.config or {}).get("volume_size") or 30))
        return {
            "strategy": strategy,
            "target_language": target_language,
            "native_style_profile": novel.native_style_profile or get_native_style_profile(target_language),
            "novel_info": {
                "title": novel.title,
                "genre": novel.genre,
                "style": novel.style,
                "audience": novel.audience,
                "target_length": novel.target_length,
                "writing_method": novel.writing_method,
                "user_idea": novel.user_idea,
            },
            "summary_mgr": SummaryManager(),
            "char_mgr": CharacterStateManager(),
            "prewrite_agent": PrewritePlannerAgent(),
            "outliner": OutlinerAgent(),
            "writer": WriterAgent(),
            "reviewer": ReviewerAgent(),
            "finalizer": FinalizerAgent(),
            "final_reviewer": FinalReviewerAgent(),
            "fact_extractor": FactExtractorAgent(),
            "current_chapter": state["start_chapter"],
            "end_chapter": state["start_chapter"] + state["num_chapters"] - 1,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "estimated_cost": 0.0,
            "review_attempt": 0,
            "rerun_count": 0,
            "volume_size": max(volume_size, 1),
            "bible_store": StoryBibleStore(),
            "checkpoint_store": CheckpointStore(),
            "quality_store": QualityReportStore(),
            "volume_no": 1,
            "volume_plan": {},
        }
    finally:
        db.close()


def _node_prewrite(state: GenerationState) -> GenerationState:
    _progress(state, "constitution", 0, 2, "生成创作宪法...", {"current_phase": "prewrite", "total_chapters": state["num_chapters"]})
    pre_provider, pre_model = get_model_for_stage(state["strategy"], "architect")
    prewrite = state["prewrite_agent"].run(state["novel_info"], state["num_chapters"], state["target_language"], pre_provider, pre_model)
    save_prewrite_artifacts(state["novel_id"], prewrite)
    return {"prewrite": prewrite}


def _node_outline(state: GenerationState) -> GenerationState:
    _progress(state, "specify_plan_tasks", 0, 10, "完成规格/计划/任务分解...", {"current_phase": "prewrite", "total_chapters": state["num_chapters"]})
    out_provider, out_model = get_model_for_stage(state["strategy"], "outliner")
    full_outlines = state["outliner"].run_full_book(
        state["novel_id"],
        state["num_chapters"],
        state["prewrite"],
        state["target_language"],
        out_provider,
        out_model,
    )
    save_full_outlines(state["novel_id"], full_outlines)
    _progress(state, "full_outline_ready", 0, 20, "全书章节大纲已确定", {"current_phase": "outline_ready", "total_chapters": state["num_chapters"]})
    return {"full_outlines": full_outlines}


def _node_volume_replan(state: GenerationState) -> GenerationState:
    """Build per-volume plan at volume boundaries."""
    chapter_num = state["current_chapter"]
    volume_no = _volume_no_for_chapter(state, chapter_num)
    volume_size = max(int(state.get("volume_size") or 30), 1)
    start = chapter_num
    end = min(state["end_chapter"], start + volume_size - 1)
    outlines = [o for o in (state.get("full_outlines") or []) if start <= int(o.get("chapter_num", 0)) <= end]

    previous_volume = max(0, volume_no - 1)
    quality_focus: list[str] = []
    previous_quality: dict[str, Any] = {}
    previous_snapshot: dict[str, Any] = {}
    previous_verdict = "pass"
    replan_level = "baseline"
    replan_actions = ["保持主线推进与人物动机一致。"]
    gate_evidence: list[dict[str, Any]] = []
    if previous_volume > 0:
        prev_reports = state["quality_store"].list_reports(
            novel_id=state["novel_id"],
            scope="volume",
            scope_id=str(previous_volume),
        )
        if prev_reports:
            previous_quality = prev_reports[0].metrics_json or {}
            previous_verdict = str(getattr(prev_reports[0], "verdict", "pass") or "pass")
            avg_review = float(previous_quality.get("avg_review_score") or 0.0)
            avg_language = float(previous_quality.get("avg_language_score") or 0.0)
            avg_aesthetic = float(previous_quality.get("avg_aesthetic_score") or 0.0)
            gate_evidence = previous_quality.get("evidence_chain") or []
            if avg_review < REVIEW_SCORE_THRESHOLD:
                quality_focus.append("强化单章主冲突与阶段性兑现，减少平铺叙事。")
            if avg_language < 0.65:
                quality_focus.append("优化句式节奏与口语自然度，控制说明性段落密度。")
            if avg_aesthetic < 0.62:
                quality_focus.append("提升情绪张力，保证章末悬念与反转落点。")
            if previous_verdict == "fail":
                replan_level = "aggressive"
                replan_actions = [
                    "限制支线数量，优先回收高优先级伏笔。",
                    "每3章至少一次强兑现并推进主线不可逆变化。",
                    "减少解释性段落，增加场景化冲突与动作。",
                ]
            elif previous_verdict == "warning" or quality_focus:
                replan_level = "focus"
                replan_actions = [
                    "围绕上一卷低分指标执行定向修正。",
                    "保持既有世界观与角色声纹稳定。",
                ]
        prev_snapshot = state["bible_store"].get_latest_snapshot(state["novel_id"], previous_volume)
        if prev_snapshot and isinstance(prev_snapshot.snapshot_json, dict):
            previous_snapshot = prev_snapshot.snapshot_json
        db = SessionLocal()
        try:
            fb_stmt = (
                select(NovelFeedback)
                .where(
                    NovelFeedback.novel_id == state["novel_id"],
                    NovelFeedback.volume_no == previous_volume,
                )
                .order_by(NovelFeedback.id.desc())
                .limit(10)
            )
            feedback_rows = db.execute(fb_stmt).scalars().all()
            feedback_tags = [str(t) for r in feedback_rows for t in (r.tags or []) if t]
            if feedback_tags:
                quality_focus.append(f"编辑反馈关注点: {'/'.join(feedback_tags[:6])}")
        finally:
            db.close()

    constraints = state["bible_store"].get_chapter_constraints(state["novel_id"], chapter_num)
    carry_over = [
        {
            "foreshadow_id": str(item.get("foreshadow_id") or ""),
            "title": str(item.get("title") or "")[:160],
            "planted_chapter": int(item.get("planted_chapter") or 0),
        }
        for item in (constraints.get("unresolved_foreshadows") or [])[:10]
        if item
    ]
    chapter_targets = [
        {"chapter_num": int(o.get("chapter_num", 0)), "title": o.get("title"), "goal": o.get("purpose") or "推进主线"}
        for o in outlines
    ]
    if quality_focus or replan_level != "baseline":
        for target in chapter_targets:
            goal = str(target.get("goal") or "")
            focus_text = " ".join(quality_focus) if quality_focus else "保持上一卷修正策略"
            target["goal"] = f"{goal}；质量修正: {focus_text}".strip("；")

    volume_plan = {
        "volume_no": volume_no,
        "start_chapter": start,
        "end_chapter": end,
        "theme": f"Volume-{volume_no}",
        "chapter_targets": chapter_targets,
        "quality_focus": quality_focus,
        "carry_over_foreshadows": carry_over,
        "previous_volume_quality": previous_quality,
        "previous_volume_verdict": previous_verdict,
        "replan_level": replan_level,
        "replan_actions": replan_actions,
        "gate_evidence": gate_evidence,
        "previous_volume_snapshot": previous_snapshot,
    }
    _progress(
        state,
        "volume_replan",
        chapter_num,
        _chapter_progress(state, 0.05),
        f"生成第{volume_no}卷执行计划",
        {
            "current_phase": "volume_planning",
            "total_chapters": state["num_chapters"],
            "volume_no": volume_no,
            "replan_level": replan_level,
        },
    )
    if state.get("task_id"):
        state["checkpoint_store"].save_checkpoint(
            task_id=state["task_id"],
            novel_id=state["novel_id"],
            volume_no=volume_no,
            chapter_num=chapter_num,
            node="volume_replan",
            state_json=volume_plan,
        )
    return {"volume_no": volume_no, "volume_plan": volume_plan}


def _node_confirmation_gate(state: GenerationState) -> GenerationState:
    if not state.get("task_id"):
        return {}
    db = SessionLocal()
    try:
        novel_stmt = select(Novel).where(Novel.id == state["novel_id"])
        novel_row = db.execute(novel_stmt).scalar_one_or_none()
        require_confirm = bool((novel_row.config or {}).get("require_outline_confirmation"))
        if not require_confirm:
            return {}
        gt_stmt = select(GenerationTask).where(GenerationTask.task_id == state["task_id"])
        gt = db.execute(gt_stmt).scalar_one_or_none()
        if gt:
            gt.status = "awaiting_outline_confirmation"
            gt.current_phase = "outline_ready"
            gt.message = "章节大纲已生成，等待确认"
            novel_row.status = "awaiting_outline_confirmation"
            db.commit()
            _progress(
                state,
                "outline_waiting_confirmation",
                0,
                20,
                "等待用户确认大纲后继续生成",
                {"status": "awaiting_outline_confirmation", "current_phase": "outline_ready", "total_chapters": state["num_chapters"]},
            )
        while gt and gt.outline_confirmed != 1:
            db.refresh(gt)
            time.sleep(2)
        if gt:
            gt.status = "running"
            gt.current_phase = "chapter_writing"
            novel_row.status = "generating"
            db.commit()
    finally:
        db.close()
    return {}


# ---------------------------------------------------------------------------
# Routing helpers
# ---------------------------------------------------------------------------

def _route_chapter_or_final(state: GenerationState) -> str:
    return "final_book_review" if state["current_chapter"] > state["end_chapter"] else "load_context"


def _route_consistency(state: GenerationState) -> str:
    return "save_blocked" if not state["consistency_report"].passed else "beats"


def _route_review(state: GenerationState) -> str:
    if state["score"] >= REVIEW_SCORE_THRESHOLD:
        return "finalizer"
    if state.get("review_attempt", 0) < MAX_RETRIES:
        return "revise"
    if state.get("rerun_count", 0) < 1:
        return "rollback_rerun"
    return "finalizer"


def _route_after_confirmation(state: GenerationState) -> str:
    if state["current_chapter"] > state["end_chapter"]:
        return "final_book_review"
    return "volume_replan" if _is_volume_start(state, state["current_chapter"]) else "load_context"


def _route_finalize(state: GenerationState) -> str:
    if state.get("quality_passed", True):
        return "advance_chapter"
    if state.get("rerun_count", 0) < 1:
        return "rollback_rerun"
    return "advance_chapter"


def _route_after_advance(state: GenerationState) -> str:
    if state["current_chapter"] > state["end_chapter"]:
        return "final_book_review"
    return "volume_replan" if _is_volume_start(state, state["current_chapter"]) else "load_context"


# ---------------------------------------------------------------------------
# Chapter-loop nodes
# ---------------------------------------------------------------------------

def _node_load_context(state: GenerationState) -> GenerationState:
    from app.services.memory.context import build_chapter_context

    chapter_num = state["current_chapter"]
    idx = chapter_num - state["start_chapter"]
    outlines = state.get("full_outlines", [])
    outline = outlines[idx] if 0 <= idx < len(outlines) else {"chapter_num": chapter_num, "title": f"第{chapter_num}章", "outline": ""}

    db = SessionLocal()
    try:
        _progress(state, "context", chapter_num, _chapter_progress(state, 0.10), "加载分层上下文...", {"current_phase": "chapter_writing", "total_chapters": state["num_chapters"]})
        ctx = build_chapter_context(state["novel_id"], chapter_num, state["prewrite"], outline, db=db)
        ctx["prewrite"] = state["prewrite"]
        ctx["chapter_outline"] = outline
        ctx["volume_plan"] = state.get("volume_plan") or {}
        ctx["hard_constraints"] = state["bible_store"].get_chapter_constraints(state["novel_id"], chapter_num, db=db)
        ctx["character_states"] = state["char_mgr"].get_states(state["novel_id"], chapter_num, db=db)
        ctx["summaries"] = state["summary_mgr"].get_summaries_before(state["novel_id"], chapter_num, db=db)
    finally:
        db.close()
    return {
        "outline": outline,
        "context": ctx,
        "draft": "",
        "candidate_drafts": [],
        "feedback": "",
        "factual_feedback": "",
        "aesthetic_feedback": "",
        "score": 0.0,
        "factual_score": 0.0,
        "aesthetic_review_score": 0.0,
        "review_attempt": 0,
        "rerun_count": 0,
        "chapter_token_snapshot": {"input": state["total_input_tokens"], "output": state["total_output_tokens"]},
    }


def _node_beats(state: GenerationState) -> GenerationState:
    chapter_num = state["current_chapter"]
    outline = state.get("outline") or {}
    beats = [
        {"name": "hook", "target": str(outline.get("hook") or "开场钩子")},
        {"name": "conflict", "target": str(outline.get("purpose") or "冲突升级")},
        {"name": "turn", "target": str(outline.get("plot_twist_level") or "中段转折")},
        {"name": "payoff", "target": str(outline.get("payoff") or "阶段兑现")},
        {"name": "cliffhanger", "target": str(outline.get("suspense_level") or "章末悬念")},
    ]
    quality_focus = ((state.get("volume_plan") or {}).get("quality_focus") or [])
    replan_actions = ((state.get("volume_plan") or {}).get("replan_actions") or [])
    if quality_focus:
        beats.append({"name": "quality_fix", "target": "；".join(str(x) for x in quality_focus[:2])})
    if replan_actions:
        beats.append({"name": "replan_action", "target": "；".join(str(x) for x in replan_actions[:2])})
    ctx = dict(state["context"])
    ctx["beat_sheet"] = beats
    _progress(
        state,
        "beats",
        chapter_num,
        _chapter_progress(state, 0.25),
        f"第{chapter_num}章节拍卡已生成",
        {"current_phase": "chapter_beats", "total_chapters": state["num_chapters"]},
    )
    return {"context": ctx}


def _node_consistency_check(state: GenerationState) -> GenerationState:
    from app.services.generation.consistency import check_consistency, inject_consistency_context

    chapter_num = state["current_chapter"]
    _progress(state, "consistency", chapter_num, _chapter_progress(state, 0.15), "一致性检查...", {"current_phase": "consistency_check", "total_chapters": state["num_chapters"]})
    report = check_consistency(state["novel_id"], chapter_num, state["outline"], state["context"], state["prewrite"])
    if report.passed:
        return {"consistency_report": report, "context": inject_consistency_context(state["context"], report)}
    return {"consistency_report": report}


def _node_save_blocked(state: GenerationState) -> GenerationState:
    chapter_num = state["current_chapter"]
    report = state["consistency_report"]
    db = SessionLocal()
    try:
        existing_stmt = select(Chapter).where(Chapter.novel_id == state["novel_id"], Chapter.chapter_num == chapter_num)
        existing = db.execute(existing_stmt).scalar_one_or_none()
        payload = {
            "title": state["outline"].get("title", f"第{chapter_num}章"),
            "content": "",
            "summary": "",
            "status": "consistency_blocked",
            "metadata_": {"consistency_report": report.summary(), "consistency_blocked": True},
        }
        if existing:
            for k, v in payload.items():
                setattr(existing, k, v)
        else:
            db.add(Chapter(novel_id=state["novel_id"], chapter_num=chapter_num, **payload))
        db.commit()
    finally:
        db.close()
    volume_no = _volume_no_for_chapter(state, chapter_num)
    state["quality_store"].add_report(
        novel_id=state["novel_id"],
        scope="chapter",
        scope_id=str(chapter_num),
        metrics_json={
            "blocked": True,
            "volume_no": volume_no,
            "reason": report.summary(),
        },
        verdict="fail",
    )
    if state.get("task_id"):
        state["checkpoint_store"].save_checkpoint(
            task_id=state["task_id"],
            novel_id=state["novel_id"],
            volume_no=volume_no,
            chapter_num=chapter_num,
            node="consistency_blocked",
            state_json={"reason": report.summary()},
        )
    _progress(state, "chapter_blocked", chapter_num, _chapter_progress(state, 1.0), f"第{chapter_num}章因一致性检查未通过已跳过", {"current_phase": "chapter_blocked", "total_chapters": state["num_chapters"]})
    return {}


def _node_writer(state: GenerationState) -> GenerationState:
    chapter_num = state["current_chapter"]
    attempt = state.get("review_attempt", 0) + 1
    _progress(state, "writer", chapter_num, _chapter_progress(state, 0.35), f"写作第{chapter_num}章（尝试{attempt}）...", {"current_phase": "chapter_writing", "total_chapters": state["num_chapters"]})
    w_provider, w_model = get_model_for_stage(state["strategy"], "writer")
    ctx_a = dict(state["context"])
    ctx_a["ab_variant"] = "A"
    ctx_a["ab_goal"] = "稳健推进主线，保持事实一致。"
    draft_a = state["writer"].run(
        state["novel_id"],
        chapter_num,
        state["outline"],
        ctx_a,
        state["target_language"],
        state["native_style_profile"],
        w_provider,
        w_model,
    )
    ctx_b = dict(state["context"])
    ctx_b["ab_variant"] = "B"
    ctx_b["ab_goal"] = "增强情绪张力和节奏反转，保持硬约束不变。"
    draft_b = state["writer"].run(
        state["novel_id"],
        chapter_num,
        state["outline"],
        ctx_b,
        state["target_language"],
        state["native_style_profile"],
        w_provider,
        w_model,
    )
    candidates = [
        {"variant": "A", "draft": draft_a},
        {"variant": "B", "draft": draft_b},
    ]
    output_tokens = state["total_output_tokens"] + estimate_tokens(draft_a) + estimate_tokens(draft_b)
    return {"candidate_drafts": candidates, "draft": draft_a, "total_output_tokens": output_tokens}


def _node_review(state: GenerationState) -> GenerationState:
    chapter_num = state["current_chapter"]
    _progress(state, "reviewer", chapter_num, _chapter_progress(state, 0.55), "章节审校...", {"current_phase": "chapter_review", "total_chapters": state["num_chapters"]})
    r_provider, r_model = get_model_for_stage(state["strategy"], "reviewer")
    candidates = state.get("candidate_drafts") or [{"variant": "A", "draft": state.get("draft", "")}]
    best = None
    for c in candidates:
        text = str(c.get("draft") or "")
        struct_score, struct_feedback = state["reviewer"].run(
            text,
            chapter_num,
            state["target_language"],
            state["native_style_profile"],
            r_provider,
            r_model,
        )
        factual_score, factual_feedback, contradictions = state["reviewer"].run_factual(
            text,
            chapter_num,
            state.get("context") or {},
            state["target_language"],
            r_provider,
            r_model,
        )
        aesthetic_score, aesthetic_feedback, highlights = state["reviewer"].run_aesthetic(
            text,
            chapter_num,
            state["target_language"],
            r_provider,
            r_model,
        )
        combined = (struct_score * 0.45) + (factual_score * 0.35) + (aesthetic_score * 0.20)
        item = {
            "variant": c.get("variant"),
            "draft": text,
            "combined": combined,
            "struct_score": struct_score,
            "factual_score": factual_score,
            "aesthetic_score": aesthetic_score,
            "feedback": struct_feedback,
            "factual_feedback": factual_feedback,
            "aesthetic_feedback": aesthetic_feedback,
            "contradictions": contradictions,
            "highlights": highlights,
        }
        if best is None or item["combined"] > best["combined"]:
            best = item
    if best is None:
        return {"score": 0.0, "feedback": "review failed", "factual_score": 0.0, "aesthetic_review_score": 0.0}
    combined_feedback = "\n".join(
        [
            f"[结构] {best['feedback']}",
            f"[事实] {best['factual_feedback']}",
            f"[审美] {best['aesthetic_feedback']}",
        ]
    ).strip()
    return {
        "draft": best["draft"],
        "score": best["combined"],
        "feedback": combined_feedback,
        "factual_feedback": best["factual_feedback"],
        "aesthetic_feedback": best["aesthetic_feedback"],
        "factual_score": best["factual_score"],
        "aesthetic_review_score": best["aesthetic_score"],
    }


def _node_revise(state: GenerationState) -> GenerationState:
    review_attempt = state.get("review_attempt", 0) + 1
    ctx = dict(state["context"])
    ctx["review_feedback"] = state["feedback"]
    return {"review_attempt": review_attempt, "context": ctx}


def _node_rollback_rerun(state: GenerationState) -> GenerationState:
    chapter_num = state["current_chapter"]
    snap = state.get("chapter_token_snapshot", {})
    ctx = dict(state["context"])
    ctx["review_feedback"] = f"{state.get('feedback', '')}\n请彻底重写该章，修复上述问题并保持连续性。"
    _progress(state, "rollback_rerun", chapter_num, _chapter_progress(state, 0.60), f"第{chapter_num}章审校未通过，回滚并重跑一次...", {"current_phase": "rollback_rerun", "total_chapters": state["num_chapters"]})
    return {
        "rerun_count": state.get("rerun_count", 0) + 1,
        "review_attempt": 0,
        "context": ctx,
        "total_input_tokens": snap.get("input", state["total_input_tokens"]),
        "total_output_tokens": snap.get("output", state["total_output_tokens"]),
    }


def _node_finalize(state: GenerationState) -> GenerationState:
    chapter_num = state["current_chapter"]
    _progress(state, "finalizer", chapter_num, _chapter_progress(state, 0.70), "定稿...", {"current_phase": "chapter_finalizing", "total_chapters": state["num_chapters"]})
    f_provider, f_model = get_model_for_stage(state["strategy"], "finalizer")
    final_content = state["finalizer"].run(state["draft"], state["feedback"], state["target_language"], f_provider, f_model)
    extracted_facts = state["fact_extractor"].run(
        chapter_num=chapter_num,
        content=final_content,
        outline=state.get("outline") or {},
        language=state["target_language"],
        provider=f_provider,
        model=f_model,
    )
    language_score, language_report = evaluate_language_quality(final_content, state["target_language"])
    aesthetic_score = _aesthetic_score(final_content)

    db = SessionLocal()
    try:
        _progress(state, "memory_update", chapter_num, _chapter_progress(state, 0.85), "生成摘要 & 更新记忆...", {"current_phase": "memory_update", "total_chapters": state["num_chapters"]})
        summary_text = generate_chapter_summary(final_content, state["outline"], chapter_num, state["target_language"], state["strategy"])
        factual_score = float(state.get("factual_score", 0.0) or 0.0)
        reviewer_aesthetic = float(state.get("aesthetic_review_score", 0.0) or 0.0)
        quality_passed = bool(
            state.get("score", 0.0) >= REVIEW_SCORE_THRESHOLD
            and factual_score >= 0.65
            and language_score >= 0.6
            and reviewer_aesthetic >= 0.6
            and aesthetic_score >= 0.6
        )
        state["summary_mgr"].add_summary(state["novel_id"], chapter_num, summary_text, db=db)
        update_character_states_from_content(
            state["novel_id"],
            chapter_num,
            final_content,
            state["prewrite"],
            state["char_mgr"],
            state["target_language"],
            state["strategy"],
            db=db,
        )
        existing_stmt = select(Chapter).where(Chapter.novel_id == state["novel_id"], Chapter.chapter_num == chapter_num)
        existing = db.execute(existing_stmt).scalar_one_or_none()
        revision_count = state.get("review_attempt", 0) + 1 + (state.get("rerun_count", 0) * (MAX_RETRIES + 1))
        payload = {
            "title": state["outline"].get("title", f"第{chapter_num}章"),
            "content": final_content,
            "summary": summary_text,
            "review_score": state["score"],
            "status": "completed" if quality_passed else "quality_blocked",
            "language_quality_score": language_score,
            "language_quality_report": language_report,
            "metadata_": {
                "language_quality_report": language_report,
                "consistency_report": state["consistency_report"].summary(),
                "revision_count": revision_count,
                "context_budget_used": state["context"].get("budget_used", 0),
                "rerun_count": state.get("rerun_count", 0),
                "factual_score": factual_score,
                "aesthetic_review_score": reviewer_aesthetic,
            },
        }
        if existing:
            for k, v in payload.items():
                setattr(existing, k, v)
        else:
            db.add(Chapter(novel_id=state["novel_id"], chapter_num=chapter_num, **payload))
        db.commit()
        _write_longform_artifacts(
            state=state,
            chapter_num=chapter_num,
            summary_text=summary_text,
            final_content=final_content,
            language_score=language_score,
            aesthetic_score=aesthetic_score,
            revision_count=revision_count,
            extracted_facts=extracted_facts,
        )
    finally:
        db.close()

    total_input_tokens = state["total_input_tokens"] + estimate_tokens(str(state["outline"]) + str(state["context"]) + (state.get("feedback") or ""))
    estimated_cost = round((total_input_tokens / 1000) * 0.0015 + (state["total_output_tokens"] / 1000) * 0.002, 6)
    _progress(
        state,
        "chapter_done",
        chapter_num,
        _chapter_progress(state, 1.0),
        f"第{chapter_num}章完成",
        {
            "current_phase": "chapter_done",
            "total_chapters": state["num_chapters"],
            "token_usage_input": total_input_tokens,
            "token_usage_output": state["total_output_tokens"],
            "estimated_cost": estimated_cost,
        },
    )
    factual_score = float(state.get("factual_score", 0.0) or 0.0)
    reviewer_aesthetic = float(state.get("aesthetic_review_score", 0.0) or 0.0)
    quality_passed = bool(
        state.get("score", 0.0) >= REVIEW_SCORE_THRESHOLD
        and factual_score >= 0.65
        and language_score >= 0.6
        and reviewer_aesthetic >= 0.6
        and aesthetic_score >= 0.6
    )
    fail_reason = ""
    if not quality_passed:
        fail_reasons: list[str] = []
        if state.get("score", 0.0) < REVIEW_SCORE_THRESHOLD:
            fail_reasons.append("结构推进不足")
        if factual_score < 0.65:
            fail_reasons.append("事实一致性不足")
        if language_score < 0.6:
            fail_reasons.append("语言自然度不足")
        if reviewer_aesthetic < 0.6 or aesthetic_score < 0.6:
            fail_reasons.append("爽点节奏与情绪张力不足")
        fail_reason = (
            f"章后质量门禁未通过：review_score={state.get('score', 0.0):.2f}, "
            f"factual_score={factual_score:.2f}, language_score={language_score:.2f}, "
            f"aesthetic_score={reviewer_aesthetic:.2f}/{aesthetic_score:.2f}；"
            f"失败类别: {','.join(fail_reasons)}。请重写并定向修复。"
        )
    return {
        "total_input_tokens": total_input_tokens,
        "estimated_cost": estimated_cost,
        "quality_passed": quality_passed,
        "feedback": fail_reason if fail_reason else state.get("feedback", ""),
    }


def _node_advance_chapter(state: GenerationState) -> GenerationState:
    return {"current_chapter": state["current_chapter"] + 1}


def _node_final_book_review(state: GenerationState) -> GenerationState:
    db = SessionLocal()
    try:
        last_chapter = state["start_chapter"] + state["num_chapters"] - 1
        all_summaries = state["summary_mgr"].get_summaries_before(state["novel_id"], last_chapter + 1, db=db)
        if all_summaries:
            chapter_payload = [{"chapter_num": s["chapter_num"], "summary": s["summary"]} for s in all_summaries]
        else:
            chapter_stmt = select(Chapter).where(Chapter.novel_id == state["novel_id"]).order_by(Chapter.chapter_num)
            chapter_rows = db.execute(chapter_stmt).scalars().all()
            chapter_payload = [{"chapter_num": c.chapter_num, "title": c.title, "content": (c.content or "")[:2000]} for c in chapter_rows]
    finally:
        db.close()

    _progress(state, "final_book_review", state["num_chapters"], 97, "全书终审...", {"current_phase": "full_book_review", "total_chapters": state["num_chapters"]})
    fr_provider, fr_model = get_model_for_stage(state["strategy"], "reviewer")
    final_report = state["final_reviewer"].run_full_book(chapter_payload, state["target_language"], fr_provider, fr_model)
    save_prewrite_artifacts(state["novel_id"], {"final_book_review": final_report})
    state["quality_store"].add_report(
        novel_id=state["novel_id"],
        scope="book",
        scope_id="final",
        metrics_json=final_report if isinstance(final_report, dict) else {"raw": str(final_report)},
        verdict="pass" if float((final_report or {}).get("score", 0.0) or 0.0) >= 0.7 else "warning",
    )
    if state.get("task_id"):
        state["checkpoint_store"].save_checkpoint(
            task_id=state["task_id"],
            novel_id=state["novel_id"],
            volume_no=_volume_no_for_chapter(state, state["end_chapter"]),
            chapter_num=state["end_chapter"],
            node="book_done",
            state_json={"final_report": final_report},
        )
    _progress(
        state,
        "done",
        state["num_chapters"],
        100,
        "全书生成完成",
        {
            "current_phase": "completed",
            "total_chapters": state["num_chapters"],
            "token_usage_input": state["total_input_tokens"],
            "token_usage_output": state["total_output_tokens"],
            "estimated_cost": state["estimated_cost"],
            "final_report": final_report,
        },
    )
    return {}


# ---------------------------------------------------------------------------
# Graph construction (singleton)
# ---------------------------------------------------------------------------

def _build_generation_graph():
    graph = StateGraph(GenerationState)
    graph.add_node("init", _node_init)
    graph.add_node("prewrite", _node_prewrite)
    graph.add_node("outline", _node_outline)
    graph.add_node("confirmation_gate", _node_confirmation_gate)
    graph.add_node("volume_replan", _node_volume_replan)
    graph.add_node("load_context", _node_load_context)
    graph.add_node("consistency_check", _node_consistency_check)
    graph.add_node("save_blocked", _node_save_blocked)
    graph.add_node("beats", _node_beats)
    graph.add_node("writer", _node_writer)
    graph.add_node("reviewer", _node_review)
    graph.add_node("revise", _node_revise)
    graph.add_node("rollback_rerun", _node_rollback_rerun)
    graph.add_node("finalizer", _node_finalize)
    graph.add_node("advance_chapter", _node_advance_chapter)
    graph.add_node("final_book_review", _node_final_book_review)

    graph.set_entry_point("init")
    graph.add_edge("init", "prewrite")
    graph.add_edge("prewrite", "outline")
    graph.add_edge("outline", "confirmation_gate")
    graph.add_conditional_edges("confirmation_gate", _route_after_confirmation, {"volume_replan": "volume_replan", "load_context": "load_context", "final_book_review": "final_book_review"})
    graph.add_edge("volume_replan", "load_context")
    graph.add_edge("load_context", "consistency_check")
    graph.add_conditional_edges("consistency_check", _route_consistency, {"save_blocked": "save_blocked", "beats": "beats"})
    graph.add_edge("beats", "writer")
    graph.add_edge("save_blocked", "advance_chapter")
    graph.add_edge("writer", "reviewer")
    graph.add_conditional_edges("reviewer", _route_review, {"revise": "revise", "rollback_rerun": "rollback_rerun", "finalizer": "finalizer"})
    graph.add_edge("revise", "writer")
    graph.add_edge("rollback_rerun", "writer")
    graph.add_conditional_edges("finalizer", _route_finalize, {"rollback_rerun": "rollback_rerun", "advance_chapter": "advance_chapter"})
    graph.add_conditional_edges("advance_chapter", _route_after_advance, {"volume_replan": "volume_replan", "load_context": "load_context", "final_book_review": "final_book_review"})
    graph.add_edge("final_book_review", END)
    return graph.compile()


_compiled_graph = _build_generation_graph()


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def run_generation_pipeline_langgraph(
    novel_id: int,
    num_chapters: int,
    start_chapter: int,
    progress_callback=None,
    task_id: str | None = None,
) -> None:
    if task_id:
        db = SessionLocal()
        try:
            cp_stmt = (
                select(GenerationCheckpoint)
                .where(GenerationCheckpoint.task_id == task_id, GenerationCheckpoint.novel_id == novel_id)
                .order_by(GenerationCheckpoint.chapter_num.desc(), GenerationCheckpoint.id.desc())
            )
            latest_cp = db.execute(cp_stmt).scalar_one_or_none()
            if latest_cp:
                target_end = start_chapter + num_chapters - 1
                resumed_start = int(latest_cp.chapter_num) + 1
                if resumed_start <= target_end:
                    num_chapters = target_end - resumed_start + 1
                    start_chapter = resumed_start
                    logger.info(
                        "Resuming task %s from chapter %s (remaining=%s)",
                        task_id,
                        start_chapter,
                        num_chapters,
                    )
                else:
                    logger.info("Task %s already completed by checkpoint at chapter %s", task_id, latest_cp.chapter_num)
                    return
        finally:
            db.close()

    _compiled_graph.invoke(
        {
            "novel_id": novel_id,
            "num_chapters": num_chapters,
            "start_chapter": start_chapter,
            "task_id": task_id,
            "progress_callback": progress_callback or (lambda *a, **k: None),
        }
    )
