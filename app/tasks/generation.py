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
    update_resume_cursor,
)
from app.services.task_runtime.cursor_service import resume_from_last_completed

from app.services.task_runtime.lease_service import background_heartbeat
from app.core.constants import CREATION_WORKER_HEARTBEAT_SECONDS
from app.services.generation.contracts import OutputContractError
from app.core.llm_contract import get_last_prompt_meta

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GenerationResumePlan:
    start_chapter: int
    num_chapters: int
    display_total_chapters: int
    mode: str = "chapter_range"


def _error_meta_from_exc(exc: Exception) -> tuple[str, str, bool]:
    if isinstance(exc, OutputContractError):
        if exc.code == "MODEL_OUTPUT_POLICY_VIOLATION":
            return exc.code, "policy", bool(exc.retryable)
        if exc.code in {"MODEL_OUTPUT_PARSE_FAILED", "MODEL_OUTPUT_SCHEMA_INVALID", "MODEL_OUTPUT_CONTRACT_EXHAUSTED"}:
            return exc.code, "transient", bool(exc.retryable)
        return exc.code, "transient", bool(exc.retryable)
    return "GENERATION_FAILED", "transient", True


SUBTASK_LABELS: dict[str, str] = {
    "queued": "任务已入队",
    "prewrite": "预写准备",
    "outline_ready": "大纲就绪",
    "chapter_writing": "章节写作中",
    "chapter_review": "章节审校中",
    "chapter_finalizing": "章节定稿中",
    "full_book_review": "全书终审中",
    "completed": "任务已完成",
    "failed": "任务失败",
    "cancelled": "任务已取消",
    "book_planning": "拆分卷任务",
    "volume_dispatch": "调度卷任务",
    "constitution": "生成创作宪法",
    "specify_plan_tasks": "生成规格/计划/任务分解",
    "full_outline_ready": "全书大纲已完成",
    "outline_waiting_confirmation": "等待大纲确认",
    "volume_replan": "分卷策略重规划",
    "closure_gate": "收官完整性检查",
    "bridge_chapter": "追加桥接章节",
    "tail_rewrite": "尾章重写补完",
    "context": "加载上下文",
    "consistency": "一致性检查",
    "chapter_blocked": "一致性未通过（跳过）",
    "beats": "生成节拍卡",
    "writer": "写作章节草稿",
    "reviewer": "章节质量审校",
    "revise": "按反馈修订",
    "rollback_rerun": "回滚并重跑",
    "finalizer": "章节定稿",
    "memory_update": "更新记忆与摘要",
    "chapter_done": "章节完成",
    "final_book_review": "全书终审",
    "done": "全书完成",
}


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


def _set_status(r: redis.Redis, key: str, novel_key: str, payload: dict[str, Any]) -> None:
    data = _with_subtask(payload)
    status = str(data.get("status") or "")
    ttl = 172800 if status in {"completed", "failed", "cancelled"} else 86400
    encoded = json.dumps(data, ensure_ascii=False)
    r.setex(key, ttl, encoded)
    r.setex(novel_key, ttl, encoded)


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


