"""Generation submit, status, progress (SSE), cancel routes."""
import json
import asyncio
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
import redis

from app.core.api_errors import http_error
from app.core.authz.deps import require_permission
from app.core.authz.resources import load_novel_resource
from app.core.authz.types import Permission, Principal
from app.core.database import get_db
from app.core.config import get_settings
from app.core.logging_config import log_event
from app.core.time_utils import to_utc_iso_z
from app.models.creation_task import CreationTask
from app.models.novel import GenerationTask, GenerationCheckpoint, User
from app.schemas.novel import GenerateRequest, GenerateResponse, GenerationStatusResponse, RetryGenerationRequest
from app.services.quota import check_generation_quota
from app.services.scheduler.scheduler_service import cancel_task, get_task_by_public_id, pause_task, resume_task, submit_task
from app.services.rewrite.service import get_default_version_id
from app.tasks.generation import submit_generation_task  # legacy patch target for tests

router = APIRouter()
logger = logging.getLogger(__name__)

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

# Redis connection pool for better performance
_redis_pool = None


def _get_redis():
    """Get Redis connection from pool."""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = redis.ConnectionPool.from_url(get_settings().redis_url)
    return redis.Redis(connection_pool=_redis_pool)


def _redis_get_json(key: str) -> dict | None:
    try:
        return _decode_redis_payload(_get_redis().get(key))
    except redis.RedisError as exc:
        log_event(logger, "generation.redis.get_failed", level=logging.WARNING, error=str(exc), redis_key=key)
        return None


def _redis_set_json(key: str, payload: dict, ttl_seconds: int = 86400) -> None:
    try:
        _get_redis().setex(key, ttl_seconds, json.dumps(payload, ensure_ascii=False))
    except redis.RedisError as exc:
        log_event(logger, "generation.redis.set_failed", level=logging.WARNING, error=str(exc), redis_key=key)


def _redis_key(task_id: str) -> str:
    return f"generation:{task_id}"


def _novel_key(novel_id: str) -> str:
    return f"generation:novel:{novel_id}"


def _sse_status_event(payload: dict) -> str:
    """Build unified SSE status envelope."""
    return f"data: {json.dumps({'type': 'status', 'payload': payload}, ensure_ascii=False)}\n\n"


def _decode_redis_payload(raw: bytes | str | None) -> dict | None:
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        return None
    return None


def _to_status_response(payload: dict) -> GenerationStatusResponse:
    step = payload.get("step")
    current_phase = payload.get("current_phase")
    subtask_key = payload.get("subtask_key") or step or current_phase
    subtask_label = payload.get("subtask_label") or SUBTASK_LABELS.get(str(subtask_key or ""), str(subtask_key or ""))
    return GenerationStatusResponse(
        task_id=payload.get("task_id"),
        status=payload.get("status", "unknown"),
        trace_id=payload.get("trace_id"),
        run_state=payload.get("run_state") or payload.get("status"),
        step=step,
        current_phase=current_phase,
        subtask_key=subtask_key,
        subtask_label=subtask_label or None,
        subtask_progress=payload.get("subtask_progress", payload.get("progress")),
        current_subtask={
            "key": subtask_key,
            "label": subtask_label or None,
            "progress": payload.get("subtask_progress", payload.get("progress")),
        } if subtask_key else None,
        current_chapter=payload.get("current_chapter", 0) or 0,
        total_chapters=payload.get("total_chapters", 0) or 0,
        progress=payload.get("progress", 0) or 0,
        token_usage_input=payload.get("token_usage_input", 0) or 0,
        token_usage_output=payload.get("token_usage_output", 0) or 0,
        estimated_cost=payload.get("estimated_cost", 0.0) or 0.0,
        volume_no=payload.get("volume_no"),
        volume_size=payload.get("volume_size"),
        pacing_mode=payload.get("pacing_mode"),
        low_progress_streak=payload.get("low_progress_streak"),
        progress_signal=payload.get("progress_signal"),
        decision_state=payload.get("decision_state"),
        eta_seconds=payload.get("eta_seconds"),
        eta_label=payload.get("eta_label"),
        message=payload.get("message"),
        error=payload.get("error"),
        last_error=payload.get("last_error"),
    )


def _format_eta(seconds: int) -> str:
    sec = max(0, int(seconds))
    if sec < 60:
        return f"约{sec}秒"
    minutes = sec // 60
    if minutes < 60:
        return f"约{minutes}分钟"
    hours = minutes // 60
    mins = minutes % 60
    if mins == 0:
        return f"约{hours}小时"
    return f"约{hours}小时{mins}分钟"


