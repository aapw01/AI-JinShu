"""Reviewer, revise, and rollback-rerun nodes."""
from __future__ import annotations

from typing import Any

from app.core.strategy import get_review_weights, resolve_ai_profile
from app.prompts import render_prompt
from app.services.generation.common import logger
from app.services.generation.heuristics import (
    build_review_gate,
    extract_ai_flavor,
    extract_webnovel_principles,
    normalize_progression_payload,
    normalize_reviewer_payload,
)
from app.services.generation.progress import chapter_progress, progress
from app.services.generation.state import GenerationState
from app.services.memory.progression_control import rollback_progression_range


def node_review(state: GenerationState) -> GenerationState:
    """执行 node review 相关辅助逻辑。"""
    from app.core.strategy import get_pipeline_options
    _opts = get_pipeline_options(state.get("strategy"))
    _combined_mode = _opts.get("combined_reviewer", False)
    chapter_num = state["current_chapter"]
    progress(state, "reviewer", chapter_num, chapter_progress(state, 0.55), "章节审校...", {"current_phase": "chapter_review", "total_chapters": state["num_chapters"]})
    novel_config = (state.get("novel_info") or {}).get("config")
    struct_profile = resolve_ai_profile(state["strategy"], "reviewer.structured", novel_config=novel_config)
    factual_profile = resolve_ai_profile(state["strategy"], "reviewer.factual", novel_config=novel_config)
    progression_profile = resolve_ai_profile(state["strategy"], "reviewer.progression", novel_config=novel_config)
    aesthetic_profile = resolve_ai_profile(state["strategy"], "reviewer.aesthetic", novel_config=novel_config)
    combined_profile = resolve_ai_profile(state["strategy"], "reviewer.structured", novel_config=novel_config)
    review_weights = get_review_weights(state["strategy"])
    candidates = state.get("candidate_drafts") or [{"variant": "A", "draft": state.get("draft", "")}]
    _REVIEWER_FALLBACK: dict[str, Any] = {
        "score": 0.75, "confidence": 0.3, "feedback": "审校跳过（模型输出异常）",
        "must_fix": [], "should_fix": [], "positives": [], "risks": ["reviewer_skipped"],
        "contradictions": [], "raw": {},
    }

    best = None
    for c in candidates:
        text = str(c.get("draft") or "")
        # Pre-initialize to avoid UnboundLocalError in fallback path
        struct_raw = factual_raw = progression_raw = aesthetic_raw = None
        ai_flavor_raw: dict[str, Any] = {}
        webnovel_raw: dict[str, Any] = {}
        if _combined_mode:
            struct_raw, factual_raw, progression_raw, aesthetic_raw, ai_flavor_raw, webnovel_raw = state["reviewer"].run_combined(
                text,
                chapter_num,
                state.get("context") or {},
                state["target_language"],
                state["native_style_profile"],
                combined_profile["provider"],
                combined_profile["model"],
                inference=combined_profile["inference"],
            )
            # run_combined never raises; empty defaults are still usable
        if not _combined_mode or struct_raw is None:
            # Legacy 4-call path (always used when combined_mode=False, or as last-resort fallback)
            try:
                if hasattr(state["reviewer"], "run_structured"):
                    struct_raw = state["reviewer"].run_structured(
                        text, chapter_num, state["target_language"],
                        state["native_style_profile"], struct_profile["provider"], struct_profile["model"],
                        inference=struct_profile["inference"],
                    )
                else:
                    struct_raw = state["reviewer"].run(
                        text, chapter_num, state["target_language"],
                        state["native_style_profile"], struct_profile["provider"], struct_profile["model"],
                        inference=struct_profile["inference"],
                    )
            except Exception as exc:
                logger.warning("reviewer.structured failed chapter=%s error=%s", chapter_num, exc)
                struct_raw = dict(_REVIEWER_FALLBACK)

            try:
                if hasattr(state["reviewer"], "run_factual_structured"):
                    factual_raw = state["reviewer"].run_factual_structured(
                        text, chapter_num, state.get("context") or {},
                        state["target_language"], factual_profile["provider"], factual_profile["model"],
                        inference=factual_profile["inference"],
                    )
                else:
                    factual_raw = state["reviewer"].run_factual(
                        text, chapter_num, state.get("context") or {},
                        state["target_language"], factual_profile["provider"], factual_profile["model"],
                        inference=factual_profile["inference"],
                    )
            except Exception as exc:
                logger.warning("reviewer.factual failed chapter=%s error=%s", chapter_num, exc)
                factual_raw = dict(_REVIEWER_FALLBACK)

            try:
                progression_raw = state["reviewer"].run_progression_structured(
                    text, chapter_num, state.get("context") or {},
                    state["target_language"], progression_profile["provider"], progression_profile["model"],
                    inference=progression_profile["inference"],
                )
            except Exception as exc:
                logger.warning("reviewer.progression failed chapter=%s error=%s", chapter_num, exc)
                progression_raw = dict(_REVIEWER_FALLBACK)

            try:
                if hasattr(state["reviewer"], "run_aesthetic_structured"):
                    aesthetic_raw = state["reviewer"].run_aesthetic_structured(
                        text, chapter_num, state["target_language"],
                        aesthetic_profile["provider"], aesthetic_profile["model"],
                        inference=aesthetic_profile["inference"],
                    )
                else:
                    aesthetic_raw = state["reviewer"].run_aesthetic(
                        text, chapter_num, state["target_language"],
                        aesthetic_profile["provider"], aesthetic_profile["model"],
                        inference=aesthetic_profile["inference"],
                    )
            except Exception as exc:
                logger.warning("reviewer.aesthetic failed chapter=%s error=%s", chapter_num, exc)
                aesthetic_raw = dict(_REVIEWER_FALLBACK)

        struct_pack = normalize_reviewer_payload(struct_raw, "结构审校结果")
        factual_pack = normalize_reviewer_payload(factual_raw, "事实审校结果")
        progression_pack = normalize_progression_payload(progression_raw, "推进审校结果")
        aesthetic_pack = normalize_reviewer_payload(aesthetic_raw, "审美审校结果")
        ai_flavor_pack = extract_ai_flavor({"ai_flavor": ai_flavor_raw})
        webnovel_pack = extract_webnovel_principles({"webnovel_principles": webnovel_raw})
        struct_score = float(struct_pack.get("score", 0.75))
        factual_score = float(factual_pack.get("score", 0.75))
        progression_score = float(progression_pack.get("score", 0.75))
        aesthetic_score_val = float(aesthetic_pack.get("score", 0.75))
        combined = (
            (struct_score * review_weights["structure"])
            + (factual_score * review_weights["factual"])
            + (progression_score * review_weights["progression"])
            + (aesthetic_score_val * review_weights["aesthetic"])
        )
        review_gate = build_review_gate(text, struct_pack, factual_pack, progression_pack, aesthetic_pack)
        if review_gate.get("over_correction_risk"):
            combined = max(0.0, combined - 0.02)
            logger.info(
                "over_correction_risk chapter=%s evidence_coverage=%.2f combined_adjusted=%.3f",
                chapter_num, review_gate.get("evidence_coverage", 0), combined,
            )
        item = {
            "variant": c.get("variant"),
            "draft": text,
            "combined": combined,
            "struct_score": struct_score,
            "factual_score": factual_score,
            "progression_score": progression_score,
            "aesthetic_score": aesthetic_score_val,
            "feedback": str(struct_pack.get("feedback") or ""),
            "factual_feedback": str(factual_pack.get("feedback") or ""),
            "progression_feedback": str(progression_pack.get("feedback") or ""),
            "aesthetic_feedback": str(aesthetic_pack.get("feedback") or ""),
            "contradictions": factual_pack.get("contradictions") or [],
            "duplicate_beats": progression_pack.get("duplicate_beats") or [],
            "no_new_delta": progression_pack.get("no_new_delta") or [],
            "transition_conflict": progression_pack.get("transition_conflict") or [],
            "highlights": aesthetic_pack.get("positives") or [],
            "struct_pack": struct_pack,
            "factual_pack": factual_pack,
            "progression_pack": progression_pack,
            "aesthetic_pack": aesthetic_pack,
            "ai_flavor_pack": ai_flavor_pack,
            "webnovel_pack": webnovel_pack,
            "review_gate": review_gate,
        }
        if best is None or item["combined"] > best["combined"]:
            best = item
    if best is None:
        return {"score": 0.0, "feedback": "review failed", "factual_score": 0.75, "progression_score": 0.75, "aesthetic_review_score": 0.75}
    suggestions = {
        "missing_payoff": [],
        "weak_conflict": [],
        "timeline_gap": [],
        "closure_risk": [],
        "scorecards": {
            "structure": best.get("struct_pack") or {},
            "factual": best.get("factual_pack") or {},
            "progression": best.get("progression_pack") or {},
            "aesthetic": best.get("aesthetic_pack") or {},
            "ai_flavor": best.get("ai_flavor_pack") or {},
            "webnovel_principles": best.get("webnovel_pack") or {},
        },
        "review_gate": best.get("review_gate") or {},
    }
    for c_item in (best.get("contradictions") or [])[:8]:
        txt = str(c_item).strip()
        if txt:
            suggestions["timeline_gap"].append(txt[:180])
    factual_fb = str(best.get("factual_feedback") or "")
    if "伏笔" in factual_fb or "回收" in factual_fb:
        suggestions["missing_payoff"].append(factual_fb[:180])
        suggestions["closure_risk"].append("存在伏笔回收风险")
    progression_fb = str(best.get("progression_feedback") or "")
    if progression_fb:
        suggestions["weak_conflict"].append(progression_fb[:180])
    duplicate_beats = [str(x) for x in (best.get("duplicate_beats") or []) if str(x).strip()]
    no_new_delta = [str(x) for x in (best.get("no_new_delta") or []) if str(x).strip()]
    transition_conflict = [str(x) for x in (best.get("transition_conflict") or []) if str(x).strip()]
    if duplicate_beats:
        suggestions["weak_conflict"].extend(duplicate_beats[:2])
    if no_new_delta:
        suggestions["missing_payoff"].extend(no_new_delta[:2])
    if transition_conflict:
        suggestions["timeline_gap"].extend(transition_conflict[:2])
    aesthetic_fb = str(best.get("aesthetic_feedback") or "")
    if any(k in aesthetic_fb for k in ["节奏", "平", "张力不足", "冲突弱"]):
        suggestions["weak_conflict"].append(aesthetic_fb[:180])
    for issue in ((best.get("review_gate") or {}).get("validated_issues") or [])[:2]:
        cat = str(issue.get("category") or "")
        claim = str(issue.get("claim") or "")
        if not claim:
            continue
        if cat in {"timeline", "continuity"}:
            suggestions["timeline_gap"].append(claim[:180])
        elif cat in {"closure", "payoff"}:
            suggestions["missing_payoff"].append(claim[:180])
        else:
            suggestions["weak_conflict"].append(claim[:180])
    combined_feedback = "\n".join(
        [
            f"[结构] {best['feedback']}",
            f"[事实] {best['factual_feedback']}",
            f"[推进] {best['progression_feedback']}",
            f"[审美] {best['aesthetic_feedback']}",
        ]
    ).strip()
    return {
        "draft": best["draft"],
        "score": best["combined"],
        "feedback": combined_feedback,
        "factual_feedback": best["factual_feedback"],
        "progression_feedback": best.get("progression_feedback"),
        "aesthetic_feedback": best["aesthetic_feedback"],
        "factual_score": best["factual_score"],
        "progression_score": best.get("progression_score"),
        "aesthetic_review_score": best["aesthetic_score"],
        "review_suggestions": suggestions,
        "review_gate": best.get("review_gate") or {},
    }