def _mark_creation_running(task_db_id: int) -> None:
    db = SessionLocal()
    try:
        mark_creation_task_running(db, task_id=task_db_id)
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
    runtime_end = int(runtime_state.get("effective_end_chapter") or 0)
    runtime_total = int(runtime_state.get("effective_total_chapters") or 0)
    last_completed = int(cursor.get("last_completed") or 0)

    total = max(total, runtime_total, runtime_end, last_completed)
    current = max(current, runtime_end, last_completed)
    completed = max(0, current - int(start_chapter or 1) + 1) if current >= int(start_chapter or 1) else 0
    return int(current), int(total), int(completed)


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
) -> GenerationResumePlan:
    db = SessionLocal()
    try:
        range_start = int(start_chapter)
        range_end = range_start + max(0, int(num_chapters)) - 1
        row = db.execute(select(CreationTask).where(CreationTask.id == task_db_id)).scalar_one_or_none()
        payload_data = row.payload_json if row and isinstance(row.payload_json, dict) else {}
        display_total = int(payload_data.get("original_total_chapters") or payload_data.get("num_chapters") or num_chapters or 0)
        runtime_state = get_resume_runtime_state(db, creation_task_id=task_db_id)
        runtime_node = str(runtime_state.get("node") or "").strip()
        runtime_end = int(runtime_state.get("effective_end_chapter") or range_end)
        runtime_total = int(runtime_state.get("effective_total_chapters") or display_total or max(0, runtime_end))
        runtime_resume = int(runtime_state.get("resume_from_chapter") or range_start)
        if runtime_state.get("terminal"):
            update_resume_cursor(
                db,
                creation_task_id=task_db_id,
                unit_type="chapter",
                last_completed_unit_no=max(range_start - 1, runtime_end),
                next_unit_no=max(range_start, runtime_end + 1),
            )
            db.commit()
            return GenerationResumePlan(
                start_chapter=max(range_start, runtime_end + 1),
                num_chapters=0,
                display_total_chapters=max(display_total, runtime_total),
                mode="complete",
            )
        if runtime_node == "final_book_review":
            review_total = max(display_total, runtime_total, runtime_end)
            review_start = max(1, runtime_end - review_total + 1)
            review_num = max(0, review_total - review_start + 1)
            update_resume_cursor(
                db,
                creation_task_id=task_db_id,
                unit_type="chapter",
                last_completed_unit_no=max(range_start - 1, runtime_end),
                next_unit_no=max(range_start, runtime_end + 1),
            )
            db.commit()
            return GenerationResumePlan(
                start_chapter=review_start,
                num_chapters=review_num,
                display_total_chapters=review_total,
                mode="final_book_review",
            )
        if runtime_node in {"chapter_loop", "tail_rewrite", "bridge_chapter"}:
            resume_from = max(range_start, runtime_resume)
            effective_end = max(range_end, runtime_end)
            effective_num = max(0, effective_end - resume_from + 1)
            update_resume_cursor(
                db,
                creation_task_id=task_db_id,
                unit_type="chapter",
                last_completed_unit_no=(resume_from - 1) if resume_from > range_start else None,
                next_unit_no=resume_from,
            )
            db.commit()
            return GenerationResumePlan(
                start_chapter=int(resume_from),
                num_chapters=int(effective_num),
                display_total_chapters=max(display_total, runtime_total, effective_end),
            )
        last_completed = get_last_completed_unit(
            db,
            creation_task_id=task_db_id,
            unit_type="chapter",
            unit_from=range_start,
            unit_to=range_end,
        )
        resume_from = resume_from_last_completed(
            range_start=range_start,
            range_end=range_end,
            last_completed=last_completed,
        )
        effective_num = max(0, range_end - resume_from + 1)
        update_resume_cursor(
            db,
            creation_task_id=task_db_id,
            unit_type="chapter",
            last_completed_unit_no=last_completed,
            next_unit_no=resume_from,
        )
        db.commit()
        return GenerationResumePlan(
            start_chapter=int(resume_from),
            num_chapters=int(effective_num),
            display_total_chapters=max(display_total, range_end),
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
    try:
        finalize_creation_task(
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
        db.commit()
    finally:
        db.close()


def _run_volume_generation(
    novel_id: int,
    novel_version_id: int,
    chunk_chapters: int,
    chunk_start: int,
    parent_task_id: str,
    total_chapters: int,
    total_start_chapter: int,
    volume_no: int,
    volume_size: int,
    creation_task_id: int | None = None,
    resume_mode: str = "chapter_range",
) -> dict[str, Any]:
    """Run one volume chunk under book orchestrator (shared implementation)."""
    from app.core.config import get_settings

    settings = get_settings()
    r = redis.from_url(settings.redis_url)
    key = f"generation:{parent_task_id}"
    novel_key = f"generation:novel:{novel_id}"
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
                "total_chapters": total_chapters,
                "progress": round(pct, 2),
                "message": "任务暂停中，等待恢复",
                "trace_id": metric_state["trace_id"],
            }
            _set_status(r, key, novel_key, payload_pause)
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
        effective_total_chapters = int(meta.get("total_chapters") or total_chapters or 0)
        # Map chunk progress to global progress window [20, 95].
        chunk_ratio = max(0.0, min(1.0, pct / 100.0))
        chunks = max(1, (effective_total_chapters + volume_size - 1) // volume_size) if effective_total_chapters > 0 else 1
        global_pct = 20 + ((volume_no - 1 + chunk_ratio) / chunks) * 75
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
            "total_chapters": effective_total_chapters or total_chapters,
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
        _set_status(r, key, novel_key, payload)
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

    if resume_mode == "final_book_review":
        run_final_book_review_only(
            novel_id=novel_id,
            novel_version_id=novel_version_id,
            num_chapters=chunk_chapters,
            start_chapter=chunk_start,
            progress_callback=progress_cb,
            task_id=parent_task_id,
            creation_task_id=creation_task_id,
        )
    else:
        run_generation_pipeline(
            novel_id=novel_id,
            novel_version_id=novel_version_id,
            num_chapters=chunk_chapters,
            start_chapter=chunk_start,
            progress_callback=progress_cb,
            task_id=parent_task_id,
            creation_task_id=creation_task_id,
        )
    return {"ok": True, "volume_no": volume_no, "start": chunk_start, "num_chapters": chunk_chapters}


@app.task(bind=True, acks_late=True, reject_on_worker_lost=True)
def submit_volume_generation_task(
    self,
    novel_id: int,
    novel_version_id: int,
    chunk_chapters: int,
    chunk_start: int,
    parent_task_id: str,
    total_chapters: int,
    total_start_chapter: int,
    volume_no: int,
    volume_size: int,
    creation_task_id: int | None = None,
    resume_mode: str = "chapter_range",
) -> dict[str, Any]:
    """Run one volume chunk as an independent task."""
    return _run_volume_generation(
        novel_id=novel_id,
        novel_version_id=novel_version_id,
        chunk_chapters=chunk_chapters,
        chunk_start=chunk_start,
        parent_task_id=parent_task_id,
        total_chapters=total_chapters,
        total_start_chapter=total_start_chapter,
        volume_no=volume_no,
        volume_size=volume_size,
        creation_task_id=creation_task_id,
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
    """Book-level orchestrator: split into volume tasks and execute sequentially."""
    from app.core.config import get_settings
    from app.core.database import SessionLocal
    from app.models.novel import Novel

    settings = get_settings()
    r = redis.from_url(settings.redis_url)
    task_id = self.request.id
    set_trace_id(trace_id)
    begin_usage_session(f"generation:{task_id}")
    key = f"generation:{task_id}"
    novel_key = f"generation:novel:{novel_id}"
    db: Any = None
    resume_mode = "chapter_range"

    display_total_chapters = int(num_chapters)
    if creation_task_id is not None:
        try:
            _ct_db = SessionLocal()
            _ct_row = _ct_db.execute(
                select(CreationTask).where(CreationTask.id == creation_task_id)
            ).scalar_one_or_none()
            if _ct_row and isinstance(_ct_row.payload_json, dict):
                display_total_chapters = int(
                    _ct_row.payload_json.get("original_total_chapters")
                    or _ct_row.payload_json.get("num_chapters")
                    or num_chapters
                )
            _ct_db.close()
        except Exception:
            pass

    data: dict[str, Any] = {
        "status": "running",
        "run_state": "running",
        "step": "queued",
        "current_phase": "queued",
        "current_subtask": {"key": "queued", "label": SUBTASK_LABELS.get("queued"), "progress": 0},
        "progress": 0,
        "current_chapter": int(start_chapter),
        "total_chapters": display_total_chapters,
        "novel_version_id": int(novel_version_id),
        "message": "任务已入队",
        "trace_id": trace_id or "",
    }

    hb_ctx = None
    _worker_superseded = False
    try:
        if creation_task_id is not None:
            _check_worker_superseded(creation_task_id, task_id)
            c_status = _get_creation_task_state(creation_task_id)
            if c_status in {"paused", "cancelled", "failed", "completed"}:
                logger.info("Skip generation execution because creation_task status=%s", c_status)
                data.update(
                    {
                        "status": str(c_status),
                        "run_state": str(c_status),
                        "step": "skipped",
                        "current_phase": "skipped",
                        "message": f"跳过执行：creation_task 状态为 {c_status}",
                    }
                )
                return task_id
            if c_status is None:
                logger.warning("creation_task not visible yet, continue execution task=%s", creation_task_id)
            else:
                _mark_creation_running(creation_task_id)
            resume_plan = _resolve_generation_resume(
                creation_task_id,
                start_chapter=int(start_chapter),
                num_chapters=int(num_chapters),
            )
            start_chapter = int(resume_plan.start_chapter)
            num_chapters = int(resume_plan.num_chapters)
            display_total_chapters = int(resume_plan.display_total_chapters or display_total_chapters)
            resume_mode = str(resume_plan.mode or "chapter_range")
            if resume_mode == "complete" or num_chapters <= 0:
                done_data = {
                    "status": "completed",
                    "run_state": "completed",
                    "step": "done",
                    "current_phase": "completed",
                    "current_subtask": {"key": "done", "label": SUBTASK_LABELS.get("done"), "progress": 100},
                    "progress": 100,
                    "current_chapter": max(0, int(display_total_chapters)),
                    "total_chapters": int(display_total_chapters),
                    "volume_no": 1,
                    "volume_size": 1,
                    "message": "任务已完成（已无待处理章节）",
                    "trace_id": trace_id or "",
                }
                _set_status(r, key, novel_key, done_data)
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

            novel_stmt = select(Novel).where(Novel.id == novel_id)
            novel = db.execute(novel_stmt).scalar_one_or_none()
            volume_size = int(((novel.config or {}).get("volume_size") or 30)) if novel else 30
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
                chapter_num=start_chapter,
                total_chapters=num_chapters,
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

        if resume_mode == "final_book_review":
            final_volume_no = max(1, ((max(display_total_chapters, 1) - 1) // max(volume_size, 1)) + 1)
            chunks = [(final_volume_no, start_chapter, num_chapters)]
        else:
            chunks = _volume_chunks(start_chapter, num_chapters, volume_size)

        data = {
            "status": "running",
            "run_state": "running",
            "step": "book_orchestrator",
            "current_phase": "book_planning",
            "current_subtask": {"key": "book_planning", "label": SUBTASK_LABELS.get("book_planning"), "progress": 5},
            "current_chapter": start_chapter,
            "total_chapters": display_total_chapters,
            "novel_version_id": int(novel_version_id),
            "progress": 5,
            "volume_no": 1,
            "volume_size": volume_size,
            "message": f"总控任务已启动，拆分为{len(chunks)}个卷任务",
            "trace_id": trace_id,
        }
        _set_status(r, key, novel_key, data)
        db = SessionLocal()
        try:
            _persist_generation_task(db, task_id, data)
        finally:
            db.close()
            db = None

        for volume_no, chunk_start, chunk_len in chunks:
            announce = {
                "status": "running",
                "run_state": "running",
                "step": "volume_dispatch",
                "current_phase": "volume_dispatch",
                "current_subtask": {"key": "volume_dispatch", "label": SUBTASK_LABELS.get("volume_dispatch")},
                "current_chapter": chunk_start,
                "total_chapters": display_total_chapters,
                "novel_version_id": int(novel_version_id),
                "progress": round(10 + ((volume_no - 1) / max(len(chunks), 1)) * 70, 2),
                "volume_no": volume_no,
                "volume_size": volume_size,
                "message": f"开始第{volume_no}卷（第{chunk_start}章起，共{chunk_len}章）",
                "trace_id": trace_id,
            }
            _set_status(r, key, novel_key, announce)
            db = SessionLocal()
            try:
                _persist_generation_task(db, task_id, announce)
            finally:
                db.close()
                db = None
            _run_volume_generation(
                novel_id=int(novel_id),
                novel_version_id=int(novel_version_id),
                chunk_chapters=chunk_len,
                chunk_start=chunk_start,
                parent_task_id=task_id,
                total_chapters=display_total_chapters,
                total_start_chapter=start_chapter,
                volume_no=volume_no,
                volume_size=volume_size,
                creation_task_id=creation_task_id,
                resume_mode=resume_mode,
            )

        data = {
            "status": "completed",
            "run_state": "completed",
            "step": "done",
            "current_phase": "completed",
            "current_subtask": {"key": "done", "label": SUBTASK_LABELS.get("done"), "progress": 100},
            "progress": 100,
            "current_chapter": start_chapter + num_chapters - 1,
            "total_chapters": display_total_chapters,
            "novel_version_id": int(novel_version_id),
            "volume_no": chunks[-1][0] if chunks else 1,
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
                "total_chapters": display_total_chapters,
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
            "start_chapter": int(start_chapter or 1),
            "current_chapter": int(data.get("current_chapter") or 0),
            "total_chapters": int(data.get("total_chapters") or 0),
            "completed_chapters": max(
                0,
                int(data.get("current_chapter") or 0) - int(start_chapter or 1) + 1,
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
                            start_chapter=int(start_chapter or 1),
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
        _set_status(r, key, novel_key, data)
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
        end_usage_session()

    return task_id


# Keep old task name as public API (directly mapped to new book orchestrator).
submit_generation_task = submit_book_generation_task