def _smoothed_chapter_seconds(samples: list[float]) -> float:
    if not samples:
        return 0.0
    take = samples[-5:]
    weights = [0.40, 0.27, 0.18, 0.10, 0.05]
    rev = list(reversed(take))
    acc = 0.0
    w_sum = 0.0
    for idx, sec in enumerate(rev):
        w = weights[idx] if idx < len(weights) else 0.03
        acc += max(1.0, float(sec)) * w
        w_sum += w
    return acc / max(w_sum, 1e-6)


def _estimate_eta_payload(
    db: Session,
    novel_db_id: int | None,
    task_id: str | None,
    payload: dict,
) -> dict:
    if not novel_db_id:
        return payload
    status = str(payload.get("status") or "")
    if status in {"completed", "failed", "cancelled"}:
        out = dict(payload)
        out["eta_seconds"] = 0
        out["eta_label"] = "已完成" if status == "completed" else "已停止"
        return out
    total = int(payload.get("total_chapters") or 0)
    current = int(payload.get("current_chapter") or 0)
    if total <= 0:
        return payload
    remaining = max(0, total - current)
    if remaining <= 0:
        out = dict(payload)
        out["eta_seconds"] = 0
        out["eta_label"] = "即将完成"
        return out

    samples: list[float] = []
    cp_stmt = (
        select(GenerationCheckpoint)
        .where(
            GenerationCheckpoint.novel_id == novel_db_id,
            GenerationCheckpoint.node.in_(("chapter_done", "consistency_blocked")),
        )
        .order_by(GenerationCheckpoint.created_at.asc(), GenerationCheckpoint.id.asc())
    )
    if task_id:
        cp_stmt = cp_stmt.where(GenerationCheckpoint.task_id == task_id)
    cps = db.execute(cp_stmt).scalars().all()
    if len(cps) >= 2:
        for i in range(1, len(cps)):
            delta = (cps[i].created_at - cps[i - 1].created_at).total_seconds()
            if delta > 0:
                samples.append(float(delta))

    eta_seconds = 0
    if samples:
        avg_sec = _smoothed_chapter_seconds(samples)
        eta_seconds = int(max(60, round(avg_sec * remaining)))
    else:
        progress = float(payload.get("progress") or 0.0)
        gt_stmt = (
            select(GenerationTask)
            .where(GenerationTask.novel_id == novel_db_id)
            .order_by(GenerationTask.id.desc())
            .limit(1)
        )
        if task_id:
            gt_stmt = select(GenerationTask).where(GenerationTask.task_id == task_id).limit(1)
        gt = db.execute(gt_stmt).scalar_one_or_none()
        if gt and progress > 1.0:
            now = datetime.now(timezone.utc)
            created = gt.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            elapsed = max(1.0, (now - created).total_seconds())
            eta_seconds = int(elapsed * (100.0 - progress) / progress)

    if eta_seconds <= 0:
        return payload
    out = dict(payload)
    out["eta_seconds"] = eta_seconds
    out["eta_label"] = _format_eta(eta_seconds)
    return out


@router.post("/{novel_id}/generate", response_model=GenerateResponse)
def submit_generation(
    novel_id: str,
    req: GenerateRequest,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(Permission.NOVEL_GENERATE, resource_loader=load_novel_resource)),
):
    """Submit generation into unified queued scheduler."""
    from app.core.database import resolve_novel
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise http_error(404, "novel_not_found", "Novel not found")
    active_count = db.execute(
        select(CreationTask.id)
        .where(
            CreationTask.task_type == "generation",
            CreationTask.resource_type == "novel",
            CreationTask.resource_id == novel.id,
            CreationTask.status.in_(("queued", "dispatching", "running")),
        )
    ).scalars().all()
    if active_count:
        raise http_error(409, "generation_already_active", "该小说已有正在进行的生成任务，请等待完成或取消后再试")
    user = db.execute(select(User).where(User.uuid == principal.user_uuid)).scalar_one_or_none()
    if user:
        quota = check_generation_quota(db, user=user, requested_chapters=req.num_chapters)
        if not quota.ok:
            log_event(
                logger,
                "generation.quota.blocked",
                level=logging.WARNING,
                novel_id=novel.id,
                user_id=principal.user_uuid,
                reason=quota.reason,
                total_chapters=req.num_chapters,
            )
            raise http_error(429, str(quota.reason.value if quota.reason else "quota_exceeded"), str(quota.user_message or "当前请求超出配额限制，请稍后再试"))
    trace_id = getattr(request.state, "trace_id", None)
    creation_task = submit_task(
        db,
        user_uuid=principal.user_uuid or "",
        task_type="generation",
        resource_type="novel",
        resource_id=int(novel.id),
        payload={
            "novel_id": int(novel.id),
            "novel_version_id": int(get_default_version_id(db, novel.id)),
            "num_chapters": int(req.num_chapters),
            "start_chapter": int(req.start_chapter),
            "trace_id": trace_id,
        },
    )
    novel.status = "generating"
    novel.config = {**(novel.config or {}), "require_outline_confirmation": bool(req.require_outline_confirmation)}
    db.commit()
    log_event(
        logger,
        "generation.submit",
        novel_id=novel.id,
        user_id=principal.user_uuid,
        task_id=creation_task.public_id,
        run_state="queued",
        chapter_num=req.start_chapter,
        total_chapters=req.num_chapters,
    )
    return GenerateResponse(task_id=creation_task.public_id, novel_id=novel.uuid or str(novel.id), status="queued")


