"""Initialisation, prewrite, and outline graph nodes."""
from __future__ import annotations

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.i18n import get_native_style_profile
from app.core.strategy import get_model_for_stage
from app.models.novel import Novel
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
    load_outlines_from_db,
    load_prewrite_artifacts,
    logger,
    save_full_outlines,
    save_prewrite_artifacts,
)
from app.services.generation.progress import persist_resume_runtime_state, progress
from app.services.generation.segment_plan import (
    build_segment_plan,
    merge_outlines,
    restore_segment_plan_outlines,
    segment_plan_covers_range,
)
from app.services.generation.state import GenerationState
from app.services.memory.character_state import CharacterStateManager
from app.services.memory.story_bible import CheckpointStore, QualityReportStore, StoryBibleStore
from app.services.memory.summary_manager import SummaryManager
from app.services.task_runtime.checkpoint_repo import get_resume_runtime_state


def _covers_outline_range(outlines: list[dict] | None, start_chapter: int, end_chapter: int) -> bool:
    required = set(range(int(start_chapter), int(end_chapter) + 1))
    if not required:
        return False
    available = {
        int(item.get("chapter_num") or 0)
        for item in (outlines or [])
        if isinstance(item, dict) and int(item.get("chapter_num") or 0) > 0
    }
    return required.issubset(available)


def _is_resume_like(state: GenerationState) -> bool:
    current = int(state.get("current_chapter") or state.get("start_chapter") or 1)
    segment_start = int(state.get("segment_start_chapter") or state.get("start_chapter") or 1)
    book_start = int(state.get("book_start_chapter") or segment_start)
    return current > segment_start or segment_start > book_start


def node_init(state: GenerationState) -> GenerationState:
    db = SessionLocal()
    try:
        novel_stmt = select(Novel).where(Novel.id == state["novel_id"])
        novel = db.execute(novel_stmt).scalar_one_or_none()
        if not novel:
            raise ValueError(f"Novel {state['novel_id']} not found")
        strategy = novel.strategy or "web-novel"
        target_language = novel.target_language or "zh"
        config = novel.config or {}
        volume_size = int((config.get("volume_size") or 30))
        flex_abs = max(0, int(config.get("chapter_flex_max_abs", 2) or 2))
        flex_ratio = float(config.get("chapter_flex_max_ratio", 0.1) or 0.1)
        segment_start = int(state.get("segment_start_chapter") or state["start_chapter"])
        segment_target = int(state.get("segment_target_chapters") or state["num_chapters"])
        segment_end = int(state.get("segment_end_chapter") or (segment_start + segment_target - 1))
        book_start = int(state.get("book_start_chapter") or segment_start)
        book_target_total = int(state.get("book_target_total_chapters") or state["num_chapters"])
        book_effective_end = int(state.get("book_effective_end_chapter") or (book_start + book_target_total - 1))
        runtime_state: dict[str, object] = {}
        if state.get("creation_task_id"):
            runtime_state = get_resume_runtime_state(db, creation_task_id=int(state["creation_task_id"]))
        resume_chapter = int(state.get("next_chapter") or runtime_state.get("next_chapter") or segment_start)
        if resume_chapter < segment_start or resume_chapter > (segment_end + 1):
            resume_chapter = segment_start
        retry_resume_chapter = int(runtime_state.get("retry_resume_chapter") or resume_chapter)
        segment_plan = runtime_state.get("segment_plan")
        segment_plan = dict(segment_plan) if isinstance(segment_plan, dict) else None
        if not segment_plan_covers_range(segment_plan, start_chapter=segment_start, end_chapter=segment_end):
            segment_plan = None
        flex_by_ratio = max(0, int(round(book_target_total * max(flex_ratio, 0.0))))
        flex_window = min(flex_abs, flex_by_ratio if flex_by_ratio > 0 else flex_abs)
        min_total = max(1, book_target_total - flex_window)
        max_total = max(book_target_total, book_target_total + flex_window)
        return {
            "book_start_chapter": book_start,
            "book_target_total_chapters": book_target_total,
            "book_effective_end_chapter": max(book_effective_end, segment_end),
            "book_min_total_chapters": min_total,
            "book_max_total_chapters": max_total,
            "segment_start_chapter": segment_start,
            "segment_target_chapters": segment_target,
            "segment_end_chapter": segment_end,
            "next_chapter": segment_start,
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
                "closure_threshold": float(config.get("closure_threshold", 0.95) or 0.95),
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
            "current_chapter": resume_chapter,
            "start_chapter": segment_start,
            "num_chapters": segment_target,
            "end_chapter": segment_end,
            "segment_plan": segment_plan or {},
            "retry_resume_chapter": retry_resume_chapter,
            "creation_task_id": state.get("creation_task_id"),
            "target_chapters": book_target_total,
            "min_total_chapters": min_total,
            "max_total_chapters": max_total,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "estimated_cost": 0.0,
            "review_attempt": 0,
            "rerun_count": 0,
            "volume_size": max(volume_size, 1),
            "volume_no": int(state.get("volume_no") or 1),
            "bible_store": StoryBibleStore(),
            "checkpoint_store": CheckpointStore(),
            "quality_store": QualityReportStore(),
            "volume_plan": {},
            "decision_state": {"closure": {}, "pacing": {"mode": "normal"}, "quality": {}},
            "closure_state": {},
            "tail_rewrite_attempts": 0,
            "bridge_attempts": 0,
            "low_progress_streak": 0,
            "pacing_mode": "normal",
            "review_suggestions": {},
            "consistency_scorecard": {},
            "review_gate": {},
        }
    finally:
        db.close()


