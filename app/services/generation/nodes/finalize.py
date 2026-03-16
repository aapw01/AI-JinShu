"""Finalize node — polishes draft, persists chapter, and commits all artifacts."""
from __future__ import annotations

from sqlalchemy import select

from app.core.constants import DEFAULT_CHAPTER_WORD_COUNT
from app.core.database import SessionLocal
from app.core.i18n import evaluate_language_quality
from app.core.llm_usage import snapshot_usage
from app.core.strategy import get_model_for_stage
from app.models.novel import ChapterVersion
from app.prompts import render_prompt
from app.services.generation.chapter_commit import write_longform_artifacts
from app.services.generation.character_profiles import update_character_profiles_incremental
from app.services.generation.common import (
    MAX_RETRIES,
    REVIEW_SCORE_THRESHOLD,
    generate_chapter_summary,
    normalize_chapter_content,
    resolve_chapter_title,
    update_character_states_from_content,
)
from app.services.generation.contracts import OutputContractError
from app.services.generation.heuristics import aesthetic_score, chapter_progress_signal
from app.services.generation.policies import PacingController, PacingInput
from app.services.generation.progress import chapter_progress, persist_resume_runtime_state, progress
from app.services.generation.state import GenerationState


def node_finalize(state: GenerationState) -> GenerationState:
    chapter_num = state["current_chapter"]
    progress(state, "finalizer", chapter_num, chapter_progress(state, 0.70), "定稿...", {"current_phase": "chapter_finalizing", "total_chapters": state["num_chapters"]})
    f_provider, f_model = get_model_for_stage(state["strategy"], "finalizer")
    base_feedback = str(state.get("feedback") or "")
    format_guardrail = (
        "【输出格式纠偏】上一次输出包含说明性前言或标题污染。\n"
        "现在必须仅输出章节正文，不要任何解释/总结/标题/Markdown；"
        '禁止出现"以下是根据反馈""重点解决如下问题"等语句。'
    )
    final_content = ""
    feedback_for_attempt = base_feedback
    for attempt in range(2):
        try:
            final_content = state["finalizer"].run(
                state["draft"],
                feedback_for_attempt,
                state["target_language"],
                f_provider,
                f_model,
                DEFAULT_CHAPTER_WORD_COUNT,
            ).strip()
            break
        except OutputContractError as exc:
            if exc.code != "MODEL_OUTPUT_POLICY_VIOLATION" or attempt >= 1:
                raise
            from app.services.generation.common import logger
            logger.warning(
                "finalizer contract violation chapter=%s attempt=%s applying format guardrail",
                chapter_num,
                attempt + 1,
            )
            feedback_for_attempt = (
                f"{base_feedback}\n\n{format_guardrail}" if base_feedback else format_guardrail
            )
    extracted_facts = state["fact_extractor"].run(
        chapter_num=chapter_num,
        content=final_content,
        outline=state.get("outline") or {},
        language=state["target_language"],
        provider=f_provider,
        model=f_model,
    )
    final_content = normalize_chapter_content(final_content)
    language_score, language_report = evaluate_language_quality(final_content, state["target_language"])
    aesthetic_score_val = aesthetic_score(final_content)
    chapter_title = resolve_chapter_title(
        chapter_num=chapter_num,
        title=(state.get("outline") or {}).get("title"),
        outline=state.get("outline") or {},
        content=final_content,
    )

    db = SessionLocal()
    try:
        progress(state, "memory_update", chapter_num, chapter_progress(state, 0.85), "生成摘要 & 更新记忆...", {"current_phase": "memory_update", "total_chapters": state["num_chapters"]})
        summary_text = generate_chapter_summary(final_content, state["outline"], chapter_num, state["target_language"], state["strategy"])
        factual_score = float(state.get("factual_score", 0.0) or 0.0)
        reviewer_aesthetic = float(state.get("aesthetic_review_score", 0.0) or 0.0)
        quality_passed = bool(
            state.get("score", 0.0) >= REVIEW_SCORE_THRESHOLD
            and factual_score >= 0.65
            and language_score >= 0.6
            and reviewer_aesthetic >= 0.6
        )
        state["summary_mgr"].add_summary(
            state["novel_id"],
            state.get("novel_version_id"),
            chapter_num,
            summary_text,
            db=db,
        )
        update_character_states_from_content(
            state["novel_id"],
            chapter_num,
            final_content,
            state["prewrite"],
            state["char_mgr"],
            state["target_language"],
            state["strategy"],
            db=db,
            novel_version_id=state.get("novel_version_id"),
        )
        update_character_profiles_incremental(
            db=db,
            novel_id=state["novel_id"],
            novel_version_id=state.get("novel_version_id"),
            chapter_num=chapter_num,
            content=final_content,
            prewrite=state["prewrite"],
            extracted_facts=extracted_facts,
            target_language=state["target_language"],
            strategy=state["strategy"],
        )
        existing_stmt = select(ChapterVersion).where(
            ChapterVersion.novel_version_id == state.get("novel_version_id"),
            ChapterVersion.chapter_num == chapter_num,
        )
        existing = db.execute(existing_stmt).scalar_one_or_none()
        revision_count = state.get("review_attempt", 0) + 1 + (state.get("rerun_count", 0) * (MAX_RETRIES + 1))
        payload = {
            "title": chapter_title,
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
            db.add(
                ChapterVersion(
                    novel_version_id=state.get("novel_version_id"),
                    chapter_num=chapter_num,
                    **payload,
                )
            )
        write_longform_artifacts(
            state={**state, "outline": {**(state.get("outline") or {}), "title": chapter_title}},
            chapter_num=chapter_num,
            summary_text=summary_text,
            final_content=final_content,
            language_score=language_score,
            aesthetic_score_val=aesthetic_score_val,
            revision_count=revision_count,
            extracted_facts=extracted_facts,
            db=db,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    usage = snapshot_usage()
    total_input_tokens = int(usage.get("input_tokens") or state["total_input_tokens"] or 0)
    total_output_tokens = int(usage.get("output_tokens") or state["total_output_tokens"] or 0)
    estimated_cost = round((total_input_tokens / 1000) * 0.0015 + (total_output_tokens / 1000) * 0.002, 6)
    factual_score = float(state.get("factual_score", 0.0) or 0.0)
    progress_sig = chapter_progress_signal(
        outline=state.get("outline") or {},
        summary_text=summary_text,
        final_content=final_content,
        extracted_facts=extracted_facts,
        review_score=float(state.get("score", 0.0) or 0.0),
        factual_score=factual_score,
    )
    pacing = PacingController.decide(
        PacingInput(
            phase_mode=str((state.get("closure_state") or {}).get("phase_mode") or ""),
            low_progress_streak=int(state.get("low_progress_streak") or 0),
            progress_signal=progress_sig,
        )
    )
    pacing_mode = pacing.mode
    next_streak = pacing.low_progress_streak
    decision_state = dict(state.get("decision_state") or {})
    decision_state["pacing"] = {
        "mode": pacing.mode,
        "low_progress_streak": pacing.low_progress_streak,
        "progress_signal": round(pacing.progress_signal, 4),
        "reasons": pacing.reason_codes,
    }
    decision_state["quality"] = {
        "review_score": round(float(state.get("score", 0.0) or 0.0), 4),
        "factual_score": round(float(factual_score), 4),
        "language_score": round(float(language_score), 4),
        "aesthetic_score": round(float(aesthetic_score_val), 4),
        "consistency_scorecard": state.get("consistency_scorecard") or {},
        "review_gate": state.get("review_gate") or {},
        "quality_passed": bool(
            state.get("score", 0.0) >= REVIEW_SCORE_THRESHOLD
            and factual_score >= 0.65
            and language_score >= 0.6
            and float(state.get("aesthetic_review_score", 0.0) or 0.0) >= 0.6
            and aesthetic_score_val >= 0.6
        ),
        "review_suggestions": state.get("review_suggestions") or {},
    }
    progress(
        state,
        "chapter_done",
        chapter_num,
        chapter_progress(state, 1.0),
        f"第{chapter_num}章完成",
        {
            "current_phase": "chapter_done",
            "total_chapters": max(int(state.get("book_effective_end_chapter") or 0), int(state.get("end_chapter") or chapter_num)),
            "token_usage_input": total_input_tokens,
            "token_usage_output": total_output_tokens,
            "estimated_cost": estimated_cost,
            "pacing_mode": pacing_mode,
            "low_progress_streak": next_streak,
            "progress_signal": round(progress_sig, 4),
            "decision_state": decision_state,
        },
    )
    if chapter_num < int(state.get("segment_end_chapter") or state.get("end_chapter") or chapter_num):
        persist_resume_runtime_state(
            state,
            mode="segment_running",
            next_chapter=chapter_num + 1,
            segment_start_chapter=int(state.get("segment_start_chapter") or state.get("start_chapter") or 1),
            segment_end_chapter=int(state.get("segment_end_chapter") or state.get("end_chapter") or chapter_num),
            book_effective_end_chapter=int(state.get("book_effective_end_chapter") or state.get("end_chapter") or chapter_num),
            volume_no=int(state.get("volume_no") or 1),
        )
    factual_score = float(state.get("factual_score", 0.0) or 0.0)
    reviewer_aesthetic = float(state.get("aesthetic_review_score", 0.0) or 0.0)
    quality_passed = bool(
        state.get("score", 0.0) >= REVIEW_SCORE_THRESHOLD
        and factual_score >= 0.65
        and language_score >= 0.6
        and reviewer_aesthetic >= 0.6
        and aesthetic_score_val >= 0.6
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
        if reviewer_aesthetic < 0.6 or aesthetic_score_val < 0.6:
            fail_reasons.append("爽点节奏与情绪张力不足")
        fail_reason = render_prompt(
            "post_quality_gate_fail_reason",
            review_score=f"{state.get('score', 0.0):.2f}",
            factual_score=f"{factual_score:.2f}",
            language_score=f"{language_score:.2f}",
            reviewer_aesthetic=f"{reviewer_aesthetic:.2f}",
            aesthetic_score=f"{aesthetic_score_val:.2f}",
            fail_reasons=",".join(fail_reasons),
        ).strip()
    return {
        "outline": {**(state.get("outline") or {}), "title": chapter_title},
        "total_input_tokens": total_input_tokens,
        "estimated_cost": estimated_cost,
        "total_output_tokens": total_output_tokens,
        "quality_passed": quality_passed,
        "low_progress_streak": next_streak,
        "pacing_mode": pacing_mode,
        "decision_state": decision_state,
        "feedback": fail_reason if fail_reason else state.get("feedback", ""),
    }