@router.post("/{novel_id}/generation/retry", response_model=GenerateResponse)
def retry_generation(
    novel_id: str,
    req: RetryGenerationRequest,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(Permission.NOVEL_GENERATE, resource_loader=load_novel_resource)),
):
    """Retry generation from latest failed position (or specified failed task).

    Prefer resume_task on the existing CreationTask to preserve checkpoints.
    Fall back to creating a new task only when no resumable CreationTask exists.
    """
    from app.core.database import resolve_novel

    novel = resolve_novel(db, novel_id)
    if not novel:
        raise http_error(404, "novel_not_found", "Novel not found")
    user = db.execute(select(User).where(User.uuid == principal.user_uuid)).scalar_one_or_none()

    # 1) Resolve source CreationTask (unified flow uses CreationTask.public_id as task_id)
    source_creation: CreationTask | None = None
    if req.task_id:
        source_creation = get_task_by_public_id(db, public_id=req.task_id, user_uuid=principal.user_uuid or "")
        if source_creation and (source_creation.resource_id != int(novel.id) or source_creation.task_type != "generation"):
            source_creation = None
    if source_creation is None:
        source_creation = db.execute(
            select(CreationTask)
            .where(
                CreationTask.user_uuid == (principal.user_uuid or ""),
                CreationTask.task_type == "generation",
                CreationTask.resource_type == "novel",
                CreationTask.resource_id == int(novel.id),
                CreationTask.status == "failed",
            )
            .order_by(CreationTask.updated_at.desc(), CreationTask.id.desc())
            .limit(1)
        ).scalar_one_or_none()

    # 2) If we have a failed or paused CreationTask, resume it (preserves checkpoints)
    if source_creation and source_creation.status in {"failed", "paused"}:
        if (source_creation.retry_count or 0) >= (source_creation.max_retries or 0):
            raise http_error(409, "max_retries_exceeded", "已达到最大重试次数")
        try:
            resumed = resume_task(db, public_id=source_creation.public_id, user_uuid=principal.user_uuid or "")
        except ValueError as exc:
            if str(exc) == "task_not_resumable":
                raise http_error(409, "task_not_resumable", "当前任务不可恢复")
            raise
        novel.status = "generating"
        db.commit()
        cursor = resumed.resume_cursor_json if isinstance(resumed.resume_cursor_json, dict) else {}
        next_ch = cursor.get("next")
        payload_data = resumed.payload_json or {}
        total_ch = int(payload_data.get("num_chapters") or 0)
        msg = f"重试已提交：从第{int(next_ch)}章继续" if next_ch is not None else "重试已提交"
        data = {
            "status": "queued",
            "run_state": "queued",
            "step": "queued",
            "current_phase": "queued",
            "current_subtask": {"key": "queued", "label": "任务已入队", "progress": 0},
            "subtask_key": "queued",
            "subtask_label": "任务已入队",
            "subtask_progress": 0,
            "current_chapter": int(next_ch) if next_ch is not None else 0,
            "total_chapters": total_ch,
            "progress": 0,
            "message": msg,
            "task_id": resumed.public_id,
            "trace_id": getattr(request.state, "trace_id", None),
        }
        _redis_set_json(_redis_key(resumed.public_id), data)
        _redis_set_json(_novel_key(str(novel.id)), data)
        log_event(
            logger,
            "generation.retry.submit",
            novel_id=novel.id,
            user_id=principal.user_uuid,
            task_id=resumed.public_id,
            run_state="queued",
            chapter_num=int(next_ch) if next_ch is not None else 0,
            total_chapters=total_ch,
        )
        return GenerateResponse(task_id=resumed.public_id, novel_id=novel.uuid or str(novel.id), status="queued")

    # 3) If source_creation exists but is cancelled, create new task from its resume_cursor
    if source_creation and source_creation.status == "cancelled":
        payload_data = source_creation.payload_json or {}
        start_ch = int(payload_data.get("start_chapter") or 1)
        num_ch = int(payload_data.get("num_chapters") or 1)
        cursor = source_creation.resume_cursor_json if isinstance(source_creation.resume_cursor_json, dict) else {}
        retry_start = int(cursor.get("next") or start_ch)
        source_end = start_ch + max(1, num_ch) - 1
        retry_start = max(start_ch, min(retry_start, source_end))
        retry_num = max(1, source_end - retry_start + 1)
        retry_version_id = int(payload_data.get("novel_version_id") or get_default_version_id(db, novel.id))
        if user:
            quota = check_generation_quota(db, user=user, requested_chapters=retry_num)
            if not quota.ok:
                raise http_error(429, str(quota.reason.value if quota.reason else "quota_exceeded"), str(quota.user_message or "当前请求超出配额限制，请稍后再试"))
        trace_id = getattr(request.state, "trace_id", None)
        creation_task = submit_task(
            db,
            user_uuid=principal.user_uuid or "",
            task_type="generation",
            resource_type="novel",
            resource_id=int(novel.id),
            payload={
                "novel_id": int(novel.id),
                "novel_version_id": int(retry_version_id),
                "num_chapters": int(retry_num),
                "start_chapter": int(retry_start),
                "trace_id": trace_id,
            },
        )
        novel.status = "generating"
        db.commit()
        data = {
            "status": "queued",
            "run_state": "queued",
            "step": "queued",
            "current_phase": "queued",
            "current_subtask": {"key": "queued", "label": "任务已入队", "progress": 0},
            "subtask_key": "queued",
            "subtask_label": "任务已入队",
            "subtask_progress": 0,
            "current_chapter": retry_start,
            "total_chapters": retry_num,
            "progress": 0,
            "message": f"重试已提交：从第{retry_start}章继续",
            "task_id": creation_task.public_id,
            "trace_id": trace_id,
        }
        _redis_set_json(_redis_key(creation_task.public_id), data)
        _redis_set_json(_novel_key(str(novel.id)), data)
        log_event(logger, "generation.retry.submit", novel_id=novel.id, user_id=principal.user_uuid, task_id=creation_task.public_id, run_state="queued", chapter_num=retry_start, total_chapters=retry_num)
        return GenerateResponse(task_id=creation_task.public_id, novel_id=novel.uuid or str(novel.id), status="queued")

    # 4) Fallback: legacy path using GenerationTask (creates new CreationTask)
    source_task = None
    if req.task_id:
        source_stmt = select(GenerationTask).where(
            GenerationTask.novel_id == novel.id,
            GenerationTask.task_id == req.task_id,
        )
        source_task = db.execute(source_stmt).scalar_one_or_none()
        if not source_task:
            raise http_error(404, "task_not_found", "指定的任务不存在")
    else:
        source_stmt = (
            select(GenerationTask)
            .where(
                GenerationTask.novel_id == novel.id,
                GenerationTask.status.in_(["failed", "cancelled"]),
            )
            .order_by(GenerationTask.updated_at.desc(), GenerationTask.id.desc())
            .limit(1)
        )
        source_task = db.execute(source_stmt).scalar_one_or_none()
        if not source_task:
            raise http_error(409, "no_retryable_failed_task", "当前没有可重试的失败任务")

    if source_task.status not in {"failed", "cancelled"}:
        raise http_error(409, "task_not_retryable", f"任务状态为 {source_task.status}，不可重试")

    source_start = int(source_task.start_chapter or 1)
    source_total = int(source_task.total_chapters or source_task.num_chapters or 1)
    source_end = source_start + max(1, source_total) - 1
    # Prefer resume_cursor from CreationTask (worker_task_id links to GenerationTask.task_id)
    retry_start = int(source_task.current_chapter or source_start)
    legacy_creation = db.execute(
        select(CreationTask).where(
            CreationTask.worker_task_id == source_task.task_id,
            CreationTask.task_type == "generation",
            CreationTask.resource_type == "novel",
            CreationTask.resource_id == int(novel.id),
        )
    ).scalar_one_or_none()
    if legacy_creation and isinstance(legacy_creation.resume_cursor_json, dict):
        next_val = legacy_creation.resume_cursor_json.get("next")
        if next_val is not None:
            retry_start = max(source_start, min(int(next_val), source_end))
    else:
        retry_start = max(source_start, min(retry_start, source_end))
    retry_num = max(1, source_end - retry_start + 1)

    if user:
        quota = check_generation_quota(db, user=user, requested_chapters=retry_num)
        if not quota.ok:
            log_event(
                logger,
                "generation.quota.blocked",
                level=logging.WARNING,
                novel_id=novel.id,
                user_id=principal.user_uuid,
                reason=quota.reason,
                total_chapters=retry_num,
            )
            raise http_error(429, str(quota.reason.value if quota.reason else "quota_exceeded"), str(quota.user_message or "当前请求超出配额限制，请稍后再试"))

    retry_version_id: int | None = None
    if legacy_creation and isinstance(legacy_creation.payload_json, dict):
        v = legacy_creation.payload_json.get("novel_version_id")
        if v is not None:
            retry_version_id = int(v)
    if retry_version_id is None:
        retry_version_id = int(get_default_version_id(db, novel.id))

    trace_id = getattr(request.state, "trace_id", None)
    creation_task = submit_task(
        db,
        user_uuid=principal.user_uuid or "",
        task_type="generation",
        resource_type="novel",
        resource_id=int(novel.id),
        payload={
            "novel_id": int(novel.id),
            "novel_version_id": int(retry_version_id),
            "num_chapters": int(retry_num),
            "start_chapter": int(retry_start),
            "trace_id": trace_id,
        },
    )
    novel.status = "generating"
    db.commit()

    data = {
        "status": "queued",
        "run_state": "queued",
        "step": "queued",
        "current_phase": "queued",
        "current_subtask": {"key": "queued", "label": "任务已入队", "progress": 0},
        "subtask_key": "queued",
        "subtask_label": "任务已入队",
        "subtask_progress": 0,
        "current_chapter": retry_start,
        "total_chapters": retry_num,
        "progress": 0,
        "message": f"重试已提交：从第{retry_start}章继续",
        "task_id": creation_task.public_id,
        "trace_id": trace_id,
    }
    _redis_set_json(_redis_key(creation_task.public_id), data)
    _redis_set_json(_novel_key(str(novel.id)), data)
    log_event(
        logger,
        "generation.retry.submit",
        novel_id=novel.id,
        user_id=principal.user_uuid,
        task_id=creation_task.public_id,
        run_state="queued",
        chapter_num=retry_start,
        total_chapters=retry_num,
    )
    return GenerateResponse(task_id=creation_task.public_id, novel_id=novel.uuid or str(novel.id), status="queued")


