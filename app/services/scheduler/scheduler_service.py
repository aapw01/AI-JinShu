"""Unified task submission and dispatching for creation workloads."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Select, asc, select
from sqlalchemy.orm import Session

from app.models.creation_task import CreationTask
from app.services.scheduler.concurrency_service import count_user_running_slots, get_user_concurrency_limit
from app.services.scheduler.lock_service import acquire_user_dispatch_lock
from app.services.system_settings.runtime import get_effective_runtime_setting
from app.services.task_runtime.lease_service import acquire_or_refresh_lease

import json
import logging

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
ACTIVE_STATUSES = {"queued", "dispatching", "running", "paused"}
PAUSABLE_STATUSES = {"queued", "dispatching", "running"}
RESUMABLE_STATUSES = {"paused", "failed"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _lease_ttl_seconds() -> int:
    return max(5, int(get_effective_runtime_setting("creation_worker_lease_ttl_seconds", int, 20) or 20))


def submit_task(
    db: Session,
    *,
    user_uuid: str,
    task_type: str,
    resource_type: str,
    resource_id: int,
    payload: dict[str, Any] | None = None,
    priority: int = 100,
    max_retries: int = 3,
) -> CreationTask:
    task = CreationTask(
        user_uuid=user_uuid,
        task_type=task_type,
        resource_type=resource_type,
        resource_id=resource_id,
        status="queued",
        priority=max(0, int(priority)),
        retry_count=0,
        max_retries=max(0, int(max_retries)),
        progress=0.0,
        payload_json=payload or {},
    )
    db.add(task)
    db.flush()
    task.queue_seq = int(task.id)
    db.flush()
    dispatch_user_queue(db, user_uuid=user_uuid)
    return task


def list_user_tasks(db: Session, *, user_uuid: str, limit: int = 50) -> list[CreationTask]:
    stmt: Select[tuple[CreationTask]] = (
        select(CreationTask)
        .where(CreationTask.user_uuid == user_uuid)
        .order_by(CreationTask.created_at.desc(), CreationTask.id.desc())
        .limit(max(1, min(200, int(limit))))
    )
    return list(db.execute(stmt).scalars().all())


def get_task_by_public_id(db: Session, *, public_id: str, user_uuid: str | None = None) -> CreationTask | None:
    stmt: Select[tuple[CreationTask]] = select(CreationTask).where(CreationTask.public_id == public_id)
    if user_uuid:
        stmt = stmt.where(CreationTask.user_uuid == user_uuid)
    return db.execute(stmt).scalar_one_or_none()


def get_task_by_id(db: Session, *, task_id: int) -> CreationTask | None:
    return db.execute(select(CreationTask).where(CreationTask.id == task_id)).scalar_one_or_none()


def mark_task_running(db: Session, *, task_id: int) -> CreationTask | None:
    task = db.execute(
        select(CreationTask).where(CreationTask.id == task_id).with_for_update()
    ).scalar_one_or_none()
    if not task:
        return None
    if task.status != "dispatching":
        return task
    task.status = "running"
    task.started_at = task.started_at or _utc_now()
    task.last_heartbeat_at = _utc_now()
    ttl = _lease_ttl_seconds()
    acquire_or_refresh_lease(db, creation_task_id=task_id, ttl_seconds=ttl)
    task.error_code = None
    task.error_category = None
    task.error_detail = None
    db.flush()
    return task


def update_task_progress(
    db: Session,
    *,
    task_id: int,
    progress: float | None = None,
    phase: str | None = None,
    message: str | None = None,
    token_usage_input: int | None = None,
    token_usage_output: int | None = None,
    estimated_cost: float | None = None,
) -> CreationTask | None:
    task = get_task_by_id(db, task_id=task_id)
    if not task:
        return None
    if progress is not None:
        task.progress = float(max(0.0, min(100.0, progress)))
    if phase is not None:
        task.phase = phase
    if message is not None:
        task.message = message
    if token_usage_input is not None or token_usage_output is not None or estimated_cost is not None:
        result = dict(task.result_json) if isinstance(task.result_json, dict) else {}
        if token_usage_input is not None:
            result["token_usage_input"] = max(int(result.get("token_usage_input") or 0), int(token_usage_input or 0))
        if token_usage_output is not None:
            result["token_usage_output"] = max(int(result.get("token_usage_output") or 0), int(token_usage_output or 0))
        if estimated_cost is not None:
            result["estimated_cost"] = max(float(result.get("estimated_cost") or 0.0), float(estimated_cost or 0.0))
        task.result_json = result
    if task.status == "running":
        ttl = _lease_ttl_seconds()
        acquire_or_refresh_lease(db, creation_task_id=task_id, ttl_seconds=ttl)
    db.flush()
    return task


def transition_task_status(
    db: Session,
    *,
    task: CreationTask,
    to_status: str,
    phase: str | None = None,
    message: str | None = None,
    error_code: str | None = None,
    error_category: str | None = None,
    error_detail: str | None = None,
    progress: float | None = None,
) -> CreationTask:
    allowed: dict[str, set[str]] = {
        "queued": {"dispatching", "paused", "cancelled"},
        "dispatching": {"running", "paused", "cancelled", "queued", "failed"},
        "running": {"paused", "completed", "failed", "cancelled"},
        "paused": {"queued", "cancelled"},
        "failed": {"queued", "cancelled"},
        "completed": set(),
        "cancelled": set(),
    }
    to_status = str(to_status)
    if to_status not in allowed.get(task.status, set()):
        raise ValueError(f"illegal transition: {task.status} -> {to_status}")
    task.status = to_status
    if progress is not None:
        task.progress = float(max(0.0, min(100.0, progress)))
    if phase is not None:
        task.phase = phase
    if message is not None:
        task.message = message
    if error_code is not None:
        task.error_code = error_code
    if error_category is not None:
        task.error_category = error_category
    if error_detail is not None:
        task.error_detail = error_detail
    if to_status == "running":
        task.started_at = task.started_at or _utc_now()
    if to_status in TERMINAL_STATUSES:
        task.finished_at = _utc_now()
    db.flush()
    return task


def finalize_task(
    db: Session,
    *,
    task_id: int,
    final_status: str,
    phase: str | None = None,
    message: str | None = None,
    progress: float | None = None,
    error_code: str | None = None,
    error_category: str | None = None,
    error_detail: str | None = None,
    result_json: dict[str, Any] | None = None,
) -> CreationTask | None:
    task = db.execute(
        select(CreationTask).where(CreationTask.id == task_id).with_for_update()
    ).scalar_one_or_none()
    if not task:
        return None
    if task.status in TERMINAL_STATUSES and final_status in TERMINAL_STATUSES:
        return task
    if task.status == "queued" and final_status in {"paused", "failed"}:
        logger.info(
            "Ignoring stale finalize (status=%s→%s) for task %s — task was already re-queued",
            task.status, final_status, task_id,
        )
        return task
    if task.status not in {"running", "dispatching", "queued"} and final_status not in {"queued"}:
        return task
    if final_status not in {"completed", "failed", "cancelled", "queued", "paused"}:
        raise ValueError(f"unsupported final status: {final_status}")
    if final_status == "queued":
        task.status = "queued"
        task.finished_at = None
        task.worker_task_id = None
        task.worker_lease_expires_at = None
        if task.retry_count < task.max_retries:
            task.retry_count += 1
    elif final_status == "paused":
        task.status = "paused"
        task.worker_task_id = None
        task.worker_lease_expires_at = None
    else:
        task.status = final_status
        task.finished_at = _utc_now()
        task.worker_lease_expires_at = None
    if progress is not None:
        task.progress = float(max(0.0, min(100.0, progress)))
    if phase is not None:
        task.phase = phase
    if message is not None:
        task.message = message
    if error_code is not None:
        task.error_code = error_code
    if error_category is not None:
        task.error_category = error_category
    if error_detail is not None:
        task.error_detail = error_detail
    if result_json is not None:
        task.result_json = result_json
    db.flush()
    dispatch_user_queue(db, user_uuid=task.user_uuid)
    return task


def pause_task(db: Session, *, public_id: str, user_uuid: str) -> CreationTask:
    task = get_task_by_public_id(db, public_id=public_id, user_uuid=user_uuid)
    if not task:
        raise ValueError("task_not_found")
    if task.status in TERMINAL_STATUSES:
        raise ValueError("task_not_active")
    if task.status not in PAUSABLE_STATUSES:
        raise ValueError("task_not_pausable")
    if task.status in {"queued", "dispatching"}:
        transition_task_status(db, task=task, to_status="paused", phase="paused", message="任务已暂停")
    else:
        transition_task_status(db, task=task, to_status="paused", phase="paused", message="任务暂停中，等待安全挂起")
    task.worker_lease_expires_at = None
    dispatch_user_queue(db, user_uuid=user_uuid)
    return task


def resume_task(db: Session, *, public_id: str, user_uuid: str) -> CreationTask:
    task = get_task_by_public_id(db, public_id=public_id, user_uuid=user_uuid)
    if not task:
        raise ValueError("task_not_found")
    if task.status not in RESUMABLE_STATUSES:
        raise ValueError("task_not_resumable")
    old_status = task.status
    cursor = task.resume_cursor_json if isinstance(task.resume_cursor_json, dict) else {}
    next_unit = cursor.get("next")
    resume_msg = "任务已恢复并重新入队"
    if next_unit is not None:
        resume_msg = f"任务已恢复并重新入队，将从第{int(next_unit)}章继续"
    transition_task_status(db, task=task, to_status="queued", phase="queued", message=resume_msg)
    task.worker_task_id = None
    task.worker_lease_expires_at = None
    if old_status == "failed" and task.retry_count < task.max_retries:
        task.retry_count += 1
    dispatch_user_queue(db, user_uuid=user_uuid)
    return task


def cancel_task(db: Session, *, public_id: str, user_uuid: str) -> CreationTask:
    task = get_task_by_public_id(db, public_id=public_id, user_uuid=user_uuid)
    if not task:
        raise ValueError("task_not_found")
    if task.status in TERMINAL_STATUSES:
        return task
    worker_id = task.worker_task_id
    transition_task_status(db, task=task, to_status="cancelled", phase="cancelled", message="任务已取消")
    task.worker_lease_expires_at = None
    if worker_id:
        try:
            from app.workers.celery_app import app as celery_app
            celery_app.control.revoke(worker_id, terminate=True)
        except Exception:
            logger.warning("Failed to revoke worker %s on cancel", worker_id, exc_info=True)
    dispatch_user_queue(db, user_uuid=user_uuid)
    return task


def heartbeat_task(db: Session, *, task_id: int) -> CreationTask | None:
    ttl = _lease_ttl_seconds()
    return acquire_or_refresh_lease(db, creation_task_id=task_id, ttl_seconds=ttl)


def _reclaim_update_redis(items: list[tuple[str | None, str | None, str | None]]) -> None:
    """Clear Redis status for reclaimed tasks so frontend doesn't show stale 'running'."""
    try:
        import redis

        from app.core.config import get_settings

        r = redis.Redis.from_url(get_settings().redis_url)
        queued_payload = json.dumps({
            "status": "queued", "run_state": "queued", "step": "queued",
            "current_phase": "queued", "progress": 0,
            "message": "任务自动恢复：已重新入队",
        }, ensure_ascii=False)
        for worker_id, public_id, novel_id in items:
            if worker_id:
                r.setex(f"generation:{worker_id}", 86400, queued_payload)
            if public_id:
                r.setex(f"generation:{public_id}", 86400, queued_payload)
            if novel_id:
                r.setex(f"generation:novel:{novel_id}", 86400, queued_payload)
    except Exception:
        logger.warning("Failed to update Redis during reclaim", exc_info=True)


