"""LangGraph orchestration for novel generation.

Graph is compiled once at module level (singleton) to avoid per-invocation overhead.
All shared helpers are imported from common.py — no circular dependency with pipeline.py.
"""
import time
from typing import Any, Callable, TypedDict
import re

from langgraph.graph import END, StateGraph
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.i18n import evaluate_language_quality, get_native_style_profile
from app.core.logging_config import log_event
from app.core.strategy import get_model_for_stage
from app.core.llm_usage import snapshot_usage
from app.prompts import render_prompt
from app.models.novel import ChapterVersion, GenerationTask, Novel, GenerationCheckpoint, NovelFeedback, StoryForeshadow
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
    generate_chapter_summary,
    logger,
    resolve_chapter_title,
    save_full_outlines,
    save_prewrite_artifacts,
    update_character_states_from_content,
)
from app.services.generation.contracts import OutputContractError
from app.services.generation.character_profiles import update_character_profiles_incremental
from app.services.generation.policies import (
    ClosurePolicyEngine,
    ClosurePolicyInput,
    PacingController,
    PacingInput,
)
from app.services.memory.character_state import CharacterStateManager
from app.services.memory.summary_manager import SummaryManager
from app.services.memory.story_bible import StoryBibleStore, CheckpointStore, QualityReportStore


# ---------------------------------------------------------------------------
# State schema
# ---------------------------------------------------------------------------

class GenerationState(TypedDict, total=False):
    novel_id: int
    novel_version_id: int
    num_chapters: int
    target_chapters: int
    min_total_chapters: int
    max_total_chapters: int
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
    decision_state: dict[str, Any]
    closure_state: dict[str, Any]
    consistency_soft_fail: bool
    tail_rewrite_attempts: int
    bridge_attempts: int
    low_progress_streak: int
    pacing_mode: str
    review_suggestions: dict[str, Any]
    consistency_scorecard: dict[str, Any]
    review_gate: dict[str, Any]


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
    payload = dict(meta or {})
    payload.setdefault("task_id", state.get("task_id"))
    payload.setdefault("novel_id", state.get("novel_id"))
    usage = snapshot_usage()
    usage_in = int(usage.get("input_tokens") or 0)
    usage_out = int(usage.get("output_tokens") or 0)
    payload.setdefault("token_usage_input", usage_in or int(state.get("total_input_tokens") or 0))
    payload.setdefault("token_usage_output", usage_out or int(state.get("total_output_tokens") or 0))
    if payload.get("estimated_cost") is None:
        input_tokens = int(payload.get("token_usage_input") or 0)
        output_tokens = int(payload.get("token_usage_output") or 0)
        payload["estimated_cost"] = round((input_tokens / 1000) * 0.0015 + (output_tokens / 1000) * 0.002, 6)
    logger.info(
        "PIPELINE progress task_id=%s novel_id=%s step=%s chapter=%s pct=%.2f msg=%s meta=%s",
        payload.get("task_id"),
        payload.get("novel_id"),
        step,
        chapter,
        pct,
        msg,
        payload,
    )
    pct = max(pct, float(state.get("_last_reported_progress") or 0.0))
    state["_last_reported_progress"] = pct
    if cb:
        if chapter > 0:
            payload.setdefault("volume_no", _volume_no_for_chapter(state, chapter))
            payload.setdefault("volume_size", int(state.get("volume_size") or 30))
        cb(step, chapter, pct, msg, payload)


def _chapter_progress(state: GenerationState, phase_ratio: float) -> float:
    total = max(state["num_chapters"], 1)
    idx = max(0, state["current_chapter"] - state["start_chapter"])
    base_pct = 20 + (idx / total) * 70
    span = 70 / total
    raw = base_pct + span * phase_ratio
    prev = float(state.get("_last_reported_progress") or 0.0)
    return max(raw, prev)


def _is_volume_start(state: GenerationState, chapter: int) -> bool:
    volume_size = max(int(state.get("volume_size") or 30), 1)
    start = state.get("start_chapter") or 1
    return (chapter - start) % volume_size == 0


def _closure_phase_mode(remaining_ratio: float) -> str:
    if remaining_ratio > 0.35:
        return "expand"
    if remaining_ratio > 0.15:
        return "converge"
    if remaining_ratio > 0.05:
        return "closing"
    return "finale"


