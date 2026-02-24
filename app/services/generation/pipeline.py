"""Optimized generation pipeline inspired by novel-writer workflow."""
import logging
import time
from app.core.database import SessionLocal
from app.core.strategy import get_model_for_stage
from app.models.novel import Chapter, ChapterOutline, GenerationTask, Novel, NovelSpecification
from app.services.generation.agents import (
    FinalizerAgent,
    FinalReviewerAgent,
    OutlinerAgent,
    PrewritePlannerAgent,
    ReviewerAgent,
    WriterAgent,
)
from app.services.memory.character_state import CharacterStateManager
from app.services.memory.summary_manager import SummaryManager
from app.core.i18n import evaluate_language_quality, get_native_style_profile

logger = logging.getLogger(__name__)
REVIEW_SCORE_THRESHOLD = 0.7
MAX_RETRIES = 2


def _save_prewrite_artifacts(novel_id: int, prewrite: dict) -> None:
    db = SessionLocal()
    try:
        for spec_type, content in prewrite.items():
            existing = (
                db.query(NovelSpecification)
                .filter(NovelSpecification.novel_id == novel_id, NovelSpecification.spec_type == spec_type)
                .first()
            )
            if existing:
                existing.content = content
            else:
                db.add(NovelSpecification(novel_id=novel_id, spec_type=spec_type, content=content))
        db.commit()
    finally:
        db.close()


def _save_full_outlines(novel_id: int, outlines: list[dict]) -> None:
    db = SessionLocal()
    try:
        db.query(ChapterOutline).filter(ChapterOutline.novel_id == novel_id).delete()
        for o in outlines:
            db.add(
                ChapterOutline(
                    novel_id=novel_id,
                    chapter_num=o.get("chapter_num"),
                    title=o.get("title"),
                    outline=o.get("outline"),
                    metadata_={
                        "role": o.get("role"),
                        "purpose": o.get("purpose"),
                        "suspense_level": o.get("suspense_level"),
                        "foreshadowing": o.get("foreshadowing"),
                        "plot_twist_level": o.get("plot_twist_level"),
                        "hook": o.get("hook"),
                        "payoff": o.get("payoff"),
                        "mini_climax": o.get("mini_climax"),
                        "summary": o.get("summary"),
                    },
                )
            )
        db.commit()
    finally:
        db.close()


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _generate_chapter_summary(
    content: str, outline: dict, chapter_num: int, language: str, strategy: str
) -> str:
    """Generate a concise summary from the actual chapter content."""
    from app.core.llm import get_llm_with_fallback
    from app.core.strategy import get_model_for_stage

    provider, model = get_model_for_stage(strategy, "reviewer")
    llm = get_llm_with_fallback(provider, model)
    prompt = f"""Summarize this chapter in 200-400 characters. Include: key events, character state changes, new information revealed, and the chapter-end hook.

Chapter {chapter_num} content (truncated):
{content[:4000]}

Output only the summary text in {language}, no JSON or markdown."""
    try:
        resp = llm.invoke(prompt)
        return resp.content.strip()[:800]
    except Exception:
        return outline.get("summary") or f"第{chapter_num}章摘要"