def reclaim_stale_running_tasks(db: Session) -> int:
    now = _utc_now()
    dispatching_timeout = now - timedelta(seconds=120)
    stale = list(
        db.execute(
            select(CreationTask)
            .where(
                CreationTask.status.in_(("dispatching", "running")),
                CreationTask.worker_lease_expires_at.is_not(None),
                CreationTask.worker_lease_expires_at < now,
            )
            .with_for_update(skip_locked=True)
        ).scalars().all()
    )
    orphaned_dispatching = list(
        db.execute(
            select(CreationTask)
            .where(
                CreationTask.status == "dispatching",
                CreationTask.worker_lease_expires_at.is_(None),
                CreationTask.updated_at < dispatching_timeout,
            )
            .with_for_update(skip_locked=True)
        ).scalars().all()
    )
    all_stale = stale + orphaned_dispatching
    reclaimed = 0
    old_worker_ids: list[str] = []
    redis_cleanup: list[tuple[str | None, str | None, str | None]] = []
    for row in all_stale:
        if row.worker_task_id:
            old_worker_ids.append(str(row.worker_task_id))
        redis_cleanup.append((
            str(row.worker_task_id) if row.worker_task_id else None,
            row.public_id,
            str(row.resource_id) if row.task_type == "generation" and row.resource_type == "novel" else None,
        ))
        row.status = "queued"
        row.phase = "queued"
        row.message = "任务自动恢复：检测到worker中断，已重新入队"
        row.worker_task_id = None
        row.worker_lease_expires_at = None
        row.recovery_count = int(row.recovery_count or 0) + 1
        row.finished_at = None
        reclaimed += 1
    if old_worker_ids:
        try:
            from app.workers.celery_app import app as celery_app
            for wid in old_worker_ids:
                celery_app.control.revoke(wid, terminate=True)
        except Exception:
            pass
    if redis_cleanup:
        _reclaim_update_redis(redis_cleanup)
    if reclaimed:
        db.flush()
        users = {str(r.user_uuid) for r in all_stale if r.user_uuid}
        for user_uuid in users:
            dispatch_user_queue(db, user_uuid=user_uuid)
    return reclaimed


