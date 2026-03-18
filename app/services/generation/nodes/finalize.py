"""Finalize node — polishes draft, persists chapter, and commits all artifacts."""
from __future__ import annotations

import re

from sqlalchemy import select

from app.core.constants import DEFAULT_CHAPTER_WORD_COUNT
from app.core.database import SessionLocal
from app.core.i18n import evaluate_language_quality
from app.core.llm_usage import snapshot_usage
from app.core.strategy import get_inference_for_stage, get_model_for_stage
from app.models.novel import ChapterVersion
from app.prompts import render_prompt
from app.services.generation.chapter_commit import write_longform_artifacts
from app.services.generation.character_profiles import update_character_profiles_incremental
from app.services.generation.common import (
    MAX_RETRIES,
    REVIEW_SCORE_THRESHOLD,
    generate_chapter_summary,
    logger,
    normalize_chapter_content,
    resolve_chapter_title,
    update_character_states_from_content,
)
from app.services.generation.contracts import OutputContractError
from app.services.generation.heuristics import aesthetic_score, chapter_progress_signal
from app.services.generation.length_control import maybe_compact_chapter_length
from app.services.generation.policies import PacingController, PacingInput
from app.services.generation.progress import chapter_progress, persist_resume_runtime_state, progress
from app.services.generation.state import GenerationState
from app.services.memory.progression_control import ProgressionPromotionService

_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?；;…]+")


def _paragraph_fragmentation_metrics(content: str) -> dict[str, float]:
    paragraphs = [part.strip() for part in str(content or "").split("\n\n") if part.strip()]
    if not paragraphs:
        return {
            "paragraph_count": 0,
            "avg_paragraph_len": 0.0,
            "short_paragraph_ratio": 0.0,
            "single_sentence_ratio": 0.0,
        }
    paragraph_lengths = [len(paragraph.replace("\n", "")) for paragraph in paragraphs]
    short_count = sum(1 for length in paragraph_lengths if length < 25)
    single_sentence_count = 0
    for paragraph in paragraphs:
        sentence_count = len([part for part in _SENTENCE_SPLIT_RE.split(paragraph) if part.strip()])
        if sentence_count <= 1:
            single_sentence_count += 1
    total = len(paragraphs)
    return {
        "paragraph_count": float(total),
        "avg_paragraph_len": round(sum(paragraph_lengths) / total, 2),
        "short_paragraph_ratio": round(short_count / total, 4),
        "single_sentence_ratio": round(single_sentence_count / total, 4),
    }


def _should_compact_paragraphs(metrics: dict[str, float]) -> bool:
    paragraph_count = int(metrics.get("paragraph_count") or 0)
    avg_len = float(metrics.get("avg_paragraph_len") or 0.0)
    short_ratio = float(metrics.get("short_paragraph_ratio") or 0.0)
    single_ratio = float(metrics.get("single_sentence_ratio") or 0.0)
    if paragraph_count < 18:
        return False
    if short_ratio >= 0.3:
        return True
    if single_ratio >= 0.45:
        return True
    return avg_len > 0 and avg_len < 42


def _paragraph_compaction_feedback(metrics: dict[str, float]) -> str:
    return (
        "【段落整理】当前正文存在碎段过多问题，请在不改剧情与人物动机的前提下整理段落。\n"
        f"- 当前段落数：{int(metrics.get('paragraph_count') or 0)}\n"
        f"- 平均段长：{float(metrics.get('avg_paragraph_len') or 0.0):.1f}\n"
        f"- 超短段比例：{float(metrics.get('short_paragraph_ratio') or 0.0):.2%}\n"
        f"- 单句段比例：{float(metrics.get('single_sentence_ratio') or 0.0):.2%}\n"
        "要求：\n"
        "1. 合并没有必要拆开的超短段与连续情绪/动作碎段。\n"
        "2. 保留快节奏、对话张力和章末钩子，不要改剧情走向。\n"
        "3. 允许少量单句段强调，但不要泛滥。\n"
        "4. 不要补写新剧情，只整理段落层次。"
    )


def _is_structural_replan(state: GenerationState) -> bool:
    segment_plan = state.get("segment_plan")
    if not isinstance(segment_plan, dict):
        return False
    plan_kind = str(segment_plan.get("plan_kind") or "normal")
    return plan_kind in {"tail_rewrite", "bridge", "volume_replan"}