def node_revise(state: GenerationState) -> GenerationState:
    """执行 node revise 相关辅助逻辑。"""
    review_attempt = state.get("review_attempt", 0) + 1
    ctx = dict(state["context"])
    suggestions = state.get("review_suggestions") or {}
    ctx["review_feedback"] = state["feedback"]
    ctx["review_suggestions"] = suggestions
    if suggestions:
        structured = []
        for key in ["missing_payoff", "weak_conflict", "timeline_gap", "closure_risk"]:
            vals = [str(v) for v in (suggestions.get(key) or []) if str(v).strip()]
            if vals:
                structured.append(f"{key}: " + "；".join(vals[:2]))
        gate = suggestions.get("review_gate") or {}
        validated = [
            str(x.get("claim") or "")
            for x in (gate.get("validated_issues") or [])
            if isinstance(x, dict) and str(x.get("claim") or "").strip()
        ]
        if validated:
            structured.append("validated_issues: " + "；".join(validated[:2]))
        if gate.get("over_correction_risk"):
            structured.append("guardrail: 低证据批评较多，保持主线与结构，不要大改无关段落。")
        if structured:
            ctx["review_feedback"] = f"{ctx['review_feedback']}\n[结构化修正]\n" + "\n".join(structured)
    return {"review_attempt": review_attempt, "context": ctx}


def node_rollback_rerun(state: GenerationState) -> GenerationState:
    """执行 node rollback rerun 相关辅助逻辑。"""
    chapter_num = state["current_chapter"]
    snap = state.get("chapter_token_snapshot", {})
    ctx = dict(state["context"])
    suggestions = state.get("review_suggestions") or {}
    ctx["review_feedback"] = render_prompt(
        "review_feedback_force_rewrite",
        feedback=(state.get("feedback", "") or ""),
    ).strip()
    if suggestions:
        ctx["review_suggestions"] = suggestions
    progress(state, "rollback_rerun", chapter_num, chapter_progress(state, 0.60), f"第{chapter_num}章审校未通过，回滚并重跑一次...", {"current_phase": "rollback_rerun", "total_chapters": state["num_chapters"]})
    rollback_progression_range(
        novel_id=state["novel_id"],
        novel_version_id=state.get("novel_version_id"),
        from_chapter=chapter_num,
        manager=state.get("progression_mgr"),
    )
    return {
        "rerun_count": state.get("rerun_count", 0) + 1,
        "review_attempt": 0,
        "context": ctx,
        "total_input_tokens": snap.get("input", state["total_input_tokens"]),
        "total_output_tokens": snap.get("output", state["total_output_tokens"]),
    }
