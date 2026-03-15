"""Closure gate, tail-rewrite, and bridge-chapter nodes."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.novel import StoryForeshadow
from app.services.generation.policies import ClosurePolicyEngine, ClosurePolicyInput
from app.services.generation.progress import (
    chapter_progress,
    closure_phase_mode,
    persist_resume_runtime_state,
    progress,
    volume_no_for_chapter,
)
from app.services.generation.state import GenerationState


def build_closure_state(state: GenerationState) -> dict[str, Any]:
    chapter_num = int(state.get("current_chapter") or 1)
    start_chapter = int(state.get("start_chapter") or 1)
    end_chapter = int(state.get("end_chapter") or chapter_num)
    target_chapters = int(state.get("target_chapters") or state.get("num_chapters") or 1)
    min_total = int(state.get("min_total_chapters") or target_chapters)
    max_total = int(state.get("max_total_chapters") or target_chapters)
    generated = max(0, chapter_num - start_chapter)
    remaining = max(0, end_chapter - chapter_num + 1)
    remaining_ratio = remaining / max(target_chapters, 1)
    phase_mode = closure_phase_mode(remaining_ratio)

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
    bridge_attempts_val = int(state.get("bridge_attempts") or 0)
    closure_threshold = float(((state.get("novel_info") or {}).get("closure_threshold")) or 0.95)
    bridge_budget_total = max(0, max_total - target_chapters)
    bridge_budget_left = max(0, bridge_budget_total - bridge_attempts_val)
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
            bridge_attempts=bridge_attempts_val,
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
        "bridge_attempts": bridge_attempts_val,
        "bridge_budget_total": int(decision.next_limits.get("bridge_budget_total") or bridge_budget_total),
        "bridge_budget_left": int(decision.next_limits.get("bridge_budget_left") or bridge_budget_left),
        "reason_codes": decision.reason_codes,
        "confidence": round(float(decision.confidence), 4),
        "next_limits": decision.next_limits,
        "must_close_items": must_close_items[:20],
        "action": action,
    }


def node_closure_gate(state: GenerationState) -> GenerationState:
    closure_state_val = build_closure_state(state)
    chapter_num = int(state.get("current_chapter") or 1)
    action = str(closure_state_val.get("action") or "continue")
    decision_state = dict(state.get("decision_state") or {})
    decision_state["closure"] = {
        "phase_mode": closure_state_val.get("phase_mode"),
        "action": closure_state_val.get("action"),
        "closure_score": closure_state_val.get("closure_score"),
        "must_close_coverage": closure_state_val.get("must_close_coverage"),
        "threshold": closure_state_val.get("closure_threshold"),
        "unresolved_count": closure_state_val.get("unresolved_count"),
        "bridge_budget_left": closure_state_val.get("bridge_budget_left"),
        "bridge_budget_total": closure_state_val.get("bridge_budget_total"),
        "min_total_chapters": closure_state_val.get("min_total_chapters"),
        "max_total_chapters": closure_state_val.get("max_total_chapters"),
        "must_close_items": closure_state_val.get("must_close_items") or [],
        "tail_rewrite_attempts": closure_state_val.get("tail_rewrite_attempts"),
        "reasons": closure_state_val.get("reason_codes") or [],
        "confidence": closure_state_val.get("confidence"),
    }
    updates: dict[str, Any] = {"closure_state": closure_state_val, "decision_state": decision_state}
    progress_meta = {
        "current_phase": "closure_gate",
        "total_chapters": max(int(state.get("num_chapters") or 0), int(state.get("end_chapter") or chapter_num)),
        "action": action,
        "reason_codes": closure_state_val.get("reason_codes") or [],
        "remaining_ratio": closure_state_val.get("remaining_ratio"),
        "unresolved_count": closure_state_val.get("unresolved_count"),
        "decision_state": decision_state,
    }

    if action == "bridge_chapter":
        updates["end_chapter"] = int(state["end_chapter"]) + 1
        updates["num_chapters"] = int(state["num_chapters"]) + 1
        updates["bridge_attempts"] = int(state.get("bridge_attempts") or 0) + 1
        progress_meta["total_chapters"] = max(int(updates["num_chapters"]), int(updates["end_chapter"]))
        progress(
            state,
            "closure_gate",
            chapter_num,
            min(96.0, chapter_progress(state, 0.95)),
            "收官检查未通过，自动扩展1章进行补完",
            progress_meta,
        )
        persist_resume_runtime_state(
            state,
            node="bridge_chapter",
            resume_from_chapter=chapter_num,
            effective_end_chapter=int(updates["end_chapter"]),
            effective_total_chapters=int(updates["end_chapter"]),
            bridge_attempts=int(updates["bridge_attempts"]),
        )
    elif action in {"finalize", "force_finalize"}:
        finalized_end = max(int(state.get("start_chapter") or 1), chapter_num - 1)
        updates["end_chapter"] = finalized_end
        updates["num_chapters"] = finalized_end - int(state.get("start_chapter") or 1) + 1
        updates["current_chapter"] = finalized_end + 1
        progress_meta["total_chapters"] = max(int(updates["num_chapters"]), int(updates["end_chapter"]))
        progress(
            state,
            "closure_gate",
            chapter_num,
            min(97.0, chapter_progress(state, 0.98)),
            "收官门禁通过，进入终审",
            progress_meta,
        )
        persist_resume_runtime_state(
            state,
            node="final_book_review",
            resume_from_chapter=int(updates["current_chapter"]),
            effective_end_chapter=int(updates["end_chapter"]),
            effective_total_chapters=int(updates["end_chapter"]),
        )
    elif action == "rewrite_tail":
        progress(
            state,
            "closure_gate",
            chapter_num,
            min(96.5, chapter_progress(state, 0.96)),
            "收官检查发现未闭环项，准备回退重写尾部章节",
            progress_meta,
        )
        persist_resume_runtime_state(
            state,
            node="tail_rewrite",
            resume_from_chapter=max(int(state.get("start_chapter") or 1), chapter_num - 2),
            effective_end_chapter=int(state.get("end_chapter") or chapter_num),
            effective_total_chapters=int(state.get("end_chapter") or chapter_num),
        )
    else:
        progress(
            state,
            "closure_gate",
            chapter_num,
            min(95.0, chapter_progress(state, 0.92)),
            "收官检查通过，继续写作",
            progress_meta,
        )

    if state.get("task_id"):
        state["checkpoint_store"].save_checkpoint(
            task_id=state["task_id"],
            novel_id=state["novel_id"],
            volume_no=volume_no_for_chapter(state, max(chapter_num - 1, int(state.get("start_chapter") or 1))),
            chapter_num=max(chapter_num - 1, int(state.get("start_chapter") or 1)),
            node="closure_gate",
            state_json=closure_state_val,
        )
    return updates


def node_tail_rewrite(state: GenerationState) -> GenerationState:
    start_chapter = int(state.get("start_chapter") or 1)
    current = int(state.get("current_chapter") or start_chapter)
    rewind_to = max(start_chapter, current - 2)
    attempts = int(state.get("tail_rewrite_attempts") or 0) + 1
    closure_state_val = state.get("closure_state") or {}
    progress(
        state,
        "tail_rewrite",
        rewind_to,
        min(96.8, chapter_progress(state, 0.97)),
        f"进入第{attempts}轮尾章重写（回退到第{rewind_to}章）",
        {
            "current_phase": "tail_rewrite",
            "total_chapters": max(int(state.get("num_chapters") or 0), int(state.get("end_chapter") or rewind_to)),
            "rewrite_attempts": attempts,
            "remaining_ratio": closure_state_val.get("remaining_ratio"),
            "unresolved_count": closure_state_val.get("unresolved_count"),
        },
    )
    if state.get("task_id"):
        state["checkpoint_store"].save_checkpoint(
            task_id=state["task_id"],
            novel_id=state["novel_id"],
            volume_no=volume_no_for_chapter(state, rewind_to),
            chapter_num=rewind_to,
            node="tail_rewrite",
            state_json={
                "rewrite_attempts": attempts,
                "rewind_to": rewind_to,
                "closure_state": closure_state_val,
            },
        )
    persist_resume_runtime_state(
        state,
        node="tail_rewrite",
        resume_from_chapter=rewind_to,
        effective_end_chapter=int(state.get("end_chapter") or rewind_to),
        effective_total_chapters=int(state.get("end_chapter") or rewind_to),
        tail_rewrite_attempts=attempts,
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
        "closure_state": {**closure_state_val, "action": "continue"},
    }


def node_bridge_chapter(state: GenerationState) -> GenerationState:
    chapter_num = int(state.get("current_chapter") or 1)
    progress(
        state,
        "bridge_chapter",
        chapter_num,
        min(96.2, chapter_progress(state, 0.95)),
        "已追加桥接章节预算，继续推进主线收束",
        {
            "current_phase": "bridge_chapter",
            "total_chapters": max(int(state.get("num_chapters") or 0), int(state.get("end_chapter") or chapter_num)),
        },
    )
    persist_resume_runtime_state(
        state,
        node="bridge_chapter",
        resume_from_chapter=chapter_num,
        effective_end_chapter=int(state.get("end_chapter") or chapter_num),
        effective_total_chapters=int(state.get("end_chapter") or chapter_num),
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