def _build_closure_state(state: GenerationState) -> dict[str, Any]:
    chapter_num = int(state.get("current_chapter") or 1)
    start_chapter = int(state.get("start_chapter") or 1)
    end_chapter = int(state.get("end_chapter") or chapter_num)
    target_chapters = int(state.get("target_chapters") or state.get("num_chapters") or 1)
    min_total = int(state.get("min_total_chapters") or target_chapters)
    max_total = int(state.get("max_total_chapters") or target_chapters)
    generated = max(0, chapter_num - start_chapter)
    remaining = max(0, end_chapter - chapter_num + 1)
    remaining_ratio = remaining / max(target_chapters, 1)
    phase_mode = _closure_phase_mode(remaining_ratio)

    constraints = state["bible_store"].get_chapter_constraints(
        state["novel_id"],
        chapter_num,
        novel_version_id=state.get("novel_version_id"),
    )
    unresolved_foreshadows = constraints.get("unresolved_foreshadows") or []
    resolved_foreshadows = 0
    total_foreshadows = 0
    db = SessionLocal()
    try:
        fs_rows = db.execute(
            select(StoryForeshadow).where(
                StoryForeshadow.novel_id == state["novel_id"],
                StoryForeshadow.planted_chapter <= end_chapter,
            )
        ).scalars().all()
        total_foreshadows = len(fs_rows)
        resolved_foreshadows = len([f for f in fs_rows if (f.state or "") == "resolved"])
    finally:
        db.close()

    plotlines = (((state.get("prewrite") or {}).get("specification") or {}).get("plotlines") or [])
    open_plotlines: list[dict[str, Any]] = []
    total_plotlines = 0
    resolved_plotlines = 0
    if isinstance(plotlines, list):
        for item in plotlines[:80]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("id") or "").strip()
            if not name:
                continue
            try:
                plot_end = int(item.get("end") or target_chapters)
            except Exception:
                plot_end = target_chapters
            total_plotlines += 1
            if generated >= plot_end:
                resolved_plotlines += 1
            if plot_end <= end_chapter and generated < plot_end:
                open_plotlines.append(
                    {
                        "id": str(item.get("id") or name),
                        "name": name[:120],
                        "expected_end": plot_end,
                    }
                )

    must_close_items = [
        {
            "type": "foreshadow",
            "id": str(x.get("foreshadow_id") or ""),
            "title": str(x.get("title") or "")[:160],
            "introduced_chapter": int(x.get("planted_chapter") or 0),
        }
        for x in unresolved_foreshadows[:30]
        if x
    ] + [
        {
            "type": "plotline",
            "id": str(x.get("id") or ""),
            "title": str(x.get("name") or "")[:160],
            "introduced_chapter": 0,
        }
        for x in open_plotlines[:20]
    ]
    unresolved_count = len(must_close_items)
    total_close_units = max(1, total_foreshadows + total_plotlines)
    resolved_units = min(total_close_units, resolved_foreshadows + resolved_plotlines)
    must_close_coverage = max(0.0, min(1.0, resolved_units / total_close_units))
    closure_score = max(0.0, min(1.0, (must_close_coverage * 0.75) + (0.25 if unresolved_count == 0 else 0.0)))
    rewrite_attempts = int(state.get("tail_rewrite_attempts") or 0)
    bridge_attempts = int(state.get("bridge_attempts") or 0)
    closure_threshold = float(((state.get("novel_info") or {}).get("closure_threshold")) or 0.95)
    bridge_budget_total = max(0, max_total - target_chapters)
    bridge_budget_left = max(0, bridge_budget_total - bridge_attempts)
    decision = ClosurePolicyEngine.decide(
        ClosurePolicyInput(
            generated_chapters=generated,
            target_chapters=target_chapters,
            min_total_chapters=min_total,
            max_total_chapters=max_total,
            remaining_chapters=remaining,
            remaining_ratio=remaining_ratio,
            phase_mode=phase_mode,
            unresolved_count=unresolved_count,
            must_close_coverage=must_close_coverage,
            closure_threshold=closure_threshold,
            tail_rewrite_attempts=rewrite_attempts,
            bridge_attempts=bridge_attempts,
        )
    )
    action = decision.action

    return {
        "generated_chapters": generated,
        "target_chapters": target_chapters,
        "min_total_chapters": min_total,
        "max_total_chapters": max_total,
        "remaining_chapters": remaining,
        "remaining_ratio": round(remaining_ratio, 4),
        "phase_mode": phase_mode,
        "unresolved_count": unresolved_count,
        "closure_score": round(closure_score, 4),
        "must_close_coverage": round(must_close_coverage, 4),
        "closure_threshold": round(closure_threshold, 4),
        "total_foreshadows": total_foreshadows,
        "resolved_foreshadows": resolved_foreshadows,
        "total_plotlines": total_plotlines,
        "resolved_plotlines": resolved_plotlines,
        "tail_rewrite_attempts": rewrite_attempts,
        "bridge_attempts": bridge_attempts,
        "bridge_budget_total": int(decision.next_limits.get("bridge_budget_total") or bridge_budget_total),
        "bridge_budget_left": int(decision.next_limits.get("bridge_budget_left") or bridge_budget_left),
        "reason_codes": decision.reason_codes,
        "confidence": round(float(decision.confidence), 4),
        "next_limits": decision.next_limits,
        "must_close_items": must_close_items[:20],
        "action": action,
    }


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


def _chapter_progress_signal(
    outline: dict[str, Any],
    summary_text: str,
    final_content: str,
    extracted_facts: dict[str, Any] | None,
    review_score: float,
    factual_score: float,
) -> float:
    """Heuristic chapter progression signal in 0-1."""
    events = extracted_facts.get("events") if isinstance(extracted_facts, dict) else []
    events_count = len(events or [])
    summary_len = len((summary_text or "").strip())
    payoff = str(outline.get("payoff") or "").strip()
    purpose = str(outline.get("purpose") or "").strip()
    mini_climax = str(outline.get("mini_climax") or "").strip().lower()
    suspense = str(outline.get("suspense_level") or "").strip()
    has_conflict_word = any(k in final_content for k in ["冲突", "对峙", "反转", "危机", "爆发", "背叛", "抉择"])

    signal = 0.0
    signal += min(events_count / 6.0, 1.0) * 0.30
    signal += min(summary_len / 260.0, 1.0) * 0.15
    signal += (0.15 if payoff else 0.0)
    signal += (0.10 if purpose else 0.0)
    signal += (0.10 if mini_climax not in {"", "none", "无"} else 0.0)
    signal += (0.08 if suspense in {"中", "高", "高强"} else 0.03 if suspense else 0.0)
    signal += (0.07 if has_conflict_word else 0.0)
    signal += max(0.0, min(1.0, review_score)) * 0.03
    signal += max(0.0, min(1.0, factual_score)) * 0.02
    return max(0.0, min(1.0, signal))


def _safe_issue(item: Any, severity: str = "should_fix") -> dict[str, Any]:
    if not isinstance(item, dict):
        return {"category": "general", "severity": severity, "claim": str(item or ""), "evidence": "", "confidence": 0.55}
    return {
        "category": str(item.get("category") or "general")[:40],
        "severity": str(item.get("severity") or severity)[:20],
        "claim": str(item.get("claim") or "")[:220],
        "evidence": str(item.get("evidence") or "")[:120],
        "confidence": max(0.0, min(1.0, float(item.get("confidence", 0.55) or 0.55))),
    }


def _evidence_valid(issue: dict[str, Any], draft: str) -> bool:
    evidence = str(issue.get("evidence") or "").strip()
    if not evidence:
        return False
    if len(evidence) < 2:
        return False
    return evidence in draft


def _build_consistency_scorecard(report: Any) -> dict[str, Any]:
    issues = list(getattr(report, "issues", []) or [])
    blockers = list(getattr(report, "blockers", []) or [])
    warnings = list(getattr(report, "warnings", []) or [])
    category_counts: dict[str, int] = {}
    for i in issues:
        key = str(getattr(i, "category", "unknown") or "unknown")
        category_counts[key] = category_counts.get(key, 0) + 1
    score = max(0.0, min(1.0, 1.0 - (len(blockers) * 0.32) - (len(warnings) * 0.08)))
    reason_codes: list[str] = []
    for cat, n in sorted(category_counts.items()):
        if n > 0:
            reason_codes.append(f"{cat}:{n}")
    return {
        "score": round(score, 4),
        "passed": bool(getattr(report, "passed", False)),
        "blockers": len(blockers),
        "warnings": len(warnings),
        "categories": category_counts,
        "reason_codes": reason_codes[:8],
        "issues": [
            {
                "level": str(getattr(i, "level", "")),
                "category": str(getattr(i, "category", "")),
                "message": str(getattr(i, "message", ""))[:220],
            }
            for i in issues[:12]
        ],
    }


