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
from app.services.generation.progress import progress
from app.services.generation.state import GenerationState
from app.services.memory.character_state import CharacterStateManager
from app.services.memory.story_bible import CheckpointStore, QualityReportStore, StoryBibleStore
from app.services.memory.summary_manager import SummaryManager


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
        target_total = int(state["num_chapters"])
        flex_by_ratio = max(0, int(round(target_total * max(flex_ratio, 0.0))))
        flex_window = min(flex_abs, flex_by_ratio if flex_by_ratio > 0 else flex_abs)
        min_total = max(1, target_total - flex_window)
        max_total = max(target_total, target_total + flex_window)
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
            "current_chapter": state["start_chapter"],
            "end_chapter": state["start_chapter"] + state["num_chapters"] - 1,
            "creation_task_id": state.get("creation_task_id"),
            "target_chapters": target_total,
            "min_total_chapters": min_total,
            "max_total_chapters": max_total,
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
    if state["start_chapter"] > 1:
        existing = load_prewrite_artifacts(state["novel_id"])
        if existing:
            logger.info("Resume: loaded existing prewrite for novel %s (start_chapter=%s)", state["novel_id"], state["start_chapter"])
            progress(state, "constitution", 0, 2, "加载已有创作宪法...", {"current_phase": "prewrite", "total_chapters": state["num_chapters"]})
            return {"prewrite": existing}
    progress(state, "constitution", 0, 2, "生成创作宪法...", {"current_phase": "prewrite", "total_chapters": state["num_chapters"]})
    pre_provider, pre_model = get_model_for_stage(state["strategy"], "architect")
    prewrite = state["prewrite_agent"].run(state["novel_info"], state["num_chapters"], state["target_language"], pre_provider, pre_model)
    save_prewrite_artifacts(state["novel_id"], prewrite)
    return {"prewrite": prewrite}


def node_outline(state: GenerationState) -> GenerationState:
    if state["start_chapter"] > 1:
        existing = load_outlines_from_db(state["novel_id"], state.get("novel_version_id"))
        if existing and len(existing) >= state["end_chapter"]:
            logger.info(
                "Resume: loaded %d existing outlines for novel %s (start_chapter=%s)",
                len(existing), state["novel_id"], state["start_chapter"],
            )
            progress(state, "full_outline_ready", 0, 20, "加载已有章节大纲...", {"current_phase": "outline_ready", "total_chapters": state["num_chapters"]})
            return {"full_outlines": existing}

    progress(state, "specify_plan_tasks", 0, 10, "完成规格/计划/任务分解...", {"current_phase": "prewrite", "total_chapters": state["num_chapters"]})
    out_provider, out_model = get_model_for_stage(state["strategy"], "outliner")
    full_outlines = state["outliner"].run_full_book(
        state["novel_id"],
        state["num_chapters"],
        state["prewrite"],
        state["target_language"],
        out_provider,
        out_model,
    )
    is_resume = state["start_chapter"] > 1
    save_full_outlines(state["novel_id"], full_outlines, novel_version_id=state.get("novel_version_id"), replace_all=not is_resume)
    progress(state, "full_outline_ready", 0, 20, "全书章节大纲已确定", {"current_phase": "outline_ready", "total_chapters": state["num_chapters"]})
    return {"full_outlines": full_outlines}
