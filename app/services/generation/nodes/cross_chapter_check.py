"""Post-write cross-chapter consistency check node."""
from __future__ import annotations

import logging

from app.core.strategy import get_inference_for_stage, get_model_for_stage
from app.services.generation.consistency import (
    _get_dead_characters,
    extract_unknown_characters,
)
from app.services.generation.progress import chapter_progress, progress
from app.services.generation.state import GenerationState

logger = logging.getLogger("app.services.generation")


def node_cross_chapter_check(state: GenerationState) -> dict:
    """Compare written draft against story memory to detect cross-chapter contradictions."""
    chapter_num = state["current_chapter"]
    draft = state.get("draft") or ""
    if not draft.strip():
        return {}

    from app.core.strategy import get_pipeline_options as _get_pipeline_options
    if not _get_pipeline_options(state.get("strategy")).get("enable_cross_chapter_check", True):
        return {}

    progress(
        state, "cross_chapter_check", chapter_num,
        chapter_progress(state, 0.58),
        "跨章一致性比对...",
        {"current_phase": "chapter_review", "total_chapters": state["num_chapters"]},
    )

    db_ctx = state["context"]
    summaries = db_ctx.get("summaries") or []
    full_recent_summaries = db_ctx.get("full_recent_summaries") or summaries
    char_states = db_ctx.get("character_states") or []
    prewrite = state.get("prewrite") or {}

    contradictions: list[dict] = []
    suggestions = dict(state.get("review_suggestions") or {})

    # --- Rule-based: dead character detection ---
    dead_chars = _get_dead_characters({"character_states": char_states})
    for char in dead_chars:
        if char and char in draft:
            contradictions.append({
                "category": "character",
                "severity": "must_fix",
                "claim": f"角色「{char}」已标记为死亡，但出现在本章正文中",
                "evidence": char,
                "confidence": 0.90,
            })

    # --- LLM: cross-chapter contradiction check (only when prior summaries exist) ---
    if full_recent_summaries and len(full_recent_summaries) >= 2:
        try:
            r_provider, r_model = get_model_for_stage(state["strategy"], "reviewer")
            factual_inference = get_inference_for_stage(state["strategy"], "reviewer.factual")
            cross_result = state["reviewer"].run_cross_chapter_check(
                draft=draft,
                chapter_num=chapter_num,
                recent_summaries=full_recent_summaries[-5:],
                char_states=char_states,
                target_language=state["target_language"],
                provider=r_provider,
                model=r_model,
                inference=factual_inference,
            )
            for c in (cross_result.get("contradictions") or [])[:6]:
                if isinstance(c, dict) and float(c.get("confidence", 0)) >= 0.65:
                    contradictions.append(c)
        except Exception as exc:
            logger.warning("cross_chapter_check LLM failed chapter=%s error=%s", chapter_num, exc)

    # --- LLM: unknown character check (only when roster is non-empty) ---
    unknown_chars = extract_unknown_characters(draft, prewrite)
    roster = list((state.get("prewrite") or {}).get("specification", {}).get("characters") or [])
    roster_names = [
        str(c.get("name") or "") for c in roster if isinstance(c, dict) and c.get("name")
    ]
    if unknown_chars and roster_names:
        try:
            r_provider, r_model = get_model_for_stage(state["strategy"], "reviewer")
            char_result = state["reviewer"].run_unknown_character_check(
                draft=draft,
                chapter_num=chapter_num,
                unknown_names=list(unknown_chars),
                roster=roster_names,
                recent_summaries=full_recent_summaries[-3:],
                target_language=state["target_language"],
                provider=r_provider,
                model=r_model,
            )
            for verdict in (char_result.get("verdicts") or []):
                name = str(verdict.get("name") or "")
                result_type = str(verdict.get("result") or "")
                confidence = float(verdict.get("confidence", 0))
                if result_type == "unreasonable" and confidence >= 0.65:
                    contradictions.append({
                        "category": "character",
                        "severity": "must_fix",
                        "claim": f"角色「{name}」未在角色表中，且其出现不符合剧情逻辑：{verdict.get('reason', '')}",
                        "evidence": str(verdict.get("evidence") or name),
                        "confidence": confidence,
                    })
                elif result_type == "new_reasonable":
                    suggestions.setdefault("new_characters_to_register", []).append({
                        "name": name,
                        "reason": verdict.get("reason", ""),
                        "chapter_introduced": chapter_num,
                    })
        except Exception as exc:
            logger.warning("unknown_character_check failed chapter=%s error=%s", chapter_num, exc)

    if not contradictions:
        return {}

    # Merge contradictions into review_suggestions
    existing_gaps = list(suggestions.get("timeline_gap") or [])
    for c in contradictions:
        claim = str(c.get("claim") or "")
        if claim:
            existing_gaps.append(f"[跨章矛盾] {claim}")
    suggestions["timeline_gap"] = existing_gaps[:8]
    suggestions["cross_chapter_contradictions"] = contradictions[:6]

    # Force rewrite decision if any must_fix
    has_must_fix = any(c.get("severity") == "must_fix" for c in contradictions)
    current_gate = dict(state.get("review_gate") or {})
    if has_must_fix:
        current_gate["decision"] = "rewrite"

    return {
        "review_suggestions": suggestions,
        "review_gate": current_gate,
    }