def _normalize_reviewer_payload(result: Any, default_feedback: str = "") -> dict[str, Any]:
    if isinstance(result, dict):
        score = float(result.get("score", 0.75) or 0.75)
        return {
            "score": max(0.0, min(1.0, score)),
            "confidence": max(0.0, min(1.0, float(result.get("confidence", 0.6) or 0.6))),
            "feedback": str(result.get("feedback", default_feedback or "")),
            "must_fix": [_safe_issue(x, "must_fix") for x in (result.get("must_fix") or [])][:4],
            "should_fix": [_safe_issue(x, "should_fix") for x in (result.get("should_fix") or [])][:4],
            "positives": [str(x)[:120] for x in (result.get("positives") or result.get("highlights") or []) if str(x).strip()][:6],
            "risks": [str(x)[:120] for x in (result.get("risks") or []) if str(x).strip()][:6],
            "contradictions": [str(x)[:180] for x in (result.get("contradictions") or []) if str(x).strip()][:10],
            "raw": result,
        }
    if isinstance(result, tuple):
        if len(result) >= 3:
            score, feedback, third = result[0], result[1], result[2]
            extra = [str(x) for x in (third or [])][:6] if isinstance(third, list) else []
            return {
                "score": max(0.0, min(1.0, float(score or 0.75))),
                "confidence": 0.55,
                "feedback": str(feedback or default_feedback),
                "must_fix": [],
                "should_fix": [],
                "positives": extra,
                "risks": [],
                "contradictions": extra,
                "raw": {},
            }
        if len(result) >= 2:
            score, feedback = result[0], result[1]
            return {
                "score": max(0.0, min(1.0, float(score or 0.75))),
                "confidence": 0.55,
                "feedback": str(feedback or default_feedback),
                "must_fix": [],
                "should_fix": [],
                "positives": [],
                "risks": [],
                "contradictions": [],
                "raw": {},
            }
    return {
        "score": 0.75,
        "confidence": 0.4,
        "feedback": default_feedback,
        "must_fix": [],
        "should_fix": [],
        "positives": [],
        "risks": ["invalid_reviewer_payload"],
        "contradictions": [],
        "raw": {},
    }


def _build_review_gate(
    draft: str,
    struct_payload: dict[str, Any],
    factual_payload: dict[str, Any],
    aesthetic_payload: dict[str, Any],
) -> dict[str, Any]:
    all_must_fix = list(struct_payload.get("must_fix") or []) + list(factual_payload.get("must_fix") or []) + list(aesthetic_payload.get("must_fix") or [])
    validated: list[dict[str, Any]] = []
    weak: list[dict[str, Any]] = []
    for issue in all_must_fix:
        conf = float(issue.get("confidence", 0.0) or 0.0)
        if _evidence_valid(issue, draft) and conf >= 0.6:
            validated.append(issue)
        else:
            weak.append(issue)
    evidence_coverage = len(validated) / max(1, len(all_must_fix))
    over_correction_risk = bool(len(all_must_fix) >= 3 and evidence_coverage < 0.34)
    avg_confidence = (
        float(struct_payload.get("confidence", 0.0) or 0.0)
        + float(factual_payload.get("confidence", 0.0) or 0.0)
        + float(aesthetic_payload.get("confidence", 0.0) or 0.0)
    ) / 3.0
    min_score = min(
        float(struct_payload.get("score", 0.0) or 0.0),
        float(factual_payload.get("score", 0.0) or 0.0),
        float(aesthetic_payload.get("score", 0.0) or 0.0),
    )
    gate_decision = "rewrite"
    if len(all_must_fix) == 0 and avg_confidence >= 0.72 and min_score >= 0.68:
        gate_decision = "accept_with_minor_polish"
    elif len(validated) == 0 and evidence_coverage < 0.25 and avg_confidence >= 0.8:
        gate_decision = "accept_with_minor_polish"
    return {
        "must_fix_total": len(all_must_fix),
        "must_fix_validated": len(validated),
        "must_fix_weak": len(weak),
        "evidence_coverage": round(evidence_coverage, 4),
        "avg_confidence": round(avg_confidence, 4),
        "min_score": round(min_score, 4),
        "over_correction_risk": over_correction_risk,
        "decision": gate_decision,
        "validated_issues": validated[:4],
        "weak_issues": weak[:4],
    }


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
                        novel_version_id=state.get("novel_version_id"),
                        entity_type="character",
                        name=str(c["name"]),
                        status="alive",
                        summary=str(c.get("description") or c.get("role") or "")[:300],
                        metadata={"source": "specification"},
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
                novel_version_id=state.get("novel_version_id"),
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
                novel_version_id=state.get("novel_version_id"),
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
        )

    extracted_entities: dict[str, Any] = {}
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
            )
        bible.add_fact(
            novel_id=state["novel_id"],
            novel_version_id=state.get("novel_version_id"),
            entity_id=entity.id,
            fact_type=str(fact.get("fact_type") or "attribute")[:100],
            value_json={"value": fact.get("value"), "chapter_num": chapter_num},
            chapter_from=chapter_num,
        )

    # Fact writeback: timeline markers and key item mentions.
    for marker in _extract_timeline_markers(final_content):
        bible.add_event(
            novel_id=state["novel_id"],
            novel_version_id=state.get("novel_version_id"),
            event_id=f"TL-{chapter_num:04d}-{abs(hash(marker)) % 10000:04d}",
            chapter_num=chapter_num,
            title=f"时间标记:{marker}",
            event_type="timeline_marker",
            payload={"marker": marker},
        )

    for item_name in _extract_item_mentions(final_content):
        item_entity = bible.upsert_entity(
            novel_id=state["novel_id"],
            novel_version_id=state.get("novel_version_id"),
            entity_type="item",
            name=item_name,
            status="active",
            summary=f"在第{chapter_num}章出现",
            metadata={"source": "chapter_content"},
        )
        bible.add_fact(
            novel_id=state["novel_id"],
            novel_version_id=state.get("novel_version_id"),
            entity_id=item_entity.id,
            fact_type="mentioned_in_chapter",
            value_json={"chapter_num": chapter_num, "context": final_content[:300]},
            chapter_from=chapter_num,
        )

    # Relationship signal writeback by character co-occurrence.
    chars = bible.list_entities(
        state["novel_id"],
        novel_version_id=state.get("novel_version_id"),
        entity_type="character",
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
        novel_version_id=state.get("novel_version_id"),
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
            "consistency_scorecard": state.get("consistency_scorecard") or {},
            "review_gate": state.get("review_gate") or {},
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
                "consistency_scorecard": state.get("consistency_scorecard") or {},
                "review_gate": state.get("review_gate") or {},
                "summary": summary_text[:400],
                "content_preview": final_content[:400],
            },
        )

    volume_size = max(int(state.get("volume_size") or 30), 1)
    is_volume_end = (chapter_num - state.get("start_chapter", 1) + 1) % volume_size == 0 or chapter_num == state["end_chapter"]
    if is_volume_end:
        chapter_reports = quality.list_reports(
            novel_id=state["novel_id"],
            novel_version_id=state.get("novel_version_id"),
            scope="chapter",
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
                "blocked_chapters": blocked_count,
                "evidence_chain": evidence_chain,
            },
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
    save_full_outlines(state["novel_id"], full_outlines, novel_version_id=state.get("novel_version_id"))
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
            novel_version_id=state.get("novel_version_id"),
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

    constraints = state["bible_store"].get_chapter_constraints(
        state["novel_id"],
        chapter_num,
        novel_version_id=state.get("novel_version_id"),
    )
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