@router.delete("/{novel_id}/generation/{task_id}")
def cancel_generation(
    novel_id: str,
    task_id: str,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.NOVEL_GENERATE, resource_loader=load_novel_resource)),
):
    """Cancel unified creation task by task_id."""
    from app.core.database import resolve_novel
    from app.workers.celery_app import app as celery_app

    novel = resolve_novel(db, novel_id)
    if not novel:
        raise http_error(404, "novel_not_found", "Novel not found")

    ctask = get_task_by_public_id(db, public_id=task_id, user_uuid=_.user_uuid or "")
    if not ctask:
        raise http_error(404, "task_not_found", "Task not found")
    if ctask.worker_task_id:
        try:
            celery_app.control.revoke(ctask.worker_task_id, terminate=True)
        except Exception:
            logger.warning("Failed to revoke worker task %s, proceeding with cancel", ctask.worker_task_id)
    cancel_task(db, public_id=task_id, user_uuid=_.user_uuid or "")
    if ctask.resource_id == novel.id:
        novel.status = "draft"
    db.commit()

    # Update Redis (both task key and novel key)
    data = {
        "status": "cancelled",
        "run_state": "cancelled",
        "step": "cancelled",
        "current_phase": "cancelled",
        "current_subtask": {"key": "cancelled", "label": "任务已取消", "progress": ctask.progress or 0},
        "subtask_key": "cancelled",
        "subtask_label": "任务已取消",
        "subtask_progress": ctask.progress or 0,
        "progress": ctask.progress or 0,
        "message": "任务已取消",
    }
    _redis_set_json(_redis_key(task_id), data)
    if ctask.worker_task_id and ctask.worker_task_id != task_id:
        _redis_set_json(_redis_key(ctask.worker_task_id), data)
    _redis_set_json(_novel_key(str(novel.id)), data)
    log_event(logger, "generation.cancel", novel_id=novel.id, task_id=task_id, run_state="cancelled")
    return {"ok": True, "message": "Task cancelled"}