def node_prewrite(state: GenerationState) -> GenerationState:
    if _is_resume_like(state):
        existing = load_prewrite_artifacts(state["novel_id"])
        if existing:
            logger.info("Resume: loaded existing prewrite for novel %s (current_chapter=%s)", state["novel_id"], state["current_chapter"])
            progress(state, "constitution", 0, 2, "加载已有创作宪法...", {"current_phase": "prewrite", "total_chapters": state["num_chapters"]})
            return {"prewrite": existing}
    progress(state, "constitution", 0, 2, "生成创作宪法...", {"current_phase": "prewrite", "total_chapters": state["num_chapters"]})
    pre_provider, pre_model = get_model_for_stage(state["strategy"], "architect")
    prewrite = state["prewrite_agent"].run(state["novel_info"], state["num_chapters"], state["target_language"], pre_provider, pre_model)
    save_prewrite_artifacts(state["novel_id"], prewrite)
    return {"prewrite": prewrite}


def node_outline(state: GenerationState) -> GenerationState:
    segment_start = int(state.get("segment_start_chapter") or state["start_chapter"])
    segment_end = int(state.get("segment_end_chapter") or state["end_chapter"])
    existing = load_outlines_from_db(state["novel_id"], state.get("novel_version_id"))
    runtime_plan = state.get("segment_plan") if isinstance(state.get("segment_plan"), dict) else None
    if segment_plan_covers_range(runtime_plan, start_chapter=segment_start, end_chapter=segment_end):
        db = SessionLocal()
        try:
            restored = restore_segment_plan_outlines(
                novel_id=state["novel_id"],
                novel_version_id=state.get("novel_version_id"),
                segment_plan=runtime_plan or {},
                db=db,
            )
            db.commit()
        finally:
            db.close()
        full_outlines = merge_outlines(existing, restored)
        progress(state, "full_outline_ready", 0, 20, "恢复既有章节计划...", {"current_phase": "outline_ready", "total_chapters": state["num_chapters"]})
        return {
            "full_outlines": full_outlines,
            "segment_plan": runtime_plan,
            "retry_resume_chapter": int(state.get("retry_resume_chapter") or state.get("current_chapter") or segment_start),
        }

    if _covers_outline_range(existing, segment_start, segment_end):
        plan = build_segment_plan(
            start_chapter=segment_start,
            end_chapter=segment_end,
            volume_no=int(state.get("volume_no") or 1),
            plan_kind="normal",
            outlines=existing,
        )
        if state.get("creation_task_id"):
            persist_resume_runtime_state(
                state,
                mode="segment_running",
                next_chapter=int(state.get("current_chapter") or segment_start),
                segment_start_chapter=segment_start,
                segment_end_chapter=segment_end,
                book_effective_end_chapter=int(state.get("book_effective_end_chapter") or segment_end),
                volume_no=int(state.get("volume_no") or 1),
                retry_resume_chapter=int(state.get("retry_resume_chapter") or state.get("current_chapter") or segment_start),
                segment_plan=plan,
            )
        if _is_resume_like(state):
            logger.info(
                "Resume: loaded outlines for novel %s range=%s-%s",
                state["novel_id"],
                segment_start,
                segment_end,
            )
            progress(state, "full_outline_ready", 0, 20, "加载已有章节大纲...", {"current_phase": "outline_ready", "total_chapters": state["num_chapters"]})
        else:
            progress(state, "full_outline_ready", 0, 20, "全书章节大纲已确定", {"current_phase": "outline_ready", "total_chapters": state["num_chapters"]})
        return {
            "full_outlines": existing,
            "segment_plan": plan,
            "retry_resume_chapter": int(state.get("retry_resume_chapter") or state.get("current_chapter") or segment_start),
        }

    progress(state, "specify_plan_tasks", 0, 10, "完成规格/计划/任务分解...", {"current_phase": "prewrite", "total_chapters": state["num_chapters"]})
    out_provider, out_model = get_model_for_stage(state["strategy"], "outliner")
    full_outlines = state["outliner"].run_full_book(
        novel_id=state["novel_id"],
        num_chapters=state["num_chapters"],
        prewrite=state["prewrite"],
        start_chapter=segment_start,
        language=state["target_language"],
        provider=out_provider,
        model=out_model,
    )
    is_resume = _is_resume_like(state)
    save_full_outlines(state["novel_id"], full_outlines, novel_version_id=state.get("novel_version_id"), replace_all=not is_resume)
    full_outlines = load_outlines_from_db(state["novel_id"], state.get("novel_version_id"))
    plan = build_segment_plan(
        start_chapter=segment_start,
        end_chapter=segment_end,
        volume_no=int(state.get("volume_no") or 1),
        plan_kind="normal",
        outlines=full_outlines,
    )
    if state.get("creation_task_id"):
        persist_resume_runtime_state(
            state,
            mode="segment_running",
            next_chapter=int(state.get("current_chapter") or segment_start),
            segment_start_chapter=segment_start,
            segment_end_chapter=segment_end,
            book_effective_end_chapter=int(state.get("book_effective_end_chapter") or segment_end),
            volume_no=int(state.get("volume_no") or 1),
            retry_resume_chapter=int(state.get("retry_resume_chapter") or state.get("current_chapter") or segment_start),
            segment_plan=plan,
        )
    progress(state, "full_outline_ready", 0, 20, "全书章节大纲已确定", {"current_phase": "outline_ready", "total_chapters": state["num_chapters"]})
    return {
        "full_outlines": full_outlines,
        "segment_plan": plan,
        "retry_resume_chapter": int(state.get("retry_resume_chapter") or state.get("current_chapter") or segment_start),
    }
