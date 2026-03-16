"""Generation Celery tasks.

Book-level orchestration dispatches volume-level tasks for long-form runs.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Any

import redis
from sqlalchemy import select

from app.workers.celery_app import app
from app.services.generation.pipeline import run_final_book_review_only, run_generation_pipeline
from app.core.database import SessionLocal
from app.core.logging_config import bind_log_context, log_event
from app.core.llm_usage import begin_usage_session, end_usage_session, snapshot_usage
from app.core.trace import set_trace_id
from app.models.creation_task import CreationTask
from app.models.novel import GenerationTask
from app.services.quota import record_generation_usage
from app.services.scheduler.scheduler_service import (
    dispatch_user_queue_for_user,
    heartbeat_task as heartbeat_creation_task,
    finalize_task as finalize_creation_task,
    get_task_by_id as get_creation_task_by_id,
    mark_task_running as mark_creation_task_running,
    update_task_progress as update_creation_task_progress,
)
from app.services.task_runtime.checkpoint_repo import (
    get_resume_runtime_state,
    get_last_completed_unit,
    mark_unit_completed,
    update_resume_runtime_state,
    update_resume_cursor,
)
from app.services.task_runtime.cursor_service import resume_from_last_completed

from app.services.task_runtime.lease_service import background_heartbeat
from app.core.constants import CREATION_WORKER_HEARTBEAT_SECONDS
from app.services.generation.contracts import OutputContractError
from app.services.generation.status_snapshot import SUBTASK_LABELS, sync_generation_novel_snapshot, write_generation_cache
from app.core.llm_contract import get_last_prompt_meta

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GenerationResumePlan:
    next_chapter: int
    book_start_chapter: int
    book_target_total_chapters: int
    book_effective_end_chapter: int
    current_volume_no: int
    mode: str = "segment_running"


def _task_book_start(payload_data: dict[str, Any], *, fallback_start: int) -> int:
    return int(payload_data.get("book_start_chapter") or payload_data.get("start_chapter") or fallback_start or 1)


def _task_book_total(payload_data: dict[str, Any], *, fallback_total: int) -> int:
    return int(
        payload_data.get("book_target_total_chapters")
        or payload_data.get("original_total_chapters")
        or payload_data.get("num_chapters")
        or fallback_total
        or 0
    )


def _task_book_end(payload_data: dict[str, Any], *, fallback_start: int, fallback_total: int) -> int:
    book_start = _task_book_start(payload_data, fallback_start=fallback_start)
    book_total = max(0, _task_book_total(payload_data, fallback_total=fallback_total))
    return book_start + max(book_total - 1, 0)


def _volume_no_for_next_chapter(*, next_chapter: int, book_start_chapter: int, volume_size: int) -> int:
    size = max(1, int(volume_size or 1))
    start = max(1, int(book_start_chapter or 1))
    chapter = max(start, int(next_chapter or start))
    return max(1, ((chapter - start) // size) + 1)


def _error_meta_from_exc(exc: Exception) -> tuple[str, str, bool]:
    if isinstance(exc, OutputContractError):
        if exc.code == "MODEL_OUTPUT_POLICY_VIOLATION":
            return exc.code, "policy", bool(exc.retryable)
        if exc.code in {"MODEL_OUTPUT_PARSE_FAILED", "MODEL_OUTPUT_SCHEMA_INVALID", "MODEL_OUTPUT_CONTRACT_EXHAUSTED"}:
            return exc.code, "transient", bool(exc.retryable)
        return exc.code, "transient", bool(exc.retryable)
    return "GENERATION_FAILED", "transient", True


def _with_subtask(payload: dict[str, Any]) -> dict[str, Any]:
    step = str(payload.get("step") or payload.get("current_phase") or "").strip()
    if not step:
        return payload
    merged = dict(payload)
    merged.setdefault("subtask_key", step)
    merged.setdefault("subtask_label", SUBTASK_LABELS.get(step, step))
    merged.setdefault("subtask_progress", float(payload.get("progress") or 0.0))
    merged.setdefault(
        "current_subtask",
        {
            "key": merged.get("subtask_key"),
            "label": merged.get("subtask_label"),
            "progress": merged.get("subtask_progress"),
        },
    )
    return merged


def _volume_chunks(start_chapter: int, num_chapters: int, volume_size: int) -> list[tuple[int, int, int]]:
    """Return [(volume_no, chunk_start, chunk_len), ...]."""
    volume_size = max(1, int(volume_size or 30))
    chunks: list[tuple[int, int, int]] = []
    remaining = max(0, int(num_chapters))
    current = int(start_chapter)
    idx = 0
    while remaining > 0:
        chunk_len = min(volume_size, remaining)
        volume_no = (idx // 1) + 1
        chunks.append((volume_no, current, chunk_len))
        current += chunk_len
        remaining -= chunk_len
        idx += 1
    return chunks


def _set_status(
    payload: dict[str, Any],
    *,
    task_public_id: str,
    novel_id: int | str,
    worker_task_id: str | None = None,
    clear_worker_ids: list[str] | None = None,
) -> None:
    write_generation_cache(
        task_public_id=task_public_id,
        novel_id=novel_id,
        payload=_with_subtask(payload),
        worker_task_id=worker_task_id,
        mirror_worker=False,
        clear_worker_ids=clear_worker_ids or [],
        mirror_novel=True,
    )


def _persist_generation_task(
    db,
    task_id: str,
    data: dict[str, Any],
) -> None:
    from app.models.novel import GenerationTask

    gt_stmt = select(GenerationTask).where(GenerationTask.task_id == task_id)
    gt = db.execute(gt_stmt).scalar_one_or_none()
    if not gt:
        return
    gt.status = data.get("status", gt.status)
    gt.run_state = data.get("run_state", gt.run_state or gt.status)
    gt.step = data.get("step", gt.step)
    gt.current_phase = data.get("current_phase", gt.current_phase)
    gt.current_chapter = data.get("current_chapter", gt.current_chapter)
    gt.total_chapters = data.get("total_chapters", gt.total_chapters)
    gt.progress = data.get("progress", gt.progress)
    gt.message = data.get("message", gt.message)
    if "trace_id" in data:
        gt.trace_id = data.get("trace_id")
    gt.token_usage_input = data.get("token_usage_input", gt.token_usage_input)
    gt.token_usage_output = data.get("token_usage_output", gt.token_usage_output)
    gt.estimated_cost = data.get("estimated_cost", gt.estimated_cost)
    if "error" in data:
        gt.error = data.get("error")
    if "error_code" in data:
        gt.error_code = data.get("error_code")
    if "error_category" in data:
        gt.error_category = data.get("error_category")
    if "retryable" in data:
        gt.retryable = 1 if data.get("retryable") else 0
    if "final_report" in data:
        gt.final_report = data["final_report"]
    db.commit()


def _get_task_state(task_id: str) -> tuple[str | None, str | None]:
    """Read authoritative task state from CreationTask (falls back to GenerationTask for legacy)."""
    db = SessionLocal()
    try:
        ct = db.execute(
            select(CreationTask).where(CreationTask.worker_task_id == task_id)
        ).scalar_one_or_none()
        if ct:
            return ct.status, ct.phase
        row = db.execute(select(GenerationTask).where(GenerationTask.task_id == task_id)).scalar_one_or_none()
        if not row:
            return None, None
        return row.status, row.run_state
    finally:
        db.close()


def _get_creation_task_state(task_db_id: int) -> str | None:
    db = SessionLocal()
    try:
        row = get_creation_task_by_id(db, task_id=task_db_id)
        return row.status if row else None
    finally:
        db.close()


def _activate_creation_task(task_db_id: int, *, current_celery_id: str) -> None:
    db = SessionLocal()
    try:
        row = get_creation_task_by_id(db, task_id=task_db_id)
        if not row:
            raise RuntimeError("creation_task_not_found")
        if row.worker_task_id and row.worker_task_id != current_celery_id:
            raise RuntimeError(
                f"worker superseded: creation_task.worker_task_id={row.worker_task_id}, "
                f"current={current_celery_id}"
            )
        state = str(row.status or "")
        if state == "cancelled":
            raise RuntimeError("generation_cancelled")
        if state == "paused":
            raise RuntimeError("generation_paused")
        if state not in {"dispatching", "running"}:
            raise RuntimeError(f"generation_invalid_start:{state or 'unknown'}")
        mark_creation_task_running(db, task_id=task_db_id, worker_task_id=current_celery_id)
        db.commit()
    finally:
        db.close()


def _update_creation_progress(task_db_id: int, *, progress: float, phase: str, message: str) -> None:
    db = SessionLocal()
    try:
        usage = snapshot_usage()
        update_creation_task_progress(
            db,
            task_id=task_db_id,
            progress=progress,
            phase=phase,
            message=message,
            token_usage_input=int(usage.get("input_tokens") or 0),
            token_usage_output=int(usage.get("output_tokens") or 0),
            estimated_cost=float(usage.get("estimated_cost") or 0.0),
        )
        db.commit()
    finally:
        db.close()


def _resolve_completed_usage_totals(
    *,
    row: CreationTask | None,
    start_chapter: int,
    fallback_current: int,
    fallback_total: int,
) -> tuple[int, int, int]:
    current = max(0, int(fallback_current or 0))
    total = max(0, int(fallback_total or 0))
    completed = max(0, current - int(start_chapter or 1) + 1) if current >= int(start_chapter or 1) else 0
    if not row:
        return current, total, completed

    cursor = row.resume_cursor_json if isinstance(row.resume_cursor_json, dict) else {}
    runtime_state = cursor.get("runtime_state") if isinstance(cursor.get("runtime_state"), dict) else {}
    runtime_end = int(runtime_state.get("book_effective_end_chapter") or 0)
    runtime_total = max(int(runtime_state.get("book_target_total_chapters") or 0), runtime_end)
    last_completed = int(cursor.get("last_completed") or 0)

    total = max(total, runtime_total, runtime_end, last_completed)
    current = max(current, runtime_end, last_completed)
    completed = max(0, current - int(start_chapter or 1) + 1) if current >= int(start_chapter or 1) else 0
    return int(current), int(total), int(completed)


def _persist_task_runtime_state(
    task_db_id: int,
    *,
    mode: str,
    volume_no: int,
    segment_start_chapter: int,
    segment_end_chapter: int,
    next_chapter: int,
    book_start_chapter: int,
    book_target_total_chapters: int,
    book_effective_end_chapter: int,
    tail_rewrite_attempts: int = 0,
    bridge_attempts: int = 0,
) -> None:
    db = SessionLocal()
    try:
        update_resume_runtime_state(
            db,
            creation_task_id=task_db_id,
            runtime_state={
                "mode": str(mode),
                "volume_no": int(volume_no),
                "segment_start_chapter": int(segment_start_chapter),
                "segment_end_chapter": int(segment_end_chapter),
                "next_chapter": int(next_chapter),
                "book_effective_end_chapter": int(book_effective_end_chapter),
                "book_target_total_chapters": int(book_target_total_chapters),
                "tail_rewrite_attempts": int(tail_rewrite_attempts or 0),
                "bridge_attempts": int(bridge_attempts or 0),
            },
        )
        update_resume_cursor(
            db,
            creation_task_id=task_db_id,
            unit_type="chapter",
            last_completed_unit_no=max(int(book_start_chapter) - 1, int(next_chapter) - 1),
            next_unit_no=max(int(book_start_chapter), int(next_chapter)),
        )
        db.commit()
    finally:
        db.close()


def _heartbeat_creation(task_db_id: int) -> None:
    db = SessionLocal()
    try:
        heartbeat_creation_task(db, task_id=task_db_id)
        db.commit()
    finally:
        db.close()


def _mark_creation_chapter_completed(task_db_id: int, *, chapter_num: int) -> None:
    db = SessionLocal()
    try:
        mark_unit_completed(
            db,
            creation_task_id=task_db_id,
            unit_type="chapter",
            unit_no=int(chapter_num),
            payload={"phase": "chapter_done", "chapter_num": int(chapter_num)},
        )
        last_completed = get_last_completed_unit(db, creation_task_id=task_db_id, unit_type="chapter")
        next_chapter = int(last_completed or 0) + 1
        update_resume_cursor(
            db,
            creation_task_id=task_db_id,
            unit_type="chapter",
            last_completed_unit_no=last_completed,
            next_unit_no=next_chapter,
        )
        db.commit()
    finally:
        db.close()


def _resolve_generation_resume(
    task_db_id: int,
    *,
    start_chapter: int,
    num_chapters: int,
    volume_size: int,
) -> GenerationResumePlan:
    db = SessionLocal()
    try:
        row = db.execute(select(CreationTask).where(CreationTask.id == task_db_id)).scalar_one_or_none()
        payload_data = row.payload_json if row and isinstance(row.payload_json, dict) else {}
        task_start = int(payload_data.get("start_chapter") or start_chapter or 1)
        task_total = int(payload_data.get("num_chapters") or num_chapters or 0)
        task_end = task_start + max(task_total - 1, 0)
        book_start = _task_book_start(payload_data, fallback_start=task_start)
        book_total = max(1, _task_book_total(payload_data, fallback_total=task_total))
        book_end = max(_task_book_end(payload_data, fallback_start=task_start, fallback_total=task_total), task_end)
        runtime_state = get_resume_runtime_state(db, creation_task_id=task_db_id)
        runtime_mode = str(runtime_state.get("mode") or "").strip() or "segment_running"
        effective_book_end = int(runtime_state.get("book_effective_end_chapter") or book_end)
        next_chapter = int(runtime_state.get("next_chapter") or 0)
        current_volume_no = int(runtime_state.get("volume_no") or 0)
        runtime_segment_end = int(runtime_state.get("segment_end_chapter") or 0)

        if not next_chapter:
            cursor = row.resume_cursor_json if row and isinstance(row.resume_cursor_json, dict) else {}
            next_chapter = int(cursor.get("next") or task_start)
        computed_volume_no = _volume_no_for_next_chapter(
            next_chapter=max(next_chapter, book_start),
            book_start_chapter=book_start,
            volume_size=volume_size,
        )
        if current_volume_no <= 0 or (runtime_segment_end > 0 and next_chapter > runtime_segment_end):
            current_volume_no = computed_volume_no

        if runtime_mode == "completed":
            update_resume_cursor(
                db,
                creation_task_id=task_db_id,
                unit_type="chapter",
                last_completed_unit_no=max(book_start - 1, effective_book_end),
                next_unit_no=max(book_start, effective_book_end + 1),
            )
            db.commit()
            return GenerationResumePlan(
                next_chapter=max(book_start, effective_book_end + 1),
                book_start_chapter=book_start,
                book_target_total_chapters=book_total,
                book_effective_end_chapter=max(book_end, effective_book_end),
                current_volume_no=current_volume_no,
                mode="completed",
            )
        if runtime_mode == "book_final_review_pending":
            update_resume_cursor(
                db,
                creation_task_id=task_db_id,
                unit_type="chapter",
                last_completed_unit_no=max(book_start - 1, effective_book_end),
                next_unit_no=max(book_start, effective_book_end + 1),
            )
            db.commit()
            return GenerationResumePlan(
                next_chapter=max(book_start, effective_book_end + 1),
                book_start_chapter=book_start,
                book_target_total_chapters=book_total,
                book_effective_end_chapter=max(book_end, effective_book_end),
                current_volume_no=current_volume_no,
                mode="book_final_review_pending",
            )
        if runtime_mode == "segment_running":
            resume_from = max(task_start, next_chapter)
            update_resume_cursor(
                db,
                creation_task_id=task_db_id,
                unit_type="chapter",
                last_completed_unit_no=(resume_from - 1) if resume_from > task_start else None,
                next_unit_no=resume_from,
            )
            db.commit()
            return GenerationResumePlan(
                next_chapter=int(resume_from),
                book_start_chapter=book_start,
                book_target_total_chapters=book_total,
                book_effective_end_chapter=max(book_end, effective_book_end),
                current_volume_no=current_volume_no,
                mode="segment_running",
            )
        last_completed = get_last_completed_unit(
            db,
            creation_task_id=task_db_id,
            unit_type="chapter",
            unit_from=task_start,
            unit_to=task_end,
        )
        resume_from = resume_from_last_completed(
            range_start=task_start,
            range_end=task_end,
            last_completed=last_completed,
        )
        update_resume_cursor(
            db,
            creation_task_id=task_db_id,
            unit_type="chapter",
            last_completed_unit_no=last_completed,
            next_unit_no=resume_from,
        )
        db.commit()
        return GenerationResumePlan(
            next_chapter=int(resume_from),
            book_start_chapter=book_start,
            book_target_total_chapters=book_total,
            book_effective_end_chapter=max(book_end, effective_book_end),
            current_volume_no=max(1, ((max(resume_from, book_start) - book_start) // max(int(volume_size or 1), 1)) + 1),
            mode="segment_running",
        )
    finally:
        db.close()


def _check_worker_superseded(task_db_id: int, current_celery_id: str) -> None:
    """Abort if this worker was reclaimed and a new worker dispatched."""
    db = SessionLocal()
    try:
        row = get_creation_task_by_id(db, task_id=task_db_id)
        if not row:
            return
        if row.worker_task_id and row.worker_task_id != current_celery_id:
            raise RuntimeError(
                f"worker superseded: creation_task.worker_task_id={row.worker_task_id}, "
                f"current={current_celery_id}"
            )
    finally:
        db.close()


def _finalize_creation(
    task_db_id: int,
    *,
    status: str,
    phase: str,
    message: str,
    progress: float,
    error_code: str | None = None,
    error_category: str | None = None,
    error_detail: str | None = None,
    result_json: dict[str, Any] | None = None,
) -> None:
    db = SessionLocal()
    user_uuid: str | None = None
    try:
        row = finalize_creation_task(
            db,
            task_id=task_db_id,
            final_status=status,
            phase=phase,
            message=message,
            progress=progress,
            error_code=error_code,
            error_category=error_category,
            error_detail=error_detail,
            result_json=result_json,
        )
        user_uuid = row.user_uuid if row else None
        db.commit()
    finally:
        db.close()
    if user_uuid:
        dispatch_user_queue_for_user(user_uuid=user_uuid)


def _run_volume_generation(
    novel_id: int,
    novel_version_id: int,
    segment_target_chapters: int,
    segment_start_chapter: int,
    parent_task_id: str,
    book_start_chapter: int,
    book_target_total_chapters: int,
    book_effective_end_chapter: int,
    volume_no: int,
    volume_size: int,
    creation_task_id: int | None = None,
    creation_public_id: str | None = None,
    resume_mode: str = "segment_running",
) -> dict[str, Any]:
    """Run one volume chunk under book orchestrator (shared implementation)."""
    from app.core.config import get_settings

    settings = get_settings()
    r = redis.from_url(settings.redis_url)
    cache_task_id = creation_public_id or parent_task_id
    key = f"generation:{cache_task_id}"
    metric_state = {
        "token_usage_input": 0,
        "token_usage_output": 0,
        "estimated_cost": 0.0,
        "trace_id": "",
    }
    try:
        cached_raw = r.get(key)
        if cached_raw:
            cached = json.loads(cached_raw)
            if isinstance(cached, dict):
                metric_state["token_usage_input"] = int(cached.get("token_usage_input") or 0)
                metric_state["token_usage_output"] = int(cached.get("token_usage_output") or 0)
                metric_state["estimated_cost"] = float(cached.get("estimated_cost") or 0.0)
                metric_state["trace_id"] = str(cached.get("trace_id") or "")
    except Exception:
        pass

    def progress_cb(step: str, chapter: int, pct: float, msg: str = "", meta: dict | None = None):
        task_status, run_state = _get_task_state(parent_task_id)
        if creation_task_id is not None:
            try:
                _heartbeat_creation(creation_task_id)
            except Exception:
                pass
            _check_worker_superseded(creation_task_id, parent_task_id)
            creation_state = _get_creation_task_state(creation_task_id)
            if creation_state == "cancelled":
                raise RuntimeError("generation_cancelled")
            if creation_state == "paused":
                raise RuntimeError("generation_paused")
            if step == "chapter_done" and int(chapter or 0) > 0:
                try:
                    _mark_creation_chapter_completed(creation_task_id, chapter_num=int(chapter))
                except Exception:
                    logger.exception("failed to mark generation checkpoint task=%s chapter=%s", creation_task_id, chapter)
        if task_status == "cancelled" or run_state == "cancelled":
            raise RuntimeError("generation_cancelled")
        paused_iterations = 0
        max_paused_iterations = 3600
        while task_status == "paused" or run_state == "paused":
            paused_iterations += 1
            if paused_iterations > max_paused_iterations:
                raise RuntimeError("generation_paused_timeout")
            payload_pause = {
                "status": "paused",
                "run_state": "paused",
                "step": "paused",
                "current_phase": "paused",
                "current_subtask": {"key": "paused", "label": "任务已暂停", "progress": round(pct, 2)},
                "current_chapter": chapter,
                "total_chapters": book_effective_end_chapter,
                "progress": round(pct, 2),
                "message": "任务暂停中，等待恢复",
                "trace_id": metric_state["trace_id"],
            }
            _set_status(
                payload_pause,
                task_public_id=cache_task_id,
                novel_id=novel_id,
                worker_task_id=parent_task_id if creation_public_id else None,
            )
            if creation_task_id is not None and paused_iterations % 5 == 0:
                try:
                    _heartbeat_creation(creation_task_id)
                except Exception:
                    pass
            import time

            time.sleep(1.0)
            task_status, run_state = _get_task_state(parent_task_id)
            if task_status == "cancelled" or run_state == "cancelled":
                raise RuntimeError("generation_cancelled")
        meta = meta or {}
        # Preserve last known usage/cost to avoid resetting to 0 on non-metric phases.
        if meta.get("token_usage_input") is not None:
            metric_state["token_usage_input"] = int(meta.get("token_usage_input") or 0)
        if meta.get("token_usage_output") is not None:
            metric_state["token_usage_output"] = int(meta.get("token_usage_output") or 0)
        if meta.get("estimated_cost") is not None:
            metric_state["estimated_cost"] = float(meta.get("estimated_cost") or 0.0)
        effective_total_chapters = max(int(meta.get("total_chapters") or 0), int(book_effective_end_chapter or 0))
        global_pct = max(0.0, min(100.0, float(pct or 0.0)))
        payload = {
            "status": meta.get("status", "running"),
            "run_state": "running",
            "step": step,
            "current_phase": meta.get("current_phase", step),
            "current_subtask": {
                "key": step,
                "label": SUBTASK_LABELS.get(step, step),
                "progress": round(global_pct, 2),
            },
            "current_chapter": chapter,
            "total_chapters": effective_total_chapters or int(book_effective_end_chapter or 0),
            "progress": round(global_pct, 2),
            "token_usage_input": metric_state["token_usage_input"],
            "token_usage_output": metric_state["token_usage_output"],
            "estimated_cost": metric_state["estimated_cost"],
            "volume_no": volume_no,
            "volume_size": volume_size,
            "message": msg,
            "pacing_mode": meta.get("pacing_mode"),
            "low_progress_streak": meta.get("low_progress_streak"),
            "progress_signal": meta.get("progress_signal"),
            "decision_state": meta.get("decision_state"),
            "trace_id": metric_state["trace_id"],
        }
        _set_status(
            payload,
            task_public_id=cache_task_id,
            novel_id=novel_id,
            worker_task_id=parent_task_id if creation_public_id else None,
        )
        if creation_task_id is not None:
            try:
                _update_creation_progress(
                    creation_task_id,
                    progress=round(global_pct, 2),
                    phase=meta.get("current_phase", step),
                    message=msg or SUBTASK_LABELS.get(step, step),
                )
            except Exception:
                pass

    if resume_mode == "book_final_review_pending":
        run_final_book_review_only(
            novel_id=novel_id,
            novel_version_id=novel_version_id,
            book_start_chapter=book_start_chapter,
            book_target_total_chapters=book_target_total_chapters,
            book_effective_end_chapter=book_effective_end_chapter,
            volume_no=volume_no,
            progress_callback=progress_cb,
            task_id=parent_task_id,
            creation_task_id=creation_task_id,
        )
    else:
        run_generation_pipeline(
            novel_id=novel_id,
            novel_version_id=novel_version_id,
            segment_target_chapters=segment_target_chapters,
            segment_start_chapter=segment_start_chapter,
            book_start_chapter=book_start_chapter,
            book_target_total_chapters=book_target_total_chapters,
            book_effective_end_chapter=book_effective_end_chapter,
            volume_no=volume_no,
            progress_callback=progress_cb,
            task_id=parent_task_id,
            creation_task_id=creation_task_id,
        )
    return {"ok": True, "volume_no": volume_no, "start": segment_start_chapter, "num_chapters": segment_target_chapters}


@app.task(bind=True, acks_late=True, reject_on_worker_lost=True)
def submit_volume_generation_task(
    self,
    novel_id: int,
    novel_version_id: int,
    segment_target_chapters: int,
    segment_start_chapter: int,
    parent_task_id: str,
    book_start_chapter: int,
    book_target_total_chapters: int,
    book_effective_end_chapter: int,
    volume_no: int,
    volume_size: int,
    creation_task_id: int | None = None,
    creation_public_id: str | None = None,
    resume_mode: str = "segment_running",
) -> dict[str, Any]:
    """Run one volume chunk as an independent task."""
    return _run_volume_generation(
        novel_id=novel_id,
        novel_version_id=novel_version_id,
        segment_target_chapters=segment_target_chapters,
        segment_start_chapter=segment_start_chapter,
        parent_task_id=parent_task_id,
        book_start_chapter=book_start_chapter,
        book_target_total_chapters=book_target_total_chapters,
        book_effective_end_chapter=book_effective_end_chapter,
        volume_no=volume_no,
        volume_size=volume_size,
        creation_task_id=creation_task_id,
        creation_public_id=creation_public_id,
        resume_mode=resume_mode,
    )


@app.task(bind=True, acks_late=True, reject_on_worker_lost=True)
def submit_book_generation_task(
    self,
    novel_id: str,
    novel_version_id: int,
    num_chapters: int,
    start_chapter: int,
    parent_task_id: str | None = None,
    trace_id: str | None = None,
    creation_task_id: int | None = None,
):
    """Book-level orchestrator: run book segments sequentially, then final review once."""
    from app.core.database import SessionLocal
    from app.models.novel import Novel

    task_id = self.request.id
    set_trace_id(trace_id)
    begin_usage_session(f"generation:{task_id}")
    db: Any = None
    resume_mode = "segment_running"
    creation_public_id: str | None = None
    book_start_chapter = int(start_chapter or 1)
    book_target_total_chapters = int(num_chapters or 0)
    book_effective_end_chapter = int(start_chapter or 1) + max(int(num_chapters or 0) - 1, 0)
    next_chapter = int(start_chapter or 1)
    current_volume_no = 1
    volume_size = 30

    data: dict[str, Any] = {
        "status": "running",
        "run_state": "running",
        "step": "queued",
        "current_phase": "queued",
        "current_subtask": {"key": "queued", "label": SUBTASK_LABELS.get("queued"), "progress": 0},
        "progress": 0,
        "current_chapter": int(next_chapter),
        "total_chapters": int(book_target_total_chapters),
        "novel_version_id": int(novel_version_id),
        "message": "任务已入队",
        "trace_id": trace_id or "",
    }

    hb_ctx = None
    _worker_superseded = False
    try:
        db = SessionLocal()
        try:
            novel = db.execute(select(Novel).where(Novel.id == novel_id)).scalar_one_or_none()
            volume_size = int(((novel.config or {}).get("volume_size") or 30)) if novel else 30
            if creation_task_id is not None:
                ct = db.execute(select(CreationTask).where(CreationTask.id == creation_task_id)).scalar_one_or_none()
                if not ct:
                    raise RuntimeError("creation_task_not_found")
                creation_public_id = ct.public_id
                if isinstance(ct.payload_json, dict):
                    payload_data = dict(ct.payload_json)
                    trace_id = trace_id or str(payload_data.get("trace_id") or "") or trace_id
                    book_start_chapter = _task_book_start(payload_data, fallback_start=book_start_chapter)
                    book_target_total_chapters = _task_book_total(payload_data, fallback_total=book_target_total_chapters)
                    book_effective_end_chapter = max(
                        book_effective_end_chapter,
                        _task_book_end(
                            payload_data,
                            fallback_start=book_start_chapter,
                            fallback_total=book_target_total_chapters,
                        ),
                    )
        finally:
            db.close()
            db = None

        if creation_task_id is not None:
            _activate_creation_task(creation_task_id, current_celery_id=task_id)
            resume_plan = _resolve_generation_resume(
                creation_task_id,
                start_chapter=int(start_chapter),
                num_chapters=int(num_chapters),
                volume_size=volume_size,
            )
            next_chapter = int(resume_plan.next_chapter)
            book_start_chapter = int(resume_plan.book_start_chapter)
            book_target_total_chapters = int(resume_plan.book_target_total_chapters)
            book_effective_end_chapter = int(resume_plan.book_effective_end_chapter)
            current_volume_no = int(resume_plan.current_volume_no or 1)
            resume_mode = str(resume_plan.mode or "segment_running")
            if resume_mode == "completed" or next_chapter > book_effective_end_chapter:
                done_data = {
                    "status": "completed",
                    "run_state": "completed",
                    "step": "done",
                    "current_phase": "completed",
                    "current_subtask": {"key": "done", "label": SUBTASK_LABELS.get("done"), "progress": 100},
                    "progress": 100,
                    "current_chapter": max(0, int(book_effective_end_chapter)),
                    "total_chapters": int(max(book_target_total_chapters, book_effective_end_chapter)),
                    "volume_no": 1,
                    "volume_size": 1,
                    "message": "任务已完成（已无待处理章节）",
                    "trace_id": trace_id or "",
                }
                _set_status(
                    done_data,
                    task_public_id=creation_public_id or task_id,
                    novel_id=novel_id,
                    worker_task_id=task_id if creation_public_id else None,
                )
                _finalize_creation(
                    creation_task_id,
                    status="completed",
                    phase="completed",
                    message=str(done_data["message"]),
                    progress=100.0,
                )
                return task_id

        hb_ctx = background_heartbeat(creation_task_id, heartbeat_fn=_heartbeat_creation, interval_seconds=CREATION_WORKER_HEARTBEAT_SECONDS)
        hb_ctx.__enter__()

        db = SessionLocal()
        try:
            ct = db.execute(
                select(CreationTask).where(CreationTask.id == creation_task_id)
            ).scalar_one_or_none()
            ct_status = ct.status if ct else None
            ct_phase = ct.phase if ct else None
            trace_id = trace_id or (((ct.payload_json or {}).get("trace_id")) if ct else None) or ""
        finally:
            db.close()
            db = None

        if trace_id:
            set_trace_id(trace_id)
        with bind_log_context(trace_id=trace_id, task_id=task_id, novel_id=novel_id):
            log_event(
                logger,
                "generation.task.started",
                task_id=task_id,
                novel_id=novel_id,
                run_state="running",
                chapter_num=next_chapter,
                total_chapters=book_target_total_chapters,
            )
        if ct_status in {"completed", "cancelled"}:
            logger.info("Skip replay for task %s because creation_task status=%s", task_id, ct_status)
            data.update(
                {
                    "status": str(ct_status),
                    "run_state": str(ct_phase or ct_status),
                    "step": "skipped",
                    "current_phase": "skipped",
                    "message": f"跳过重放：任务状态为 {ct_status}",
                    "trace_id": trace_id,
                }
            )
            return task_id

        data = {
            "status": "running",
            "run_state": "running",
            "step": "book_orchestrator",
            "current_phase": "book_planning",
            "current_subtask": {"key": "book_planning", "label": SUBTASK_LABELS.get("book_planning"), "progress": 5},
            "current_chapter": next_chapter,
            "total_chapters": max(book_target_total_chapters, book_effective_end_chapter),
            "novel_version_id": int(novel_version_id),
            "progress": 5,
            "volume_no": current_volume_no,
            "volume_size": volume_size,
            "message": "总控任务已启动，按卷顺序执行",
            "trace_id": trace_id,
        }
        _set_status(
            data,
            task_public_id=creation_public_id or task_id,
            novel_id=novel_id,
            worker_task_id=task_id if creation_public_id else None,
        )
        db = SessionLocal()
        try:
            _persist_generation_task(db, task_id, data)
        finally:
            db.close()
            db = None

        while resume_mode == "segment_running" and next_chapter <= book_effective_end_chapter:
            segment_start_chapter = int(next_chapter)
            segment_end_chapter = min(segment_start_chapter + max(volume_size - 1, 0), book_effective_end_chapter)
            segment_target_chapters = max(1, segment_end_chapter - segment_start_chapter + 1)
            announce = {
                "status": "running",
                "run_state": "running",
                "step": "volume_dispatch",
                "current_phase": "volume_dispatch",
                "current_subtask": {"key": "volume_dispatch", "label": SUBTASK_LABELS.get("volume_dispatch")},
                "current_chapter": segment_start_chapter,
                "total_chapters": max(book_target_total_chapters, book_effective_end_chapter),
                "novel_version_id": int(novel_version_id),
                "progress": round(data.get("progress") or 5, 2),
                "volume_no": current_volume_no,
                "volume_size": volume_size,
                "message": f"开始第{current_volume_no}卷（第{segment_start_chapter}章起，共{segment_target_chapters}章）",
                "trace_id": trace_id,
            }
            _set_status(
                announce,
                task_public_id=creation_public_id or task_id,
                novel_id=novel_id,
                worker_task_id=task_id if creation_public_id else None,
            )
            db = SessionLocal()
            try:
                _persist_generation_task(db, task_id, announce)
            finally:
                db.close()
                db = None
            if creation_task_id is not None:
                _persist_task_runtime_state(
                    creation_task_id,
                    mode="segment_running",
                    volume_no=current_volume_no,
                    segment_start_chapter=segment_start_chapter,
                    segment_end_chapter=segment_end_chapter,
                    next_chapter=segment_start_chapter,
                    book_start_chapter=book_start_chapter,
                    book_target_total_chapters=book_target_total_chapters,
                    book_effective_end_chapter=book_effective_end_chapter,
                )
            _run_volume_generation(
                novel_id=int(novel_id),
                novel_version_id=int(novel_version_id),
                segment_target_chapters=segment_target_chapters,
                segment_start_chapter=segment_start_chapter,
                parent_task_id=task_id,
                book_start_chapter=book_start_chapter,
                book_target_total_chapters=book_target_total_chapters,
                book_effective_end_chapter=book_effective_end_chapter,
                volume_no=current_volume_no,
                volume_size=volume_size,
                creation_task_id=creation_task_id,
                creation_public_id=creation_public_id,
                resume_mode=resume_mode,
            )
            if creation_task_id is None:
                next_chapter = segment_end_chapter + 1
                current_volume_no += 1
                continue

            db = SessionLocal()
            try:
                row = db.execute(select(CreationTask).where(CreationTask.id == creation_task_id)).scalar_one_or_none()
                payload_data = row.payload_json if row and isinstance(row.payload_json, dict) else {}
                cursor = row.resume_cursor_json if row and isinstance(row.resume_cursor_json, dict) else {}
                runtime_state = cursor.get("runtime_state") if isinstance(cursor.get("runtime_state"), dict) else {}
                book_start_chapter = _task_book_start(payload_data, fallback_start=book_start_chapter)
                book_target_total_chapters = _task_book_total(payload_data, fallback_total=book_target_total_chapters)
                book_effective_end_chapter = max(
                    book_effective_end_chapter,
                    int(runtime_state.get("book_effective_end_chapter") or 0),
                    _task_book_end(
                        payload_data,
                        fallback_start=book_start_chapter,
                        fallback_total=book_target_total_chapters,
                    ),
                )
                next_chapter = int(runtime_state.get("next_chapter") or cursor.get("next") or (segment_end_chapter + 1))
                runtime_segment_end = int(runtime_state.get("segment_end_chapter") or 0)
                computed_volume_no = _volume_no_for_next_chapter(
                    next_chapter=next_chapter,
                    book_start_chapter=book_start_chapter,
                    volume_size=volume_size,
                )
                current_volume_no = int(runtime_state.get("volume_no") or 0)
                if current_volume_no <= 0 or (runtime_segment_end > 0 and next_chapter > runtime_segment_end):
                    current_volume_no = computed_volume_no
                resume_mode = str(runtime_state.get("mode") or "segment_running")
            finally:
                db.close()

        if creation_task_id is not None and resume_mode != "completed":
            _persist_task_runtime_state(
                creation_task_id,
                mode="book_final_review_pending",
                volume_no=max(1, current_volume_no if next_chapter <= book_effective_end_chapter else current_volume_no - 1),
                segment_start_chapter=max(book_start_chapter, next_chapter),
                segment_end_chapter=max(book_start_chapter, book_effective_end_chapter),
                next_chapter=max(book_effective_end_chapter + 1, next_chapter),
                book_start_chapter=book_start_chapter,
                book_target_total_chapters=book_target_total_chapters,
                book_effective_end_chapter=book_effective_end_chapter,
            )

        if resume_mode != "completed":
            _run_volume_generation(
                novel_id=int(novel_id),
                novel_version_id=int(novel_version_id),
                segment_target_chapters=max(1, book_effective_end_chapter - book_start_chapter + 1),
                segment_start_chapter=book_start_chapter,
                parent_task_id=task_id,
                book_start_chapter=book_start_chapter,
                book_target_total_chapters=book_target_total_chapters,
                book_effective_end_chapter=book_effective_end_chapter,
                volume_no=max(1, current_volume_no if next_chapter <= book_effective_end_chapter else current_volume_no - 1),
                volume_size=volume_size,
                creation_task_id=creation_task_id,
                creation_public_id=creation_public_id,
                resume_mode="book_final_review_pending",
            )

        data = {
            "status": "completed",
            "run_state": "completed",
            "step": "done",
            "current_phase": "completed",
            "current_subtask": {"key": "done", "label": SUBTASK_LABELS.get("done"), "progress": 100},
            "progress": 100,
            "current_chapter": book_effective_end_chapter,
            "total_chapters": max(book_target_total_chapters, book_effective_end_chapter),
            "novel_version_id": int(novel_version_id),
            "volume_no": max(1, current_volume_no if next_chapter <= book_effective_end_chapter else current_volume_no - 1),
            "volume_size": volume_size,
            "message": "总控任务完成",
            "trace_id": trace_id,
        }
    except Exception as e:
        err = str(e)
        _worker_superseded = "worker superseded" in err
        if _worker_superseded:
            logger.warning("Worker superseded, exiting gracefully: %s", err)
        else:
            logger.error(f"Book generation failed for novel {novel_id}: {e}")
            is_paused = err == "generation_paused"
            is_cancelled = err == "generation_cancelled"
            status = "paused" if is_paused else ("cancelled" if is_cancelled else "failed")
            error_code, error_category, retryable = _error_meta_from_exc(e)
            data = {
                "status": status,
                "run_state": status,
                "step": status,
                "current_phase": status,
                "current_subtask": {
                    "key": status,
                    "label": "任务已暂停" if is_paused else ("任务已取消" if is_cancelled else SUBTASK_LABELS.get("failed")),
                    "progress": 0,
                },
                "progress": 0,
                "total_chapters": max(book_target_total_chapters, book_effective_end_chapter),
                "novel_version_id": int(novel_version_id),
                "error": None if (is_paused or is_cancelled) else str(e),
                "error_code": None if (is_paused or is_cancelled) else error_code,
                "error_category": None if (is_paused or is_cancelled) else error_category,
                "retryable": False if (is_paused or is_cancelled) else retryable,
                "message": "任务暂停并等待恢复" if is_paused else ("任务已取消" if is_cancelled else "总控任务失败"),
                "trace_id": trace_id,
            }
    finally:
        if hb_ctx is not None:
            hb_ctx.__exit__(None, None, None)
        if _worker_superseded:
            end_usage_session()
            return task_id
        usage = snapshot_usage()
        data["token_usage_input"] = int(usage.get("input_tokens") or data.get("token_usage_input") or 0)
        data["token_usage_output"] = int(usage.get("output_tokens") or data.get("token_usage_output") or 0)
        data["estimated_cost"] = float(usage.get("estimated_cost") or data.get("estimated_cost") or 0.0)
        prompt_meta = get_last_prompt_meta() or {}
        status = str(data.get("status") or "")
        usage_summary = {
            "token_usage_input": int(data.get("token_usage_input") or 0),
            "token_usage_output": int(data.get("token_usage_output") or 0),
            "estimated_cost": float(data.get("estimated_cost") or 0.0),
            "start_chapter": int(book_start_chapter or 1),
            "current_chapter": int(data.get("current_chapter") or 0),
            "total_chapters": int(data.get("total_chapters") or 0),
            "completed_chapters": max(
                0,
                int(data.get("current_chapter") or 0) - int(book_start_chapter or 1) + 1,
            ) if status == "completed" else 0,
            "usage_calls": int((usage or {}).get("calls") or 0),
            "usage_stages": (usage or {}).get("stages") or {},
            "prompt_version": str(prompt_meta.get("prompt_version") or "v2"),
            "prompt_hash": prompt_meta.get("prompt_hash"),
            "prompt_template": prompt_meta.get("prompt_template"),
        }
        db = SessionLocal()
        try:
            if creation_task_id is not None and data.get("status") in {"failed", "paused"}:
                try:
                    last = get_last_completed_unit(
                        db,
                        creation_task_id=creation_task_id,
                        unit_type="chapter",
                    )
                    if last is not None:
                        data["current_chapter"] = int(last) + 1
                        update_resume_cursor(
                            db,
                            creation_task_id=creation_task_id,
                            unit_type="chapter",
                            last_completed_unit_no=last,
                            next_unit_no=int(last) + 1,
                        )
                except Exception:
                    logger.warning("Failed to update resume_cursor on task failure", exc_info=True)
            if creation_task_id is not None:
                row = db.execute(select(CreationTask).where(CreationTask.id == creation_task_id)).scalar_one_or_none()
                if row:
                    if status == "completed":
                        effective_current, effective_total, completed_chapters = _resolve_completed_usage_totals(
                            row=row,
                            start_chapter=int(book_start_chapter or 1),
                            fallback_current=int(data.get("current_chapter") or 0),
                            fallback_total=int(data.get("total_chapters") or 0),
                        )
                        data["current_chapter"] = effective_current
                        data["total_chapters"] = effective_total
                        usage_summary["current_chapter"] = effective_current
                        usage_summary["total_chapters"] = effective_total
                        usage_summary["completed_chapters"] = completed_chapters
                    row.result_json = usage_summary
            _persist_generation_task(db, task_id, data)
            record_generation_usage(db, task_id=task_id, novel_id=int(novel_id), source="generation")
            db.commit()
            log_event(
                logger,
                "generation.task.finalized",
                task_id=task_id,
                novel_id=novel_id,
                run_state=data.get("run_state"),
                status_code=data.get("status"),
                error_code=data.get("error_code"),
                error_category=data.get("error_category"),
                retryable=data.get("retryable"),
            )
        except Exception as e:  # pragma: no cover
            logger.error(f"Failed to update final status in DB: {e}")
            db.rollback()
        finally:
            db.close()
        _set_status(
            data,
            task_public_id=creation_public_id or task_id,
            novel_id=novel_id,
            worker_task_id=task_id if creation_public_id else None,
        )
        if creation_task_id is not None:
            try:
                if status == "completed":
                    _finalize_creation(
                        creation_task_id,
                        status="completed",
                        phase="completed",
                        message=str(data.get("message") or "任务完成"),
                        progress=100.0,
                        result_json=usage_summary,
                    )
                elif status == "paused":
                    _finalize_creation(
                        creation_task_id,
                        status="paused",
                        phase="paused",
                        message=str(data.get("message") or "任务已暂停"),
                        progress=float(data.get("progress") or 0),
                        result_json=usage_summary,
                    )
                elif status == "cancelled":
                    _finalize_creation(
                        creation_task_id,
                        status="cancelled",
                        phase="cancelled",
                        message=str(data.get("message") or "任务已取消"),
                        progress=float(data.get("progress") or 0),
                        result_json=usage_summary,
                    )
                else:
                    _finalize_creation(
                        creation_task_id,
                        status="failed",
                        phase="failed",
                        message=str(data.get("message") or "任务失败"),
                        progress=float(data.get("progress") or 0),
                        error_code=str(data.get("error_code") or "GENERATION_FAILED"),
                        error_category=str(data.get("error_category") or "transient"),
                        error_detail=str(data.get("error") or ""),
                        result_json=usage_summary,
                    )
            except Exception:
                logger.exception("Failed to finalize creation task %s", creation_task_id)
        if creation_task_id is not None:
            sync_db = SessionLocal()
            try:
                sync_generation_novel_snapshot(sync_db, novel_id=int(novel_id))
            finally:
                sync_db.close()
        end_usage_session()

    return task_id


# Keep old task name as public API (directly mapped to new book orchestrator).
submit_generation_task = submit_book_generation_task