@router.post("/{novel_id}/generation/pause")
def pause_generation(
    novel_id: str,
    task_id: str | None = None,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(Permission.NOVEL_GENERATE, resource_loader=load_novel_resource)),
):
    from app.core.database import resolve_novel

    novel = resolve_novel(db, novel_id)
    if not novel:
        raise http_error(404, "novel_not_found", "Novel not found")
    row = None
    if task_id:
        row = get_task_by_public_id(db, public_id=task_id, user_uuid=principal.user_uuid or "")
    else:
        row = db.execute(
            select(CreationTask)
            .where(
                CreationTask.user_uuid == (principal.user_uuid or ""),
                CreationTask.task_type == "generation",
                CreationTask.resource_type == "novel",
                CreationTask.resource_id == novel.id,
                CreationTask.status.in_(["queued", "dispatching", "running"]),
            )
            .order_by(CreationTask.updated_at.desc(), CreationTask.id.desc())
        ).scalar_one_or_none()
    if not row:
        raise http_error(404, "no_running_task", "No running task")
    try:
        pause_task(db, public_id=row.public_id, user_uuid=principal.user_uuid or "")
    except ValueError as exc:
        code = str(exc)
        if code == "task_not_found":
            raise http_error(404, "task_not_found", "Task not found")
        if code == "task_not_active":
            raise http_error(409, "task_not_active", "Task is not active")
        if code == "task_not_pausable":
            raise http_error(409, "task_not_pausable", "当前任务不可暂停")
        raise http_error(409, "task_not_pausable", "当前任务不可暂停")
    db.commit()
    existing = _redis_get_json(_redis_key(row.worker_task_id or row.public_id)) or {}
    payload = {
        "status": "paused",
        "run_state": "paused",
        "step": "paused",
        "current_phase": "paused",
        "current_chapter": existing.get("current_chapter") or 0,
        "total_chapters": existing.get("total_chapters") or 0,
        "progress": row.progress or existing.get("progress") or 0,
        "message": "任务已暂停",
        "task_id": row.public_id,
    }
    _redis_set_json(_redis_key(row.public_id), payload)
    if row.worker_task_id:
        _redis_set_json(_redis_key(row.worker_task_id), payload)
    _redis_set_json(_novel_key(str(novel.id)), payload)
    log_event(logger, "generation.pause", novel_id=novel.id, task_id=row.public_id, run_state="paused")
    return {"ok": True, "task_id": row.public_id, "run_state": "paused"}