def _update_character_states_from_content(
    novel_id: int,
    chapter_num: int,
    content: str,
    prewrite: dict,
    char_mgr: CharacterStateManager,
    language: str,
    strategy: str,
    db=None,
) -> None:
    """Extract and update character state changes from written chapter content."""
    from app.core.llm import get_llm_with_fallback
    from app.core.strategy import get_model_for_stage
    from app.services.generation.agents import _parse_json_response

    provider, model = get_model_for_stage(strategy, "reviewer")
    llm = get_llm_with_fallback(provider, model)
    characters = prewrite.get("specification", {}).get("characters", [])
    if not isinstance(characters, list) or not characters:
        return
    char_names = [c.get("name", "") for c in characters if isinstance(c, dict) and c.get("name")]
    if not char_names:
        return
    prompt = f"""Based on this chapter content, report any STATE CHANGES for these characters: {', '.join(char_names)}

Chapter {chapter_num} content (truncated):
{content[:3000]}

Output JSON: {{"updates": [{{"name": "角色名", "status": "alive/injured/dead/unknown", "location": "当前位置", "new_items": [], "lost_items": [], "relationship_changes": [], "key_action": "本章关键行为"}}]}}
Only include characters who actually appeared or were affected. Output pure JSON."""
    try:
        resp = llm.invoke(prompt)
        data = _parse_json_response(resp.content)
        for update in data.get("updates", []):
            if isinstance(update, dict) and update.get("name"):
                char_mgr.update_state(
                    novel_id,
                    update["name"],
                    {"chapter_num": chapter_num, **update},
                    db=db,
                )
    except Exception as e:
        logger.warning(f"Character state update from content failed: {e}")