def dispatch_user_queue(db: Session, *, user_uuid: str) -> list[CreationTask]:
    if not bool(get_effective_runtime_setting("creation_scheduler_enabled", bool, True)):
        return []
    acquire_user_dispatch_lock(db, user_uuid=user_uuid)
    limit = get_user_concurrency_limit(db, user_uuid=user_uuid)
    running_slots = count_user_running_slots(db, user_uuid=user_uuid)
    available = max(0, int(limit) - int(running_slots))
    if available <= 0:
        return []
    batch_max = max(1, int(get_effective_runtime_setting("creation_max_dispatch_batch", int, 5) or 5))
    to_dispatch_count = min(available, batch_max)
    queued = list(
        db.execute(
            select(CreationTask)
            .where(
                CreationTask.user_uuid == user_uuid,
                CreationTask.status == "queued",
            )
            .order_by(asc(CreationTask.priority), asc(CreationTask.queue_seq), asc(CreationTask.id))
            .limit(to_dispatch_count)
            .with_for_update()
        ).scalars().all()
    )
    dispatched: list[CreationTask] = []
    for task in queued:
        transition_task_status(db, task=task, to_status="dispatching", phase="dispatching", message="任务调度中")
        task.worker_lease_expires_at = _utc_now() + timedelta(seconds=120)
        db.flush()
        try:
            _enqueue_worker_task(db, task=task)
            dispatched.append(task)
        except Exception as exc:
            # Isolate bad historical tasks/payloads so one broken row won't break
            # the current API submit flow.
            transition_task_status(
                db,
                task=task,
                to_status="failed",
                phase="failed",
                message="任务派发失败",
                error_code="DISPATCH_PAYLOAD_INVALID",
                error_category="permanent",
                error_detail=str(exc),
            )
    return dispatched