@router.post("/{novel_id}/generation/resume")
def resume_generation(
    novel_id: str,
    task_id: str | None = None,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(Permission.NOVEL_GENERATE, resource_loader=load_novel_resource)),
):
    from app.core.database import resolve_novel

    novel = resolve_novel(db, novel_id)
    if not novel:
        raise http_error(404, "novel_not_found", "Novel not found")
    row = None
    if task_id:
        row = get_task_by_public_id(db, public_id=task_id, user_uuid=principal.user_uuid or "")
    else:
        row = db.execute(
            select(CreationTask)
            .where(
                CreationTask.user_uuid == (principal.user_uuid or ""),
                CreationTask.task_type == "generation",
                CreationTask.resource_type == "novel",
                CreationTask.resource_id == novel.id,
                CreationTask.status == "paused",
            )
            .order_by(CreationTask.updated_at.desc(), CreationTask.id.desc())
        ).scalar_one_or_none()
    if not row:
        raise http_error(404, "no_paused_task", "No paused task")
    try:
        resume_task(db, public_id=row.public_id, user_uuid=principal.user_uuid or "")
    except ValueError as exc:
        code = str(exc)
        if code == "task_not_found":
            raise http_error(404, "task_not_found", "Task not found")
        if code == "task_not_resumable":
            raise http_error(409, "task_not_resumable", "当前任务不可恢复")
        raise http_error(409, "task_not_resumable", "当前任务不可恢复")
    db.commit()
    existing = _redis_get_json(_redis_key(row.worker_task_id or row.public_id)) or {}
    payload = {
        "status": "queued",
        "run_state": "queued",
        "step": "queued",
        "current_phase": "queued",
        "current_chapter": existing.get("current_chapter") or 0,
        "total_chapters": existing.get("total_chapters") or 0,
        "progress": row.progress or existing.get("progress") or 0,
        "message": "任务已恢复并重新入队",
        "task_id": row.public_id,
    }
    _redis_set_json(_redis_key(row.public_id), payload)
    if row.worker_task_id:
        _redis_set_json(_redis_key(row.worker_task_id), payload)
    _redis_set_json(_novel_key(str(novel.id)), payload)
    log_event(logger, "generation.resume", novel_id=novel.id, task_id=row.public_id, run_state="queued")
    return {"ok": True, "task_id": row.public_id, "run_state": "queued"}


@router.post("/{novel_id}/generation/cancel")
def cancel_generation_by_novel(
    novel_id: str,
    task_id: str | None = None,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(Permission.NOVEL_GENERATE, resource_loader=load_novel_resource)),
):
    from app.core.database import resolve_novel
    from app.workers.celery_app import app as celery_app

    novel = resolve_novel(db, novel_id)
    if not novel:
        raise http_error(404, "novel_not_found", "Novel not found")
    row = None
    if task_id:
        row = get_task_by_public_id(db, public_id=task_id, user_uuid=principal.user_uuid or "")
    else:
        row = db.execute(
            select(CreationTask)
            .where(
                CreationTask.user_uuid == (principal.user_uuid or ""),
                CreationTask.task_type == "generation",
                CreationTask.resource_type == "novel",
                CreationTask.resource_id == novel.id,
                CreationTask.status.in_(["queued", "dispatching", "running", "paused"]),
            )
            .order_by(CreationTask.updated_at.desc(), CreationTask.id.desc())
        ).scalar_one_or_none()
    if not row:
        raise http_error(404, "no_active_task", "No active task")
    try:
        cancel_task(db, public_id=row.public_id, user_uuid=principal.user_uuid or "")
    except ValueError as exc:
        code = str(exc)
        if code == "task_not_found":
            raise http_error(404, "task_not_found", "Task not found")
        raise http_error(409, "task_not_cancellable", "当前任务不可取消")
    if row.worker_task_id:
        try:
            celery_app.control.revoke(row.worker_task_id, terminate=True)
        except Exception:
            logger.warning("Failed to revoke worker task %s, proceeding with cancel", row.worker_task_id)
    novel.status = "draft"
    db.commit()
    payload = {
        "status": "cancelled",
        "run_state": "cancelled",
        "step": "cancelled",
        "current_phase": "cancelled",
        "current_chapter": 0,
        "total_chapters": 0,
        "progress": row.progress or 0,
        "message": "任务已取消",
        "task_id": row.public_id,
    }
    _redis_set_json(_redis_key(row.public_id), payload)
    _redis_set_json(_novel_key(str(novel.id)), payload)
    log_event(logger, "generation.cancel", novel_id=novel.id, task_id=row.public_id, run_state="cancelled")
    return {"ok": True, "task_id": row.public_id, "run_state": "cancelled"}