def _route_consistency(state: GenerationState) -> str:
    if not state["consistency_report"].passed and not state.get("consistency_soft_fail"):
        return "save_blocked"
    return "beats"


def _route_review(state: GenerationState) -> str:
    review_gate = state.get("review_gate") or {}
    if review_gate.get("decision") == "accept_with_minor_polish":
        return "finalizer"
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


def _route_after_closure_gate(state: GenerationState) -> str:
    action = str((state.get("closure_state") or {}).get("action") or "")
    if action == "rewrite_tail":
        return "tail_rewrite"
    if action == "bridge_chapter":
        return "bridge_chapter"
    if state["current_chapter"] > state["end_chapter"]:
        return "final_book_review"
    return "volume_replan" if _is_volume_start(state, state["current_chapter"]) else "load_context"


def _route_after_tail_rewrite(state: GenerationState) -> str:
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
        ctx = build_chapter_context(
            state["novel_id"],
            state.get("novel_version_id"),
            chapter_num,
            state["prewrite"],
            outline,
            db=db,
        )
        ctx["prewrite"] = state["prewrite"]
        ctx["chapter_outline"] = outline
        ctx["volume_plan"] = state.get("volume_plan") or {}
        ctx["closure_state"] = state.get("closure_state") or {}
        ctx["decision_state"] = state.get("decision_state") or {}
        ctx["prompt_contract"] = {
            "NarrativeIntent": {
                "chapter_goal": str(outline.get("purpose") or "推进主线并形成阶段性兑现"),
                "conflict_target": str(outline.get("plot_twist_level") or "中"),
                "payoff_target": str(outline.get("payoff") or "无"),
            },
            "ClosureIntent": {
                "phase_mode": str((state.get("closure_state") or {}).get("phase_mode") or ""),
                "must_close_items": (state.get("closure_state") or {}).get("must_close_items") or [],
                "suppress_new_mainline": str((state.get("closure_state") or {}).get("phase_mode") or "") in {"closing", "finale"},
            },
            "PacingIntent": {
                "mode": str(state.get("pacing_mode") or "normal"),
                "min_progress_signal": 0.45,
                "streak": int(state.get("low_progress_streak") or 0),
            },
            "HardConstraints": {
                "consistency": state["bible_store"].get_chapter_constraints(
                    state["novel_id"],
                    chapter_num,
                    novel_version_id=state.get("novel_version_id"),
                    db=db,
                ),
            },
        }
        ctx["hard_constraints"] = ctx["prompt_contract"]["HardConstraints"]["consistency"]
        ctx["character_states"] = state["char_mgr"].get_states(
            state["novel_id"],
            chapter_num,
            db=db,
            novel_version_id=state.get("novel_version_id"),
        )
        ctx["summaries"] = state["summary_mgr"].get_summaries_before(
            state["novel_id"],
            state.get("novel_version_id"),
            chapter_num,
            db=db,
        )
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
    closure_state = state.get("closure_state") or {}
    closure_items = closure_state.get("must_close_items") or []
    closure_phase = str(closure_state.get("phase_mode") or "")
    pacing_mode = str(state.get("pacing_mode") or "normal")
    low_progress_streak = int(state.get("low_progress_streak") or 0)
    if closure_phase in {"closing", "finale"}:
        beats.append({"name": "ending_mode", "target": "收官阶段：减少新支线，优先闭环主线冲突与高优先伏笔。"})
    if pacing_mode in {"accelerated", "closing_accelerated"}:
        beats.append({"name": "pace_boost", "target": "连续低推进触发加速：本章必须出现不可逆变化与冲突升级。"})
        beats.append({"name": "payoff_boost", "target": "至少兑现1个既有伏笔/矛盾，禁止空转铺垫。"})
        if low_progress_streak >= 3:
            beats.append({"name": "hard_hook", "target": "章末必须形成强钩子，且直接连接下一章主冲突。"})
    if closure_items:
        labels = [str(x.get("title") or x.get("id") or "") for x in closure_items[:2] if x]
        if labels:
            beats.append({"name": "closure_target", "target": "本章优先回收：" + "；".join(labels)})
    ctx = dict(state["context"])
    ctx["beat_sheet"] = beats
    contract = dict(ctx.get("prompt_contract") or {})
    contract["NarrativeIntent"] = {
        "chapter_goal": str(outline.get("purpose") or "推进主线并形成阶段兑现"),
        "conflict_target": str(outline.get("plot_twist_level") or "中"),
        "payoff_target": str(outline.get("payoff") or "无"),
        "beats": beats,
    }
    contract["PacingIntent"] = {
        "mode": pacing_mode,
        "streak": low_progress_streak,
        "min_progress_signal": 0.45,
    }
    contract["ClosureIntent"] = {
        "phase_mode": closure_phase,
        "must_close_items": closure_items[:3],
        "suppress_new_mainline": closure_phase in {"closing", "finale"},
    }
    ctx["prompt_contract"] = contract
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
    report = check_consistency(
        state["novel_id"],
        state["novel_version_id"],
        chapter_num,
        state["outline"],
        state["context"],
        state["prewrite"],
    )
    scorecard = _build_consistency_scorecard(report)
    if report.passed:
        return {
            "consistency_report": report,
            "consistency_scorecard": scorecard,
            "context": inject_consistency_context(state["context"], report),
            "consistency_soft_fail": False,
        }
    closure_phase = str((state.get("closure_state") or {}).get("phase_mode") or "")
    if closure_phase in {"closing", "finale"}:
        return {
            "consistency_report": report,
            "consistency_scorecard": scorecard,
            "context": inject_consistency_context(state["context"], report),
            "consistency_soft_fail": True,
        }
    return {"consistency_report": report, "consistency_scorecard": scorecard, "consistency_soft_fail": False}