def dispatch_global(db: Session) -> int:
    users = list(
        db.execute(
            select(CreationTask.user_uuid)
            .where(CreationTask.status == "queued")
            .distinct()
        ).scalars().all()
    )
    count = 0
    for user_uuid in users:
        count += len(dispatch_user_queue(db, user_uuid=user_uuid))
    return count


def _enqueue_worker_task(db: Session, *, task: CreationTask) -> None:
    if task.task_type == "generation":
        from app.models.novel import GenerationTask
        from app.tasks.generation import submit_generation_task

        payload = task.payload_json or {}
        for key in ("novel_id", "novel_version_id", "num_chapters", "start_chapter"):
            if payload.get(key) is None:
                raise ValueError(f"missing payload key for generation: {key}")
        async_result = submit_generation_task.delay(
            novel_id=payload["novel_id"],
            novel_version_id=payload["novel_version_id"],
            num_chapters=payload["num_chapters"],
            start_chapter=payload["start_chapter"],
            parent_task_id=None,
            trace_id=payload.get("trace_id"),
            creation_task_id=task.id,
        )
        db.add(
            GenerationTask(
                task_id=str(async_result.id),
                novel_id=int(payload["novel_id"]),
                status="submitted",
                run_state="submitted",
                current_phase="queued",
                total_chapters=int(payload["num_chapters"]),
                num_chapters=int(payload["num_chapters"]),
                start_chapter=int(payload["start_chapter"]),
                trace_id=payload.get("trace_id"),
                message="任务已入队",
            )
        )
    elif task.task_type == "rewrite":
        from app.models.novel import RewriteRequest
        from app.tasks.rewrite import submit_rewrite_task

        payload = task.payload_json or {}
        for key in ("novel_id", "rewrite_request_id", "base_version_id", "target_version_id", "rewrite_from", "rewrite_to"):
            if payload.get(key) is None:
                raise ValueError(f"missing payload key for rewrite: {key}")
        async_result = submit_rewrite_task.delay(
            payload["novel_id"],
            payload["rewrite_request_id"],
            payload["base_version_id"],
            payload["target_version_id"],
            payload["rewrite_from"],
            payload["rewrite_to"],
            task.id,
        )
        req = db.execute(select(RewriteRequest).where(RewriteRequest.id == int(payload["rewrite_request_id"]))).scalar_one_or_none()
        if req:
            req.task_id = task.public_id
            req.status = "queued"
            req.message = "重写任务已入队"
    elif task.task_type == "storyboard_lane":
        from app.models.novel import NovelVersion
        from app.models.storyboard import StoryboardProject, StoryboardRun, StoryboardRunLane, StoryboardVersion
        from app.tasks.storyboard import run_storyboard_lane

        payload = task.payload_json or {}
        for key in ("project_id", "run_id", "run_lane_id", "lane", "version_id", "novel_version_id"):
            if payload.get(key) is None:
                raise ValueError(f"missing payload key for storyboard_lane: {key}")
        project_id = int(payload["project_id"])
        run_id = int(payload["run_id"])
        run_lane_id = int(payload["run_lane_id"])
        lane = str(payload["lane"])
        version_id = int(payload["version_id"])
        novel_version_id = int(payload["novel_version_id"])

        project = db.execute(select(StoryboardProject).where(StoryboardProject.id == project_id)).scalar_one_or_none()
        if not project:
            raise ValueError(f"storyboard project not found: {project_id}")
        run = db.execute(
            select(StoryboardRun).where(
                StoryboardRun.id == run_id,
                StoryboardRun.storyboard_project_id == project_id,
            )
        ).scalar_one_or_none()
        if not run:
            raise ValueError(f"storyboard run context not found: {run_id}")
        run_lane = db.execute(
            select(StoryboardRunLane).where(
                StoryboardRunLane.id == run_lane_id,
                StoryboardRunLane.storyboard_run_id == run_id,
                StoryboardRunLane.storyboard_project_id == project_id,
            )
        ).scalar_one_or_none()
        if not run_lane:
            raise ValueError(f"storyboard run lane context not found: {run_lane_id}")
        version = db.execute(
            select(StoryboardVersion).where(
                StoryboardVersion.id == version_id,
                StoryboardVersion.storyboard_project_id == project_id,
            )
        ).scalar_one_or_none()
        if not version:
            raise ValueError(f"storyboard version context not found: {version_id}")
        if version.lane != lane:
            raise ValueError("storyboard run lane/version mismatch")
        source_version = db.execute(select(NovelVersion).where(NovelVersion.id == novel_version_id)).scalar_one_or_none()
        if not source_version or int(source_version.novel_id) != int(project.novel_id):
            raise ValueError("storyboard novel_version context invalid")
        async_result = run_storyboard_lane.delay(
            project_id=project_id,
            run_id=run_id,
            run_lane_id=run_lane_id,
            version_id=version_id,
            lane=lane,
            novel_version_id=novel_version_id,
            creation_task_id=int(task.id),
        )
    elif task.task_type == "storyboard":
        from app.models.novel import NovelVersion
        from app.models.storyboard import StoryboardProject, StoryboardTask, StoryboardVersion
        from app.tasks.storyboard import run_storyboard_pipeline

        payload = task.payload_json or {}
        for key in ("project_id", "task_db_id", "novel_version_id"):
            if payload.get(key) is None:
                raise ValueError(f"missing payload key for storyboard: {key}")
        project_id = int(payload["project_id"])
        task_db_id = int(payload["task_db_id"])
        novel_version_id = int(payload["novel_version_id"])
        version_ids = [int(x) for x in (payload.get("version_ids") or [])]
        if not version_ids:
            raise ValueError("missing payload key for storyboard: version_ids")

        project = db.execute(select(StoryboardProject).where(StoryboardProject.id == project_id)).scalar_one_or_none()
        if not project:
            raise ValueError(f"storyboard project not found: {project_id}")
        task_row = db.execute(select(StoryboardTask).where(StoryboardTask.id == task_db_id)).scalar_one_or_none()
        if not task_row or int(task_row.storyboard_project_id) != project_id:
            raise ValueError(f"storyboard task context not found: {task_db_id}")
        matched_version_ids = set(
            db.execute(
                select(StoryboardVersion.id).where(
                    StoryboardVersion.id.in_(version_ids),
                    StoryboardVersion.storyboard_project_id == project_id,
                )
            ).scalars().all()
        )
        requested_version_ids = set(version_ids)
        if matched_version_ids != requested_version_ids:
            raise ValueError("storyboard versions context invalid")

        source_version = db.execute(select(NovelVersion).where(NovelVersion.id == novel_version_id)).scalar_one_or_none()
        if not source_version or int(source_version.novel_id) != int(project.novel_id):
            raise ValueError("storyboard novel_version context invalid")

        async_result = run_storyboard_pipeline.delay(
            project_id=project_id,
            version_ids=version_ids,
            novel_version_id=novel_version_id,
            task_db_id=task_db_id,
            creation_task_id=int(task.id),
        )
    else:
        raise ValueError(f"unknown task type: {task.task_type}")
    task.worker_task_id = str(async_result.id)
    db.flush()