@router.get("/{novel_id}/generation/tasks")
def list_generation_tasks(
    novel_id: str,
    limit: int = 20,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.NOVEL_READ, resource_loader=load_novel_resource)),
):
    from app.core.database import resolve_novel

    novel = resolve_novel(db, novel_id)
    if not novel:
        raise http_error(404, "novel_not_found", "Novel not found")
    rows = db.execute(
        select(CreationTask)
        .where(
            CreationTask.user_uuid == (_.user_uuid or ""),
            CreationTask.task_type == "generation",
            CreationTask.resource_type == "novel",
            CreationTask.resource_id == novel.id,
        )
        .order_by(CreationTask.updated_at.desc(), CreationTask.id.desc())
        .limit(max(1, min(limit, 100)))
    ).scalars().all()
    results = []
    for r in rows:
        payload_data = r.payload_json if isinstance(r.payload_json, dict) else {}
        redis_data = _redis_get_json(_redis_key(r.worker_task_id or r.public_id)) or {}
        results.append({
            "task_id": r.public_id,
            "status": r.status,
            "run_state": r.status,
            "current_chapter": redis_data.get("current_chapter") or 0,
            "total_chapters": redis_data.get("total_chapters") or int(payload_data.get("num_chapters") or 0),
            "progress": r.progress or 0,
            "message": r.message,
            "error": r.error_detail,
            "error_code": r.error_code,
            "error_category": r.error_category,
            "retryable": bool((r.retry_count or 0) < (r.max_retries or 0)),
            "trace_id": None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        })
    return results


@router.post("/{novel_id}/generation/{task_id}/confirm-outline")
def confirm_outline_generation(
    novel_id: str,
    task_id: str,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.NOVEL_GENERATE, resource_loader=load_novel_resource)),
):
    """Confirm outline stage and continue chapter writing."""
    from app.core.database import resolve_novel

    novel = resolve_novel(db, novel_id)
    if not novel:
        raise http_error(404, "novel_not_found", "Novel not found")
    gt_stmt = select(GenerationTask).where(
        GenerationTask.task_id == task_id,
        GenerationTask.novel_id == novel.id,
    )
    gt = db.execute(gt_stmt).scalar_one_or_none()
    if not gt:
        raise http_error(404, "task_not_found", "Task not found")
    if gt.status != "awaiting_outline_confirmation":
        return {"ok": True, "message": "无需确认或已确认"}
    gt.status = "running"
    gt.run_state = "running"
    gt.outline_confirmed = 1
    novel.status = "generating"
    db.commit()
    data = {
        "status": "running",
        "run_state": "running",
        "current_phase": "chapter_writing",
        "step": "chapter_writing",
        "current_subtask": {"key": "chapter_writing", "label": "开始章节生成", "progress": gt.progress or 20},
        "subtask_key": "chapter_writing",
        "subtask_label": "开始章节生成",
        "subtask_progress": gt.progress or 20,
        "current_chapter": gt.current_chapter or 1,
        "total_chapters": gt.total_chapters or gt.num_chapters or 0,
        "progress": gt.progress or 20,
        "message": "已确认大纲，继续生成章节",
        "trace_id": gt.trace_id,
    }
    _redis_set_json(_redis_key(task_id), data)
    _redis_set_json(_novel_key(str(novel.id)), data)
    return {"ok": True, "message": "已确认大纲，任务继续"}