def _node_save_blocked(state: GenerationState) -> GenerationState:
    chapter_num = state["current_chapter"]
    report = state["consistency_report"]
    chapter_title = resolve_chapter_title(
        chapter_num=chapter_num,
        title=(state.get("outline") or {}).get("title"),
        outline=state.get("outline") or {},
    )
    db = SessionLocal()
    try:
        existing_stmt = select(ChapterVersion).where(
            ChapterVersion.novel_version_id == state.get("novel_version_id"),
            ChapterVersion.chapter_num == chapter_num,
        )
        existing = db.execute(existing_stmt).scalar_one_or_none()
        payload = {
            "title": chapter_title,
            "content": "",
            "summary": "",
            "status": "consistency_blocked",
            "metadata_": {"consistency_report": report.summary(), "consistency_blocked": True},
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
        db.commit()
    finally:
        db.close()
    volume_no = _volume_no_for_chapter(state, chapter_num)
    state["quality_store"].add_report(
        novel_id=state["novel_id"],
        novel_version_id=state.get("novel_version_id"),
        scope="chapter",
        scope_id=str(chapter_num),
        metrics_json={
            "blocked": True,
            "volume_no": volume_no,
            "reason": report.summary(),
            "consistency_scorecard": state.get("consistency_scorecard") or {},
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
            state_json={
                "reason": report.summary(),
                "consistency_scorecard": state.get("consistency_scorecard") or {},
            },
        )
    _progress(state, "chapter_blocked", chapter_num, _chapter_progress(state, 1.0), f"第{chapter_num}章因一致性检查未通过已跳过", {"current_phase": "chapter_blocked", "total_chapters": state["num_chapters"]})
    return {"outline": {**(state.get("outline") or {}), "title": chapter_title}}


def _node_writer(state: GenerationState) -> GenerationState:
    def _is_invalid_draft(text: str) -> bool:
        t = str(text or "").strip().lower()
        if not t:
            return True
        return ("content generation failed" in t) or t.startswith("[chapter ")

    def _safe_write(
        chapter_num_: int,
        outline_: dict[str, Any],
        ctx_: dict[str, Any],
        provider_: str | None,
        model_: str | None,
    ) -> str:
        draft = state["writer"].run(
            state["novel_id"],
            chapter_num_,
            outline_,
            ctx_,
            state["target_language"],
            state["native_style_profile"],
            provider_,
            model_,
        )
        if _is_invalid_draft(draft):
            raise RuntimeError("writer returned invalid placeholder draft")
        return draft

    chapter_num = state["current_chapter"]
    attempt = state.get("review_attempt", 0) + 1
    _progress(state, "writer", chapter_num, _chapter_progress(state, 0.35), f"写作第{chapter_num}章（尝试{attempt}）...", {"current_phase": "chapter_writing", "total_chapters": state["num_chapters"]})
    w_provider, w_model = get_model_for_stage(state["strategy"], "writer")
    pacing_mode = str(state.get("pacing_mode") or "normal")
    ctx_a = dict(state["context"])
    ctx_a["ab_variant"] = "A"
    ctx_a["ab_goal"] = "稳健推进主线，保持事实一致。"
    if pacing_mode in {"accelerated", "closing_accelerated"}:
        ctx_a["ab_goal"] = "加速推进主线，减少铺垫，必须输出明确冲突升级与阶段兑现。"
    def _attempt_ab_write():
        _draft_a = ""
        _err_a: Exception | None = None
        try:
            _draft_a = _safe_write(chapter_num, state["outline"], ctx_a, w_provider, w_model)
        except Exception as exc:
            _err_a = exc

        if _draft_a:
            return _draft_a, "", _err_a, None

        ctx_b = dict(state["context"])
        ctx_b["ab_variant"] = "B"
        ctx_b["ab_goal"] = "增强情绪张力和节奏反转，保持硬约束不变。"
        if pacing_mode in {"accelerated", "closing_accelerated"}:
            ctx_b["ab_goal"] = "强化反转与高压冲突，提升推进效率并压缩无效叙述。"
        _draft_b = ""
        _err_b: Exception | None = None
        try:
            _draft_b = _safe_write(chapter_num, state["outline"], ctx_b, w_provider, w_model)
        except Exception as exc:
            _err_b = exc
        return _draft_a, _draft_b, _err_a, _err_b

    _NODE_WRITER_MAX_ROUNDS = 2
    _NODE_WRITER_ROUND_DELAY = 15.0
    draft_a = ""
    draft_b = ""
    err_a: Exception | None = None
    err_b: Exception | None = None
    for _round in range(_NODE_WRITER_MAX_ROUNDS):
        draft_a, draft_b, err_a, err_b = _attempt_ab_write()
        if draft_a or draft_b:
            break
        if _round < _NODE_WRITER_MAX_ROUNDS - 1:
            import logging as _lg
            _lg.getLogger(__name__).warning(
                "writer AB both failed round=%s, retrying in %.0fs: A=%s B=%s",
                _round + 1, _NODE_WRITER_ROUND_DELAY, err_a, err_b,
            )
            import time as _time
            _time.sleep(_NODE_WRITER_ROUND_DELAY)

    if not draft_a and not draft_b:
        contract_errors = [e for e in (err_a, err_b) if isinstance(e, OutputContractError)]
        if contract_errors:
            detail = f"A={err_a}; B={err_b}"
            if all(e.code == "MODEL_OUTPUT_POLICY_VIOLATION" for e in contract_errors):
                raise OutputContractError(
                    code="MODEL_OUTPUT_POLICY_VIOLATION",
                    stage="writer",
                    chapter_num=chapter_num,
                    provider=w_provider,
                    model=w_model,
                    detail=detail,
                    retryable=False,
                )
            raise OutputContractError(
                code="MODEL_OUTPUT_CONTRACT_EXHAUSTED",
                stage="writer",
                chapter_num=chapter_num,
                provider=w_provider,
                model=w_model,
                detail=detail,
                retryable=True,
            )
        raise RuntimeError(f"writer failed for both variants: A={err_a}, B={err_b}")

    candidates = [
        {"variant": "A", "draft": draft_a} if draft_a else None,
        {"variant": "B", "draft": draft_b} if draft_b else None,
    ]
    candidates = [c for c in candidates if c]
    usage = snapshot_usage()
    input_tokens = int(usage.get("input_tokens") or state["total_input_tokens"] or 0)
    output_tokens = int(usage.get("output_tokens") or state["total_output_tokens"] or 0)
    primary = draft_a or draft_b
    return {"candidate_drafts": candidates, "draft": primary, "total_input_tokens": input_tokens, "total_output_tokens": output_tokens}


def _node_review(state: GenerationState) -> GenerationState:
    chapter_num = state["current_chapter"]
    _progress(state, "reviewer", chapter_num, _chapter_progress(state, 0.55), "章节审校...", {"current_phase": "chapter_review", "total_chapters": state["num_chapters"]})
    r_provider, r_model = get_model_for_stage(state["strategy"], "reviewer")
    candidates = state.get("candidate_drafts") or [{"variant": "A", "draft": state.get("draft", "")}]
    best = None
    for c in candidates:
        text = str(c.get("draft") or "")
        if hasattr(state["reviewer"], "run_structured"):
            struct_raw = state["reviewer"].run_structured(
                text,
                chapter_num,
                state["target_language"],
                state["native_style_profile"],
                r_provider,
                r_model,
            )
        else:
            struct_raw = state["reviewer"].run(
                text,
                chapter_num,
                state["target_language"],
                state["native_style_profile"],
                r_provider,
                r_model,
            )
        if hasattr(state["reviewer"], "run_factual_structured"):
            factual_raw = state["reviewer"].run_factual_structured(
                text,
                chapter_num,
                state.get("context") or {},
                state["target_language"],
                r_provider,
                r_model,
            )
        else:
            factual_raw = state["reviewer"].run_factual(
                text,
                chapter_num,
                state.get("context") or {},
                state["target_language"],
                r_provider,
                r_model,
            )
        if hasattr(state["reviewer"], "run_aesthetic_structured"):
            aesthetic_raw = state["reviewer"].run_aesthetic_structured(
                text,
                chapter_num,
                state["target_language"],
                r_provider,
                r_model,
            )
        else:
            aesthetic_raw = state["reviewer"].run_aesthetic(
                text,
                chapter_num,
                state["target_language"],
                r_provider,
                r_model,
            )
        struct_pack = _normalize_reviewer_payload(struct_raw, "结构审校结果")
        factual_pack = _normalize_reviewer_payload(factual_raw, "事实审校结果")
        aesthetic_pack = _normalize_reviewer_payload(aesthetic_raw, "审美审校结果")
        struct_score = float(struct_pack.get("score", 0.75))
        factual_score = float(factual_pack.get("score", 0.75))
        aesthetic_score = float(aesthetic_pack.get("score", 0.75))
        combined = (struct_score * 0.45) + (factual_score * 0.35) + (aesthetic_score * 0.20)
        review_gate = _build_review_gate(text, struct_pack, factual_pack, aesthetic_pack)
        if review_gate.get("over_correction_risk"):
            combined = min(1.0, combined + 0.05)
        item = {
            "variant": c.get("variant"),
            "draft": text,
            "combined": combined,
            "struct_score": struct_score,
            "factual_score": factual_score,
            "aesthetic_score": aesthetic_score,
            "feedback": str(struct_pack.get("feedback") or ""),
            "factual_feedback": str(factual_pack.get("feedback") or ""),
            "aesthetic_feedback": str(aesthetic_pack.get("feedback") or ""),
            "contradictions": factual_pack.get("contradictions") or [],
            "highlights": aesthetic_pack.get("positives") or [],
            "struct_pack": struct_pack,
            "factual_pack": factual_pack,
            "aesthetic_pack": aesthetic_pack,
            "review_gate": review_gate,
        }
        if best is None or item["combined"] > best["combined"]:
            best = item
    if best is None:
        return {"score": 0.0, "feedback": "review failed", "factual_score": 0.0, "aesthetic_review_score": 0.0}
    suggestions = {
        "missing_payoff": [],
        "weak_conflict": [],
        "timeline_gap": [],
        "closure_risk": [],
        "scorecards": {
            "structure": best.get("struct_pack") or {},
            "factual": best.get("factual_pack") or {},
            "aesthetic": best.get("aesthetic_pack") or {},
        },
        "review_gate": best.get("review_gate") or {},
    }
    for c in (best.get("contradictions") or [])[:8]:
        txt = str(c).strip()
        if txt:
            suggestions["timeline_gap"].append(txt[:180])
    factual_fb = str(best.get("factual_feedback") or "")
    if "伏笔" in factual_fb or "回收" in factual_fb:
        suggestions["missing_payoff"].append(factual_fb[:180])
        suggestions["closure_risk"].append("存在伏笔回收风险")
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
        "review_suggestions": suggestions,
        "review_gate": best.get("review_gate") or {},
    }


def _node_revise(state: GenerationState) -> GenerationState:
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


def _node_rollback_rerun(state: GenerationState) -> GenerationState:
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
    base_feedback = str(state.get("feedback") or "")
    format_guardrail = (
        "【输出格式纠偏】上一次输出包含说明性前言或标题污染。\n"
        "现在必须仅输出章节正文，不要任何解释/总结/标题/Markdown；"
        "禁止出现“以下是根据反馈”“重点解决如下问题”等语句。"
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
    chapter_title = resolve_chapter_title(
        chapter_num=chapter_num,
        title=(state.get("outline") or {}).get("title"),
        outline=state.get("outline") or {},
        content=final_content,
    )

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
        db.commit()
        _write_longform_artifacts(
            state={**state, "outline": {**(state.get("outline") or {}), "title": chapter_title}},
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

    usage = snapshot_usage()
    total_input_tokens = int(usage.get("input_tokens") or state["total_input_tokens"] or 0)
    total_output_tokens = int(usage.get("output_tokens") or state["total_output_tokens"] or 0)
    estimated_cost = round((total_input_tokens / 1000) * 0.0015 + (total_output_tokens / 1000) * 0.002, 6)
    factual_score = float(state.get("factual_score", 0.0) or 0.0)
    progress_signal = _chapter_progress_signal(
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
            progress_signal=progress_signal,
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
        "aesthetic_score": round(float(aesthetic_score), 4),
        "consistency_scorecard": state.get("consistency_scorecard") or {},
        "review_gate": state.get("review_gate") or {},
        "quality_passed": bool(
            state.get("score", 0.0) >= REVIEW_SCORE_THRESHOLD
            and factual_score >= 0.65
            and language_score >= 0.6
            and float(state.get("aesthetic_review_score", 0.0) or 0.0) >= 0.6
            and aesthetic_score >= 0.6
        ),
        "review_suggestions": state.get("review_suggestions") or {},
    }
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
            "token_usage_output": total_output_tokens,
            "estimated_cost": estimated_cost,
            "pacing_mode": pacing_mode,
            "low_progress_streak": next_streak,
            "progress_signal": round(progress_signal, 4),
            "decision_state": decision_state,
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
        fail_reason = render_prompt(
            "post_quality_gate_fail_reason",
            review_score=f"{state.get('score', 0.0):.2f}",
            factual_score=f"{factual_score:.2f}",
            language_score=f"{language_score:.2f}",
            reviewer_aesthetic=f"{reviewer_aesthetic:.2f}",
            aesthetic_score=f"{aesthetic_score:.2f}",
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


def _node_advance_chapter(state: GenerationState) -> GenerationState:
    return {"current_chapter": state["current_chapter"] + 1}


def _node_closure_gate(state: GenerationState) -> GenerationState:
    closure_state = _build_closure_state(state)
    chapter_num = int(state.get("current_chapter") or 1)
    action = str(closure_state.get("action") or "continue")
    decision_state = dict(state.get("decision_state") or {})
    decision_state["closure"] = {
        "phase_mode": closure_state.get("phase_mode"),
        "action": closure_state.get("action"),
        "closure_score": closure_state.get("closure_score"),
        "must_close_coverage": closure_state.get("must_close_coverage"),
        "threshold": closure_state.get("closure_threshold"),
        "unresolved_count": closure_state.get("unresolved_count"),
        "bridge_budget_left": closure_state.get("bridge_budget_left"),
        "bridge_budget_total": closure_state.get("bridge_budget_total"),
        "min_total_chapters": closure_state.get("min_total_chapters"),
        "max_total_chapters": closure_state.get("max_total_chapters"),
        "must_close_items": closure_state.get("must_close_items") or [],
        "tail_rewrite_attempts": closure_state.get("tail_rewrite_attempts"),
        "reasons": closure_state.get("reason_codes") or [],
        "confidence": closure_state.get("confidence"),
    }
    updates: dict[str, Any] = {"closure_state": closure_state, "decision_state": decision_state}
    progress_meta = {
        "current_phase": "closure_gate",
        "total_chapters": state["num_chapters"],
        "action": action,
        "reason_codes": closure_state.get("reason_codes") or [],
        "remaining_ratio": closure_state.get("remaining_ratio"),
        "unresolved_count": closure_state.get("unresolved_count"),
        "decision_state": decision_state,
    }

    if action == "bridge_chapter":
        updates["end_chapter"] = int(state["end_chapter"]) + 1
        updates["num_chapters"] = int(state["num_chapters"]) + 1
        updates["bridge_attempts"] = int(state.get("bridge_attempts") or 0) + 1
        progress_meta["total_chapters"] = updates["num_chapters"]
        _progress(
            state,
            "closure_gate",
            chapter_num,
            min(96.0, _chapter_progress(state, 0.95)),
            "收官检查未通过，自动扩展1章进行补完",
            progress_meta,
        )
    elif action in {"finalize", "force_finalize"}:
        finalized_end = max(int(state.get("start_chapter") or 1), chapter_num - 1)
        updates["end_chapter"] = finalized_end
        updates["num_chapters"] = finalized_end - int(state.get("start_chapter") or 1) + 1
        updates["current_chapter"] = finalized_end + 1
        progress_meta["total_chapters"] = updates["num_chapters"]
        _progress(
            state,
            "closure_gate",
            chapter_num,
            min(97.0, _chapter_progress(state, 0.98)),
            "收官门禁通过，进入终审",
            progress_meta,
        )
    elif action == "rewrite_tail":
        _progress(
            state,
            "closure_gate",
            chapter_num,
            min(96.5, _chapter_progress(state, 0.96)),
            "收官检查发现未闭环项，准备回退重写尾部章节",
            progress_meta,
        )
    else:
        _progress(
            state,
            "closure_gate",
            chapter_num,
            min(95.0, _chapter_progress(state, 0.92)),
            "收官检查通过，继续写作",
            progress_meta,
        )

    if state.get("task_id"):
        state["checkpoint_store"].save_checkpoint(
            task_id=state["task_id"],
            novel_id=state["novel_id"],
            volume_no=_volume_no_for_chapter(state, max(chapter_num - 1, int(state.get("start_chapter") or 1))),
            chapter_num=max(chapter_num - 1, int(state.get("start_chapter") or 1)),
            node="closure_gate",
            state_json=closure_state,
        )
    return updates


def _node_tail_rewrite(state: GenerationState) -> GenerationState:
    start_chapter = int(state.get("start_chapter") or 1)
    current = int(state.get("current_chapter") or start_chapter)
    rewind_to = max(start_chapter, current - 2)
    attempts = int(state.get("tail_rewrite_attempts") or 0) + 1
    closure_state = state.get("closure_state") or {}
    _progress(
        state,
        "tail_rewrite",
        rewind_to,
        min(96.8, _chapter_progress(state, 0.97)),
        f"进入第{attempts}轮尾章重写（回退到第{rewind_to}章）",
        {
            "current_phase": "tail_rewrite",
            "total_chapters": state["num_chapters"],
            "rewrite_attempts": attempts,
            "remaining_ratio": closure_state.get("remaining_ratio"),
            "unresolved_count": closure_state.get("unresolved_count"),
        },
    )
    if state.get("task_id"):
        state["checkpoint_store"].save_checkpoint(
            task_id=state["task_id"],
            novel_id=state["novel_id"],
            volume_no=_volume_no_for_chapter(state, rewind_to),
            chapter_num=rewind_to,
            node="tail_rewrite",
            state_json={
                "rewrite_attempts": attempts,
                "rewind_to": rewind_to,
                "closure_state": closure_state,
            },
        )
    return {
        "current_chapter": rewind_to,
        "tail_rewrite_attempts": attempts,
        "decision_state": {
            **(state.get("decision_state") or {}),
            "closure": {
                **((state.get("decision_state") or {}).get("closure") or {}),
                "action": "continue",
            },
        },
        "closure_state": {**closure_state, "action": "continue"},
    }


def _node_bridge_chapter(state: GenerationState) -> GenerationState:
    chapter_num = int(state.get("current_chapter") or 1)
    _progress(
        state,
        "bridge_chapter",
        chapter_num,
        min(96.2, _chapter_progress(state, 0.95)),
        "已追加桥接章节预算，继续推进主线收束",
        {
            "current_phase": "bridge_chapter",
            "total_chapters": state["num_chapters"],
        },
    )
    return {
        "closure_state": {**(state.get("closure_state") or {}), "action": "continue"},
        "decision_state": {
            **(state.get("decision_state") or {}),
            "closure": {
                **((state.get("decision_state") or {}).get("closure") or {}),
                "action": "continue",
            },
        },
    }


def _node_final_book_review(state: GenerationState) -> GenerationState:
    db = SessionLocal()
    try:
        last_chapter = state["start_chapter"] + state["num_chapters"] - 1
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

    _progress(state, "final_book_review", state["num_chapters"], 97, "全书终审...", {"current_phase": "full_book_review", "total_chapters": state["num_chapters"]})
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
    def _timed_node(name: str, fn):
        def _wrapped(state: GenerationState):
            started = time.perf_counter()
            chapter = int(state.get("current_chapter") or 0)
            task_id = state.get("task_id")
            novel_id = state.get("novel_id")
            log_event(
                logger,
                "pipeline.node.start",
                node=name,
                task_id=task_id,
                novel_id=novel_id,
                chapter_num=chapter,
                volume_no=_volume_no_for_chapter(state, chapter) if chapter > 0 else None,
            )
            try:
                out = fn(state)
                elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                log_event(
                    logger,
                    "pipeline.node.end",
                    node=name,
                    task_id=task_id,
                    novel_id=novel_id,
                    chapter_num=chapter,
                    volume_no=_volume_no_for_chapter(state, chapter) if chapter > 0 else None,
                    latency_ms=elapsed_ms,
                )
                slow_threshold_ms = int(get_settings().log_node_slow_threshold_ms or 2500)
                if elapsed_ms > slow_threshold_ms:
                    log_event(
                        logger,
                        "pipeline.node.slow",
                        level=30,
                        node=name,
                        task_id=task_id,
                        novel_id=novel_id,
                        chapter_num=chapter,
                        volume_no=_volume_no_for_chapter(state, chapter) if chapter > 0 else None,
                        latency_ms=elapsed_ms,
                        threshold_ms=slow_threshold_ms,
                    )
                return out
            except Exception as exc:
                elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                log_event(
                    logger,
                    "pipeline.node.error",
                    level=40,
                    message="Pipeline node failed",
                    node=name,
                    task_id=task_id,
                    novel_id=novel_id,
                    chapter_num=chapter,
                    volume_no=_volume_no_for_chapter(state, chapter) if chapter > 0 else None,
                    latency_ms=elapsed_ms,
                    error_class=type(exc).__name__,
                    error_code="PIPELINE_NODE_ERROR",
                    error_category="permanent",
                )
                raise
        return _wrapped

    graph = StateGraph(GenerationState)
    graph.add_node("init", _timed_node("init", _node_init))
    graph.add_node("prewrite", _timed_node("prewrite", _node_prewrite))
    graph.add_node("outline", _timed_node("outline", _node_outline))
    graph.add_node("confirmation_gate", _timed_node("confirmation_gate", _node_confirmation_gate))
    graph.add_node("volume_replan", _timed_node("volume_replan", _node_volume_replan))
    graph.add_node("load_context", _timed_node("load_context", _node_load_context))
    graph.add_node("consistency_check", _timed_node("consistency_check", _node_consistency_check))
    graph.add_node("save_blocked", _timed_node("save_blocked", _node_save_blocked))
    graph.add_node("beats", _timed_node("beats", _node_beats))
    graph.add_node("writer", _timed_node("writer", _node_writer))
    graph.add_node("reviewer", _timed_node("reviewer", _node_review))
    graph.add_node("revise", _timed_node("revise", _node_revise))
    graph.add_node("rollback_rerun", _timed_node("rollback_rerun", _node_rollback_rerun))
    graph.add_node("finalizer", _timed_node("finalizer", _node_finalize))
    graph.add_node("advance_chapter", _timed_node("advance_chapter", _node_advance_chapter))
    graph.add_node("closure_gate", _timed_node("closure_gate", _node_closure_gate))
    graph.add_node("bridge_chapter", _timed_node("bridge_chapter", _node_bridge_chapter))
    graph.add_node("tail_rewrite", _timed_node("tail_rewrite", _node_tail_rewrite))
    graph.add_node("final_book_review", _timed_node("final_book_review", _node_final_book_review))

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
    graph.add_edge("advance_chapter", "closure_gate")
    graph.add_conditional_edges("closure_gate", _route_after_closure_gate, {"volume_replan": "volume_replan", "load_context": "load_context", "bridge_chapter": "bridge_chapter", "tail_rewrite": "tail_rewrite", "final_book_review": "final_book_review"})
    graph.add_conditional_edges("bridge_chapter", _route_after_tail_rewrite, {"volume_replan": "volume_replan", "load_context": "load_context", "final_book_review": "final_book_review"})
    graph.add_conditional_edges("tail_rewrite", _route_after_tail_rewrite, {"volume_replan": "volume_replan", "load_context": "load_context", "final_book_review": "final_book_review"})
    graph.add_edge("final_book_review", END)
    return graph.compile()


_compiled_graph = _build_generation_graph()


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def run_generation_pipeline_langgraph(
    novel_id: int,
    novel_version_id: int,
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
                .limit(1)
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
            "novel_version_id": novel_version_id,
            "num_chapters": num_chapters,
            "start_chapter": start_chapter,
            "task_id": task_id,
            "progress_callback": progress_callback or (lambda *a, **k: None),
        }
    )