def run_generation_pipeline(
    novel_id: int,
    num_chapters: int,
    start_chapter: int,
    progress_callback=None,
    task_id: str | None = None,
) -> None:
    """Two-phase flow: prewrite (constitution/spec/plan/tasks+full outlines) -> chapter writing."""
    progress_callback = progress_callback or (lambda *a, **k: None)
    summary_mgr = SummaryManager()
    char_mgr = CharacterStateManager()
    prewrite_agent = PrewritePlannerAgent()
    outliner = OutlinerAgent()
    writer = WriterAgent()
    reviewer = ReviewerAgent()
    finalizer = FinalizerAgent()
    final_reviewer = FinalReviewerAgent()

    db = SessionLocal()
    try:
        novel = db.query(Novel).filter(Novel.id == novel_id).first()
        if not novel:
            raise ValueError(f"Novel {novel_id} not found")
        strategy = novel.strategy or "web-novel"
        target_language = novel.target_language or "zh"
        novel_info = {
            "title": novel.title,
            "genre": novel.genre,
            "style": novel.style,
            "audience": novel.audience,
            "target_length": novel.target_length,
            "writing_method": novel.writing_method,
            "user_idea": novel.user_idea,
        }
        native_style_profile = novel.native_style_profile or get_native_style_profile(target_language)
    finally:
        db.close()

    # Phase A: novel-writer style prewrite stage.
    total_input_tokens = 0
    total_output_tokens = 0
    estimated_cost = 0.0

    progress_callback("constitution", 0, 2, "生成创作宪法...", {"current_phase": "prewrite", "total_chapters": num_chapters})
    pre_provider, pre_model = get_model_for_stage(strategy, "architect")
    prewrite = prewrite_agent.run(novel_info, num_chapters, target_language, pre_provider, pre_model)
    _save_prewrite_artifacts(novel_id, prewrite)

    progress_callback("specify_plan_tasks", 0, 10, "完成规格/计划/任务分解...", {"current_phase": "prewrite", "total_chapters": num_chapters})
    out_provider, out_model = get_model_for_stage(strategy, "outliner")
    full_outlines = outliner.run_full_book(novel_id, num_chapters, prewrite, target_language, out_provider, out_model)
    _save_full_outlines(novel_id, full_outlines)
    progress_callback("full_outline_ready", 0, 20, "全书章节大纲已确定", {"current_phase": "outline_ready", "total_chapters": num_chapters})

    # Optional gate: wait for user outline confirmation.
    if task_id:
        db = SessionLocal()
        try:
            novel_row = db.query(Novel).filter(Novel.id == novel_id).first()
            require_confirm = bool((novel_row.config or {}).get("require_outline_confirmation"))
            if require_confirm:
                gt = db.query(GenerationTask).filter(GenerationTask.task_id == task_id).first()
                if gt:
                    gt.status = "awaiting_outline_confirmation"
                    gt.current_phase = "outline_ready"
                    gt.message = "章节大纲已生成，等待确认"
                    novel_row.status = "awaiting_outline_confirmation"
                    db.commit()
                    progress_callback(
                        "outline_waiting_confirmation",
                        0,
                        20,
                        "等待用户确认大纲后继续生成",
                        {"status": "awaiting_outline_confirmation", "current_phase": "outline_ready", "total_chapters": num_chapters},
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

    # Phase B: chapter loop.
    from app.services.memory.context import build_chapter_context
    from app.services.generation.consistency import check_consistency, inject_consistency_context

    total = max(num_chapters, 1)
    for i in range(num_chapters):
        chapter_num = start_chapter + i
        base_pct = 20 + (i / total) * 70
        span = 70 / total
        outline = full_outlines[i] if i < len(full_outlines) else {"chapter_num": chapter_num, "title": f"第{chapter_num}章", "outline": ""}

        db = SessionLocal()
        try:
            progress_callback("context", chapter_num, base_pct + span * 0.10, "加载分层上下文...", {"current_phase": "chapter_writing", "total_chapters": num_chapters})
            ctx = build_chapter_context(novel_id, chapter_num, prewrite, outline, db=db)
            ctx["prewrite"] = prewrite
            ctx["chapter_outline"] = outline
            ctx["character_states"] = char_mgr.get_states(novel_id, chapter_num, db=db)
            ctx["summaries"] = summary_mgr.get_summaries_before(novel_id, chapter_num, db=db)

            progress_callback("consistency", chapter_num, base_pct + span * 0.15, "一致性检查...", {"current_phase": "consistency_check", "total_chapters": num_chapters})
            report = check_consistency(novel_id, chapter_num, outline, ctx, prewrite)
            if not report.passed:
                logger.error(f"Consistency BLOCKED ch{chapter_num}: {report.summary()}")
                existing = (
                    db.query(Chapter)
                    .filter(Chapter.novel_id == novel_id, Chapter.chapter_num == chapter_num)
                    .first()
                )
                payload = {
                    "title": outline.get("title", f"第{chapter_num}章"),
                    "content": "",
                    "summary": "",
                    "status": "consistency_blocked",
                    "metadata_": {"consistency_report": report.summary(), "consistency_blocked": True},
                }
                if existing:
                    for k, v in payload.items():
                        setattr(existing, k, v)
                else:
                    db.add(Chapter(novel_id=novel_id, chapter_num=chapter_num, **payload))
                db.commit()
                progress_callback(
                    "chapter_blocked", chapter_num, base_pct + span,
                    f"第{chapter_num}章因一致性检查未通过已跳过",
                    {"current_phase": "chapter_blocked", "total_chapters": num_chapters},
                )
                continue

            ctx = inject_consistency_context(ctx, report)

            # Write -> Review -> Revise loop
            draft = ""
            feedback = ""
            score = 0.0
            revision_count = 0
            for attempt in range(MAX_RETRIES + 1):
                revision_count = attempt + 1
                progress_callback("writer", chapter_num, base_pct + span * 0.35, f"写作第{chapter_num}章（尝试{revision_count}）...", {"current_phase": "chapter_writing", "total_chapters": num_chapters})
                w_provider, w_model = get_model_for_stage(strategy, "writer")
                draft = writer.run(
                    novel_id, chapter_num, outline, ctx,
                    target_language, native_style_profile, w_provider, w_model,
                )
                total_output_tokens += _estimate_tokens(draft)

                progress_callback("reviewer", chapter_num, base_pct + span * 0.55, "章节审校...", {"current_phase": "chapter_review", "total_chapters": num_chapters})
                r_provider, r_model = get_model_for_stage(strategy, "reviewer")
                score, feedback = reviewer.run(
                    draft, chapter_num, target_language, native_style_profile, r_provider, r_model,
                )
                if score >= REVIEW_SCORE_THRESHOLD:
                    break
                ctx["review_feedback"] = feedback

            # Finalize
            progress_callback("finalizer", chapter_num, base_pct + span * 0.70, "定稿...", {"current_phase": "chapter_finalizing", "total_chapters": num_chapters})
            f_provider, f_model = get_model_for_stage(strategy, "finalizer")
            final_content = finalizer.run(draft, feedback, target_language, f_provider, f_model)
            language_score, language_report = evaluate_language_quality(final_content, target_language)

            # Post-write: generate real summary from content & update character states
            progress_callback("memory_update", chapter_num, base_pct + span * 0.85, "生成摘要 & 更新记忆...", {"current_phase": "memory_update", "total_chapters": num_chapters})
            summary_text = _generate_chapter_summary(final_content, outline, chapter_num, target_language, strategy)
            summary_mgr.add_summary(novel_id, chapter_num, summary_text, db=db)
            _update_character_states_from_content(
                novel_id, chapter_num, final_content, prewrite, char_mgr, target_language, strategy, db=db,
            )

            # Save chapter
            existing = (
                db.query(Chapter)
                .filter(Chapter.novel_id == novel_id, Chapter.chapter_num == chapter_num)
                .first()
            )
            payload = {
                "title": outline.get("title", f"第{chapter_num}章"),
                "content": final_content,
                "summary": summary_text,
                "review_score": score,
                "status": "completed",
                "language_quality_score": language_score,
                "language_quality_report": language_report,
                "metadata_": {
                    "language_quality_report": language_report,
                    "consistency_report": report.summary(),
                    "revision_count": revision_count,
                    "context_budget_used": ctx.get("budget_used", 0),
                },
            }
            if existing:
                for k, v in payload.items():
                    setattr(existing, k, v)
            else:
                db.add(Chapter(novel_id=novel_id, chapter_num=chapter_num, **payload))
            db.commit()
        finally:
            db.close()

        total_input_tokens += _estimate_tokens(str(outline) + str(ctx) + (feedback or ""))
        estimated_cost = round((total_input_tokens / 1000) * 0.0015 + (total_output_tokens / 1000) * 0.002, 6)
        progress_callback(
            "chapter_done", chapter_num, base_pct + span,
            f"第{chapter_num}章完成",
            {
                "current_phase": "chapter_done",
                "total_chapters": num_chapters,
                "token_usage_input": total_input_tokens,
                "token_usage_output": total_output_tokens,
                "estimated_cost": estimated_cost,
            },
        )

    # Phase C: whole-book review gate (use summaries instead of full content for long novels).
    db = SessionLocal()
    try:
        last_chapter = start_chapter + num_chapters - 1
        all_summaries = summary_mgr.get_summaries_before(novel_id, last_chapter + 1, db=db)
        if all_summaries:
            chapter_payload = [{"chapter_num": s["chapter_num"], "summary": s["summary"]} for s in all_summaries]
        else:
            chapter_rows = (
                db.query(Chapter)
                .filter(Chapter.novel_id == novel_id)
                .order_by(Chapter.chapter_num)
                .all()
            )
            chapter_payload = [
                {"chapter_num": c.chapter_num, "title": c.title, "content": (c.content or "")[:2000]}
                for c in chapter_rows
            ]
    finally:
        db.close()

    progress_callback(
        "final_book_review",
        num_chapters,
        97,
        "全书终审...",
        {"current_phase": "full_book_review", "total_chapters": num_chapters},
    )
    fr_provider, fr_model = get_model_for_stage(strategy, "reviewer")
    final_report = final_reviewer.run_full_book(chapter_payload, target_language, fr_provider, fr_model)
    _save_prewrite_artifacts(novel_id, {"final_book_review": final_report})
    progress_callback(
        "done",
        num_chapters,
        100,
        "全书生成完成",
        {
            "current_phase": "completed",
            "total_chapters": num_chapters,
            "token_usage_input": total_input_tokens,
            "token_usage_output": total_output_tokens,
            "estimated_cost": estimated_cost,
            "final_report": final_report,
        },
    )