@router.get("/{novel_id}/generation/status", response_model=GenerationStatusResponse)
def get_generation_status(
    novel_id: str,
    task_id: str | None = None,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.NOVEL_READ, resource_loader=load_novel_resource)),
):
    """Get unified generation task status."""
    from app.core.database import resolve_novel
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise http_error(404, "novel_not_found", "Novel not found")
    row: CreationTask | None = None
    if task_id:
        row = get_task_by_public_id(db, public_id=task_id, user_uuid=_.user_uuid or "")
    else:
        row = db.execute(
            select(CreationTask)
            .where(
                CreationTask.user_uuid == (_.user_uuid or ""),
                CreationTask.task_type == "generation",
                CreationTask.resource_type == "novel",
                CreationTask.resource_id == novel.id,
            )
            .order_by(CreationTask.updated_at.desc(), CreationTask.id.desc())
            .limit(1)
        ).scalar_one_or_none()
    if row:
        redis_key_id = row.worker_task_id or row.public_id
        redis_payload = _redis_get_json(_redis_key(redis_key_id))
        result_data = row.result_json if isinstance(row.result_json, dict) else {}
        payload = {
            "status": row.status,
            "run_state": row.status,
            "step": row.phase or row.status,
            "current_phase": row.phase or row.status,
            "current_chapter": 0,
            "total_chapters": 0,
            "progress": float(row.progress or 0.0),
            "token_usage_input": int(result_data.get("token_usage_input") or 0),
            "token_usage_output": int(result_data.get("token_usage_output") or 0),
            "estimated_cost": float(result_data.get("estimated_cost") or 0.0),
            "volume_no": None,
            "volume_size": None,
            "pacing_mode": None,
            "low_progress_streak": None,
            "progress_signal": None,
            "decision_state": None,
            "message": row.message,
            "error": row.error_detail,
            "task_id": row.public_id,
            "trace_id": None,
        }
        if isinstance(redis_payload, dict):
            for k, v in redis_payload.items():
                if k in payload:
                    payload[k] = v
        payload = _estimate_eta_payload(db, novel.id, redis_key_id, payload)
        return _to_status_response(payload)
    return GenerationStatusResponse(status="unknown", progress=0, current_chapter=0)


@router.get("/{novel_id}/generation/{task_id}/llm-debug")
def llm_debug(
    novel_id: str,
    task_id: str,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.NOVEL_READ, resource_loader=load_novel_resource)),
):
    """Expose effective provider/model routing for debugging (base_url masked)."""
    from app.core.database import resolve_novel
    from app.core.strategy import get_model_for_stage
    from app.core.config import get_settings

    novel = resolve_novel(db, novel_id)
    if not novel:
        raise http_error(404, "novel_not_found", "Novel not found")
    gt_stmt = select(GenerationTask).where(GenerationTask.task_id == task_id, GenerationTask.novel_id == novel.id)
    gt = db.execute(gt_stmt).scalar_one_or_none()
    if not gt:
        raise http_error(404, "task_not_found", "Task not found")
    strategy = novel.strategy or "web-novel"
    stages = ["architect", "outliner", "writer", "reviewer", "finalizer"]
    routing = {s: {"provider": get_model_for_stage(strategy, s)[0], "model": get_model_for_stage(strategy, s)[1]} for s in stages}
    settings = get_settings()
    base_url = (settings.llm_base_url or "").strip()
    masked = base_url[:24] + "***" if base_url else ""
    return {"task_id": task_id, "strategy": strategy, "routing": routing, "base_url_masked": masked}


@router.get("/{novel_id}/generation/progress")
def stream_generation_progress(
    novel_id: str,
    task_id: str,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.NOVEL_READ, resource_loader=load_novel_resource)),
):
    """SSE endpoint for real-time generation progress."""

    async def event_stream():
        last = None
        no_data_count = 0
        while True:
            ctask = get_task_by_public_id(db, public_id=task_id, user_uuid=_.user_uuid or "")
            key_id = (ctask.worker_task_id if ctask and ctask.worker_task_id else task_id)
            key = _redis_key(key_id)
            data = None
            try:
                data = _get_redis().get(key)
            except redis.RedisError as exc:
                log_event(logger, "generation.redis.get_failed", level=logging.WARNING, error=str(exc), redis_key=key)
            if data:
                no_data_count = 0
                d = json.loads(data)
                payload = json.dumps(d)
                if payload != last:
                    last = payload
                    yield _sse_status_event(d)
                if d.get("status") in ("completed", "failed", "cancelled", "paused"):
                    break
            else:
                no_data_count += 1
                if ctask:
                    yield _sse_status_event(
                        {
                            "status": ctask.status,
                            "current_phase": ctask.phase,
                            "progress": ctask.progress,
                            "message": ctask.message,
                            "error": ctask.error_detail,
                        }
                    )
                    if ctask.status in {"completed", "failed", "cancelled", "paused"}:
                        break
                if no_data_count >= 60:  # 30 seconds without data
                    yield _sse_status_event({"status": "unknown", "error": "Task not found or expired"})
                    break
            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
