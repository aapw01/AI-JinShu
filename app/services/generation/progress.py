"""Progress reporting and resume-state persistence helpers."""
from __future__ import annotations

from typing import Any

from app.core.database import SessionLocal
from app.core.llm_usage import snapshot_usage
from app.services.generation.common import logger
from app.services.generation.state import GenerationState
from app.services.task_runtime.checkpoint_repo import update_resume_runtime_state


def volume_no_for_chapter(state: GenerationState, chapter: int) -> int:
    volume_size = max(int(state.get("volume_size") or 30), 1)
    start = state.get("start_chapter") or 1
    offset = max(0, chapter - start)
    return (offset // volume_size) + 1


def progress(state: GenerationState, step: str, chapter: int, pct: float, msg: str, meta: dict | None = None) -> None:
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
            payload.setdefault("volume_no", volume_no_for_chapter(state, chapter))
            payload.setdefault("volume_size", int(state.get("volume_size") or 30))
        cb(step, chapter, pct, msg, payload)


def persist_resume_runtime_state(
    state: GenerationState,
    *,
    node: str,
    resume_from_chapter: int,
    effective_end_chapter: int,
    effective_total_chapters: int | None = None,
    terminal: bool = False,
    tail_rewrite_attempts: int | None = None,
    bridge_attempts: int | None = None,
) -> None:
    creation_task_id = state.get("creation_task_id")
    if not creation_task_id:
        return
    total_chapters = max(
        int(effective_end_chapter),
        int(effective_total_chapters) if effective_total_chapters is not None else 0,
    )
    runtime_state: dict[str, Any] = {
        "node": str(node),
        "resume_from_chapter": int(resume_from_chapter),
        "effective_end_chapter": int(effective_end_chapter),
        "effective_total_chapters": int(total_chapters),
        "tail_rewrite_attempts": int(
            tail_rewrite_attempts if tail_rewrite_attempts is not None else int(state.get("tail_rewrite_attempts") or 0)
        ),
        "bridge_attempts": int(
            bridge_attempts if bridge_attempts is not None else int(state.get("bridge_attempts") or 0)
        ),
        "terminal": bool(terminal),
    }
    db = SessionLocal()
    try:
        update_resume_runtime_state(db, creation_task_id=int(creation_task_id), runtime_state=runtime_state)
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("Failed to persist generation runtime state", exc_info=True)
    finally:
        db.close()


def chapter_progress(state: GenerationState, phase_ratio: float) -> float:
    total = max(state["num_chapters"], 1)
    idx = max(0, state["current_chapter"] - state["start_chapter"])
    base_pct = 20 + (idx / total) * 70
    span = 70 / total
    raw = base_pct + span * phase_ratio
    prev = float(state.get("_last_reported_progress") or 0.0)
    return max(raw, prev)


def is_volume_start(state: GenerationState, chapter: int) -> bool:
    volume_size = max(int(state.get("volume_size") or 30), 1)
    start = state.get("start_chapter") or 1
    return (chapter - start) % volume_size == 0


def closure_phase_mode(remaining_ratio: float) -> str:
    if remaining_ratio > 0.35:
        return "expand"
    if remaining_ratio > 0.15:
        return "converge"
    if remaining_ratio > 0.05:
        return "closing"
    return "finale"
