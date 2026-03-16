"""Final full-book review node."""
from __future__ import annotations

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.strategy import get_model_for_stage
from app.models.novel import ChapterVersion
from app.services.generation.common import save_prewrite_artifacts
from app.services.generation.progress import persist_resume_runtime_state, progress, volume_no_for_chapter
from app.services.generation.state import GenerationState


def node_final_book_review(state: GenerationState) -> GenerationState:
    effective_end = int(state.get("book_effective_end_chapter") or state.get("end_chapter") or state.get("current_chapter") or 0)
    effective_total = max(int(state.get("book_target_total_chapters") or 0), effective_end)
    persist_resume_runtime_state(
        state,
        mode="book_final_review_pending",
        next_chapter=effective_end + 1,
        segment_start_chapter=int(state.get("segment_start_chapter") or state.get("start_chapter") or 1),
        segment_end_chapter=int(state.get("segment_end_chapter") or state.get("end_chapter") or effective_end),
        book_effective_end_chapter=effective_end,
        volume_no=int(state.get("volume_no") or 1),
    )
    db = SessionLocal()
    try:
        last_chapter = effective_end
        all_summaries = state["summary_mgr"].get_summaries_before(
            state["novel_id"],
            state.get("novel_version_id"),
            last_chapter + 1,
            db=db,
        )
        if all_summaries:
            chapter_payload = [{"chapter_num": s["chapter_num"], "summary": s["summary"]} for s in all_summaries]
        else:
            chapter_stmt = (
                select(ChapterVersion)
                .where(ChapterVersion.novel_version_id == state.get("novel_version_id"))
                .order_by(ChapterVersion.chapter_num)
            )
            chapter_rows = db.execute(chapter_stmt).scalars().all()
            chapter_payload = [{"chapter_num": c.chapter_num, "title": c.title, "content": (c.content or "")[:2000]} for c in chapter_rows]
    finally:
        db.close()

    progress(
        state,
        "final_book_review",
        effective_end,
        97,
        "全书终审...",
        {"current_phase": "full_book_review", "total_chapters": effective_total},
    )
    fr_provider, fr_model = get_model_for_stage(state["strategy"], "reviewer")
    final_report = state["final_reviewer"].run_full_book(chapter_payload, state["target_language"], fr_provider, fr_model)
    save_prewrite_artifacts(state["novel_id"], {"final_book_review": final_report})
    state["quality_store"].add_report(
        novel_id=state["novel_id"],
        novel_version_id=state.get("novel_version_id"),
        scope="book",
        scope_id="final",
        metrics_json=final_report if isinstance(final_report, dict) else {"raw": str(final_report)},
        verdict="pass" if float((final_report or {}).get("score", 0.0) or 0.0) >= 0.7 else "warning",
    )
    if state.get("task_id"):
        state["checkpoint_store"].save_checkpoint(
            task_id=state["task_id"],
            novel_id=state["novel_id"],
            volume_no=int(state.get("volume_no") or volume_no_for_chapter(state, effective_end)),
            chapter_num=effective_end,
            node="book_done",
            state_json={"final_report": final_report},
        )
    progress(
        state,
        "done",
        effective_end,
        100,
        "全书生成完成",
        {
            "current_phase": "completed",
            "total_chapters": effective_total,
            "token_usage_input": state["total_input_tokens"],
            "token_usage_output": state["total_output_tokens"],
            "estimated_cost": state["estimated_cost"],
            "final_report": final_report,
        },
    )
    persist_resume_runtime_state(
        state,
        mode="completed",
        next_chapter=effective_end + 1,
        segment_start_chapter=int(state.get("segment_start_chapter") or state.get("start_chapter") or 1),
        segment_end_chapter=int(state.get("segment_end_chapter") or state.get("end_chapter") or effective_end),
        book_effective_end_chapter=effective_end,
        volume_no=int(state.get("volume_no") or 1),
    )
    return {}