def _ensure_retry_write_allowed(state: GenerationState, chapter_num: int) -> None:
    retry_floor = int(state.get("retry_resume_chapter") or 0)
    if retry_floor <= 0:
        return
    if chapter_num >= retry_floor:
        return
    if _is_structural_replan(state):
        return
    raise RuntimeError(
        f"generation retry overwrite blocked: chapter={chapter_num} retry_resume_chapter={retry_floor}"
    )


def _is_quality_passed(
    *,
    review_score: float,
    factual_score: float,
    progression_score: float,
    language_score: float,
    reviewer_aesthetic: float,
    aesthetic_score_val: float,
) -> bool:
    return bool(
        review_score >= REVIEW_SCORE_THRESHOLD
        and factual_score >= 0.65
        and progression_score >= 0.62
        and language_score >= 0.6
        and reviewer_aesthetic >= 0.6
        and aesthetic_score_val >= 0.6
    )


def node_finalize(state: GenerationState) -> GenerationState:
    chapter_num = state["current_chapter"]
    progress(state, "finalizer", chapter_num, chapter_progress(state, 0.70), "定稿...", {"current_phase": "chapter_finalizing", "total_chapters": state["num_chapters"]})
    f_provider, f_model = get_model_for_stage(state["strategy"], "finalizer")
    fact_inference = get_inference_for_stage(state["strategy"], "fact_extractor")
    progression_inference = get_inference_for_stage(state["strategy"], "progression_memory")
    progression_provider, progression_model = get_model_for_stage(state["strategy"], "reviewer")
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
            logger.warning(
                "finalizer contract violation chapter=%s attempt=%s applying format guardrail",
                chapter_num,
                attempt + 1,
            )
            feedback_for_attempt = (
                f"{base_feedback}\n\n{format_guardrail}" if base_feedback else format_guardrail
            )
    final_content = normalize_chapter_content(final_content)
    fragment_metrics = _paragraph_fragmentation_metrics(final_content)
    if _should_compact_paragraphs(fragment_metrics):
        try:
            compaction_feedback = _paragraph_compaction_feedback(fragment_metrics)
            final_content = normalize_chapter_content(
                state["finalizer"].run(
                    final_content,
                    compaction_feedback,
                    state["target_language"],
                    f_provider,
                    f_model,
                    DEFAULT_CHAPTER_WORD_COUNT,
                ).strip()
            )
        except Exception:
            logger.warning("finalizer paragraph compaction skipped after failure", exc_info=True)
    final_content, length_diagnostics = maybe_compact_chapter_length(
        content=final_content,
        word_count=DEFAULT_CHAPTER_WORD_COUNT,
        compact_fn=lambda draft, feedback: state["finalizer"].run(
            draft,
            feedback,
            state["target_language"],
            f_provider,
            f_model,
            DEFAULT_CHAPTER_WORD_COUNT,
        ),
        normalize_fn=normalize_chapter_content,
    )
    if bool(length_diagnostics.get("length_compaction_attempted")):
        logger.info(
            "finalizer length compaction chapter=%s before=%s after=%s applied=%s reason=%s",
            chapter_num,
            length_diagnostics.get("word_count_before_compaction"),
            length_diagnostics.get("word_count_after_compaction"),
            length_diagnostics.get("length_compaction_applied"),
            length_diagnostics.get("length_compaction_reason"),
        )
    extracted_facts = state["fact_extractor"].run(
        chapter_num=chapter_num,
        content=final_content,
        outline=state.get("outline") or {},
        language=state["target_language"],
        provider=f_provider,
        model=f_model,
        inference=fact_inference,
    )
    language_score, language_report = evaluate_language_quality(final_content, state["target_language"])
    aesthetic_score_val = aesthetic_score(final_content)
    progression_memory_raw: dict[str, object] = {
        "advancement": {},
        "transition": {},
        "advancement_confidence": 0.0,
        "transition_confidence": 0.0,
        "validation_notes": [],
    }
    progression_promotion: dict[str, object] = {
        "decision": "promote_none",
        "promotion_score": 0.0,
        "promoted_payload": {"advancement": {}, "transition": {}},
    }
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
        progression_memory_raw = state["progression_memory_extractor"].run(
            chapter_num=chapter_num,
            content=final_content,
            outline=state.get("outline") or {},
            language=state["target_language"],
            provider=progression_provider,
            model=progression_model,
            inference=progression_inference,
        )
        progression_promotion = ProgressionPromotionService().decide(
            chapter_num=chapter_num,
            extraction=progression_memory_raw,
            outline_contract=((state.get("context") or {}).get("outline_contract") if isinstance(state.get("context"), dict) else None)
            or state.get("outline")
            or {},
            review_suggestions=state.get("review_suggestions") if isinstance(state.get("review_suggestions"), dict) else {},
            review_gate=state.get("review_gate") if isinstance(state.get("review_gate"), dict) else {},
        )
        promoted_progression = progression_promotion.get("promoted_payload")
        progression_memory = promoted_progression if isinstance(promoted_progression, dict) else {"advancement": {}, "transition": {}}
        factual_score = float(state.get("factual_score", 0.0) or 0.0)
        progression_score = float(state.get("progression_score", 0.0) or 0.0)
        reviewer_aesthetic = float(state.get("aesthetic_review_score", 0.0) or 0.0)
        quality_passed = _is_quality_passed(
            review_score=float(state.get("score", 0.0) or 0.0),
            factual_score=factual_score,
            progression_score=progression_score,
            language_score=language_score,
            reviewer_aesthetic=reviewer_aesthetic,
            aesthetic_score_val=aesthetic_score_val,
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
        _ensure_retry_write_allowed(state, chapter_num)
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
                "progression_score": progression_score,
                "aesthetic_review_score": reviewer_aesthetic,
                "chapter_advancement": progression_memory.get("advancement") or {},
                "chapter_transition": progression_memory.get("transition") or {},
                "progression_memory_raw": progression_memory_raw,
                "progression_promotion": progression_promotion,
                "word_count_before_compaction": int(length_diagnostics.get("word_count_before_compaction") or 0),
                "word_count_after_compaction": int(length_diagnostics.get("word_count_after_compaction") or 0),
                "length_compaction_attempted": bool(length_diagnostics.get("length_compaction_attempted")),
                "length_compaction_applied": bool(length_diagnostics.get("length_compaction_applied")),
                "length_compaction_reason": str(length_diagnostics.get("length_compaction_reason") or ""),
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
            progression_memory=progression_memory,
            progression_promotion=progression_promotion,
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
        "progression_score": round(float(state.get("progression_score", 0.0) or 0.0), 4),
        "language_score": round(float(language_score), 4),
        "aesthetic_score": round(float(aesthetic_score_val), 4),
        "consistency_scorecard": state.get("consistency_scorecard") or {},
        "review_gate": state.get("review_gate") or {},
        "chapter_advancement": progression_memory.get("advancement") if isinstance(progression_memory, dict) else {},
        "chapter_transition": progression_memory.get("transition") if isinstance(progression_memory, dict) else {},
        "progression_promotion": progression_promotion,
        "quality_passed": bool(
            _is_quality_passed(
                review_score=float(state.get("score", 0.0) or 0.0),
                factual_score=factual_score,
                progression_score=float(state.get("progression_score", 0.0) or 0.0),
                language_score=language_score,
                reviewer_aesthetic=float(state.get("aesthetic_review_score", 0.0) or 0.0),
                aesthetic_score_val=aesthetic_score_val,
            )
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
            retry_resume_chapter=int(state.get("retry_resume_chapter") or chapter_num + 1),
        )
    factual_score = float(state.get("factual_score", 0.0) or 0.0)
    reviewer_aesthetic = float(state.get("aesthetic_review_score", 0.0) or 0.0)
    quality_passed = _is_quality_passed(
        review_score=float(state.get("score", 0.0) or 0.0),
        factual_score=factual_score,
        progression_score=float(state.get("progression_score", 0.0) or 0.0),
        language_score=language_score,
        reviewer_aesthetic=reviewer_aesthetic,
        aesthetic_score_val=aesthetic_score_val,
    )
    fail_reason = ""
    if not quality_passed:
        fail_reasons: list[str] = []
        if state.get("score", 0.0) < REVIEW_SCORE_THRESHOLD:
            fail_reasons.append("结构推进不足")
        if factual_score < 0.65:
            fail_reasons.append("事实一致性不足")
        if float(state.get("progression_score", 0.0) or 0.0) < 0.62:
            fail_reasons.append("剧情推进重复或衔接不连续")
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
