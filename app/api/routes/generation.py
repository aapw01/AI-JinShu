"""Generation submit, status, progress (SSE), cancel routes."""
import json
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any
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
from app.models.creation_task import CreationTask
from app.models.novel import GenerationTask, GenerationCheckpoint, User
from app.schemas.novel import GenerateRequest, GenerateResponse, GenerationStatusResponse, RetryGenerationRequest
from app.services.generation.status_snapshot import (
    GENERATION_CACHE_ACTIVE_STATUSES,
    SUBTASK_LABELS,
    build_generation_snapshot,
    creation_task_display_totals as snapshot_creation_task_display_totals,
    creation_task_effective_phase as snapshot_creation_task_effective_phase,
    creation_task_effective_status as snapshot_creation_task_effective_status,
    creation_task_payload as snapshot_creation_task_payload,
    creation_task_runtime_state as snapshot_creation_task_runtime_state,
    creation_task_waiting_outline_confirmation as snapshot_creation_task_waiting_outline_confirmation,
    generation_novel_key as snapshot_novel_key,
    generation_task_key as snapshot_task_key,
    read_generation_cache,
    sync_generation_novel_snapshot,
    write_generation_cache,
)
from app.services.quota import check_generation_quota
from app.services.scheduler.scheduler_service import (
    cancel_task,
    dispatch_user_queue_for_user,
    get_task_by_public_id,
    pause_task,
    resume_task,
    submit_task,
)
from app.services.rewrite.service import get_default_version_id

router = APIRouter()
logger = logging.getLogger(__name__)

# Redis connection pool for better performance
_redis_pool = None


def _get_redis():
    """Get Redis connection from pool."""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = redis.ConnectionPool.from_url(get_settings().redis_url)
    return redis.Redis(connection_pool=_redis_pool)


def _redis_get_json(key: str) -> dict | None:
    payload = read_generation_cache(key)
    if payload is not None:
        return payload
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
    return snapshot_task_key(task_id)


def _novel_key(novel_id: str) -> str:
    return snapshot_novel_key(novel_id)


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
        error_code=payload.get("error_code"),
        error_category=payload.get("error_category"),
        retryable=payload.get("retryable"),
        last_error=payload.get("last_error"),
    )


def _creation_task_runtime_state(row: CreationTask) -> dict:
    return snapshot_creation_task_runtime_state(row)


def _creation_task_payload(row: CreationTask) -> dict:
    return snapshot_creation_task_payload(row)


def _payload_book_start(payload_data: dict) -> int:
    return int(payload_data.get("book_start_chapter") or payload_data.get("start_chapter") or 1)


def _payload_book_total(payload_data: dict) -> int:
    return int(
        payload_data.get("book_target_total_chapters")
        or payload_data.get("original_total_chapters")
        or payload_data.get("num_chapters")
        or 0
    )


def _payload_book_end(payload_data: dict) -> int:
    book_start = _payload_book_start(payload_data)
    book_total = max(0, _payload_book_total(payload_data))
    return int(book_start + max(book_total - 1, 0))


def _creation_task_waiting_outline_confirmation(row: CreationTask) -> bool:
    return snapshot_creation_task_waiting_outline_confirmation(row)


def _creation_task_effective_status(row: CreationTask) -> str:
    return snapshot_creation_task_effective_status(row)


def _creation_task_effective_phase(row: CreationTask) -> str:
    return snapshot_creation_task_effective_phase(row)


def _find_generation_creation_task(
    db: Session,
    *,
    task_id: str,
    user_uuid: str,
    novel_db_id: int,
) -> CreationTask | None:
    row = get_task_by_public_id(db, public_id=task_id, user_uuid=user_uuid)
    if row and row.task_type == "generation" and row.resource_type == "novel" and int(row.resource_id) == int(novel_db_id):
        return row
    return db.execute(
        select(CreationTask)
        .where(
            CreationTask.user_uuid == user_uuid,
            CreationTask.task_type == "generation",
            CreationTask.resource_type == "novel",
            CreationTask.resource_id == int(novel_db_id),
            CreationTask.worker_task_id == task_id,
        )
        .order_by(CreationTask.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _creation_task_display_totals(row: CreationTask) -> tuple[int, int]:
    return snapshot_creation_task_display_totals(row)


def _resolve_live_chapter_display(
    *,
    redis_payload: dict | None,
    db_current_chapter: int,
    db_total_chapters: int,
) -> tuple[int, int]:
    current = int(db_current_chapter or 0)
    total = int(db_total_chapters or 0)
    if not isinstance(redis_payload, dict):
        return current, total

    redis_current = int(redis_payload.get("current_chapter") or 0)
    redis_total = int(redis_payload.get("total_chapters") or 0)
    live_phase = str(redis_payload.get("current_phase") or redis_payload.get("step") or "")

    if live_phase == "chapter_done" and current > redis_current > 0:
        resolved_current = current
    else:
        resolved_current = redis_current or current
    resolved_total = redis_total or total
    if total > 0:
        resolved_total = max(int(resolved_total or 0), total)
    return int(resolved_current), int(resolved_total)


def _publish_generation_snapshot(
    db: Session,
    row: CreationTask,
    *,
    live_payload: dict | None = None,
    stale_worker_ids: list[str] | None = None,
) -> dict[str, Any]:
    snapshot = build_generation_snapshot(row, live_payload=live_payload)
    write_generation_cache(
        task_public_id=row.public_id,
        novel_id=int(row.resource_id),
        payload=snapshot,
        worker_task_id=row.worker_task_id,
        mirror_worker=False,
        clear_worker_ids=stale_worker_ids or [],
        mirror_novel=_creation_task_effective_status(row) in GENERATION_CACHE_ACTIVE_STATUSES,
    )
    if _creation_task_effective_status(row) not in GENERATION_CACHE_ACTIVE_STATUSES:
        sync_generation_novel_snapshot(db, novel_id=int(row.resource_id))
    return snapshot


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
            "book_start_chapter": int(req.start_chapter),
            "book_target_total_chapters": int(req.num_chapters),
            "trace_id": trace_id,
        },
    )
    novel.status = "generating"
    novel.config = {**(novel.config or {}), "require_outline_confirmation": bool(req.require_outline_confirmation)}
    db.commit()
    db.refresh(creation_task)
    _publish_generation_snapshot(db, creation_task)
    dispatch_user_queue_for_user(user_uuid=principal.user_uuid or "")
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
        dispatch_user_queue_for_user(user_uuid=principal.user_uuid or "")
        db.refresh(resumed)
        _publish_generation_snapshot(db, resumed)
        cursor = resumed.resume_cursor_json if isinstance(resumed.resume_cursor_json, dict) else {}
        next_ch = cursor.get("next")
        payload_data = resumed.payload_json or {}
        total_ch = int(
            payload_data.get("book_target_total_chapters")
            or payload_data.get("original_total_chapters")
            or payload_data.get("num_chapters")
            or 0
        )
        log_event(
            logger,
            "generation.retry.submit",
            novel_id=novel.id,
            user_id=principal.user_uuid,
            task_id=resumed.public_id,
            run_state=resumed.status,
            chapter_num=int(next_ch) if next_ch is not None else 0,
            total_chapters=total_ch,
        )
        return GenerateResponse(task_id=resumed.public_id, novel_id=novel.uuid or str(novel.id), status="queued")

    # 3) If source_creation exists but is cancelled, create new task from its resume_cursor
    if source_creation and source_creation.status == "cancelled":
        payload_data = source_creation.payload_json or {}
        start_ch = int(payload_data.get("start_chapter") or 1)
        num_ch = int(payload_data.get("num_chapters") or 1)
        original_total = int(payload_data.get("original_total_chapters") or num_ch)
        source_book_start = int(payload_data.get("book_start_chapter") or start_ch)
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
                "original_total_chapters": int(original_total),
                "book_start_chapter": int(source_book_start),
                "book_target_total_chapters": int(original_total),
                "trace_id": trace_id,
            },
        )
        novel.status = "generating"
        db.commit()
        db.refresh(creation_task)
        _publish_generation_snapshot(db, creation_task)
        dispatch_user_queue_for_user(user_uuid=principal.user_uuid or "")
        log_event(logger, "generation.retry.submit", novel_id=novel.id, user_id=principal.user_uuid, task_id=creation_task.public_id, run_state="queued", chapter_num=retry_start, total_chapters=original_total)
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
    original_total = source_total
    source_book_start = source_start
    source_end = source_start + max(1, source_total) - 1
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
        legacy_payload = legacy_creation.payload_json or {}
        original_total = int(legacy_payload.get("original_total_chapters") or legacy_payload.get("num_chapters") or source_total)
        source_book_start = int(legacy_payload.get("book_start_chapter") or source_book_start)
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
            "original_total_chapters": int(original_total),
            "book_start_chapter": int(source_book_start),
            "book_target_total_chapters": int(original_total),
            "trace_id": trace_id,
        },
    )
    novel.status = "generating"
    db.commit()
    db.refresh(creation_task)
    _publish_generation_snapshot(db, creation_task)
    dispatch_user_queue_for_user(user_uuid=principal.user_uuid or "")
    log_event(
        logger,
        "generation.retry.submit",
        novel_id=novel.id,
        user_id=principal.user_uuid,
        task_id=creation_task.public_id,
        run_state="queued",
        chapter_num=retry_start,
        total_chapters=original_total,
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

    novel = resolve_novel(db, novel_id)
    if not novel:
        raise http_error(404, "novel_not_found", "Novel not found")

    ctask = get_task_by_public_id(db, public_id=task_id, user_uuid=_.user_uuid or "")
    if not ctask:
        raise http_error(404, "task_not_found", "Task not found")
    if ctask.status in {"completed", "failed", "cancelled"}:
        return {"ok": True, "message": f"Task already {ctask.status}"}
    stale_worker_id = ctask.worker_task_id
    try:
        cancel_task(db, public_id=task_id, user_uuid=_.user_uuid or "")
    except ValueError:
        raise http_error(409, "task_not_cancellable", "当前任务不可取消")
    novel.status = "cancelled"
    db.commit()
    dispatch_user_queue_for_user(user_uuid=_.user_uuid or "")
    db.refresh(ctask)
    _publish_generation_snapshot(db, ctask, stale_worker_ids=[str(stale_worker_id)] if stale_worker_id else None)
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
    stale_worker_id = row.worker_task_id
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
    dispatch_user_queue_for_user(user_uuid=principal.user_uuid or "")
    db.refresh(row)
    _publish_generation_snapshot(db, row, stale_worker_ids=[str(stale_worker_id)] if stale_worker_id else None)
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
    old_worker_task_id = row.worker_task_id
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
    dispatch_user_queue_for_user(user_uuid=principal.user_uuid or "")
    db.refresh(row)
    _publish_generation_snapshot(db, row, stale_worker_ids=[str(old_worker_task_id)] if old_worker_task_id else None)
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
    if row.status in {"completed", "failed", "cancelled"}:
        return {"ok": True, "task_id": row.public_id, "run_state": row.status}
    stale_worker_id = row.worker_task_id
    try:
        cancel_task(db, public_id=row.public_id, user_uuid=principal.user_uuid or "")
    except ValueError as exc:
        code = str(exc)
        if code == "task_not_found":
            raise http_error(404, "task_not_found", "Task not found")
        raise http_error(409, "task_not_cancellable", "当前任务不可取消")
    novel.status = "cancelled"
    db.commit()
    dispatch_user_queue_for_user(user_uuid=principal.user_uuid or "")
    db.refresh(row)
    _publish_generation_snapshot(db, row, stale_worker_ids=[str(stale_worker_id)] if stale_worker_id else None)
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
        runtime_state = _creation_task_runtime_state(r)
        snapshot = build_generation_snapshot(
            r,
            live_payload=_redis_get_json(_redis_key(r.public_id)),
        )
        results.append({
            "task_id": r.public_id,
            "status": snapshot.get("status"),
            "run_state": snapshot.get("run_state"),
            "current_chapter": snapshot.get("current_chapter"),
            "total_chapters": snapshot.get("total_chapters"),
            "progress": snapshot.get("progress"),
            "message": snapshot.get("message"),
            "error": snapshot.get("error"),
            "error_code": snapshot.get("error_code"),
            "error_category": snapshot.get("error_category"),
            "retryable": snapshot.get("retryable"),
            "trace_id": snapshot.get("trace_id"),
            "volume_no": snapshot.get("volume_no") or int(runtime_state.get("volume_no") or 0) or None,
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
    row = _find_generation_creation_task(
        db,
        task_id=task_id,
        user_uuid=_.user_uuid or "",
        novel_db_id=int(novel.id),
    )
    if row and row.task_type == "generation" and row.resource_type == "novel" and int(row.resource_id) == int(novel.id):
        payload_data = _creation_task_payload(row)
        if not bool(payload_data.get("awaiting_outline_confirmation")) or bool(payload_data.get("outline_confirmed")):
            return {"ok": True, "message": "无需确认或已确认"}
        payload_data["awaiting_outline_confirmation"] = False
        payload_data["outline_confirmed"] = True
        row.payload_json = payload_data
        row.phase = "chapter_writing"
        row.message = "已确认大纲，继续生成章节"
        novel.status = "generating"

        gt = None
        if row.worker_task_id:
            gt = db.execute(
                select(GenerationTask).where(
                    GenerationTask.task_id == row.worker_task_id,
                    GenerationTask.novel_id == novel.id,
                )
            ).scalar_one_or_none()
        if gt:
            gt.status = "running"
            gt.run_state = "running"
            gt.current_phase = "chapter_writing"
            gt.outline_confirmed = 1

        db.commit()
        db.refresh(row)
        _publish_generation_snapshot(db, row)
        return {"ok": True, "message": "已确认大纲，任务继续"}

    gt_stmt = select(GenerationTask).where(
        GenerationTask.task_id == task_id,
        GenerationTask.novel_id == novel.id,
    )
    gt = db.execute(gt_stmt).scalar_one_or_none()
    if not gt:
        raise http_error(404, "task_not_found", "Task not found")
    if gt.status != "awaiting_outline_confirmation":
        return {"ok": True, "message": "无需确认或已确认"}
    row = db.execute(
        select(CreationTask)
        .where(
            CreationTask.user_uuid == (_.user_uuid or ""),
            CreationTask.task_type == "generation",
            CreationTask.resource_type == "novel",
            CreationTask.resource_id == int(novel.id),
            CreationTask.worker_task_id == task_id,
        )
        .order_by(CreationTask.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row:
        payload_data = _creation_task_payload(row)
        payload_data["awaiting_outline_confirmation"] = False
        payload_data["outline_confirmed"] = True
        row.payload_json = payload_data
        row.phase = "chapter_writing"
        row.message = "已确认大纲，继续生成章节"
    gt.status = "running"
    gt.run_state = "running"
    gt.outline_confirmed = 1
    gt.current_phase = "chapter_writing"
    novel.status = "generating"
    db.commit()
    if row:
        db.refresh(row)
        _publish_generation_snapshot(db, row)
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
        row = _find_generation_creation_task(
            db,
            task_id=task_id,
            user_uuid=_.user_uuid or "",
            novel_db_id=int(novel.id),
        )
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
        payload = build_generation_snapshot(
            row,
            live_payload=_redis_get_json(_redis_key(row.public_id)),
        )
        payload = _estimate_eta_payload(db, novel.id, row.worker_task_id or row.public_id, payload)
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
    from app.core.database import resolve_novel

    novel = resolve_novel(db, novel_id)
    if not novel:
        raise http_error(404, "novel_not_found", "Novel not found")

    _TERMINAL = {"completed", "failed", "cancelled", "paused"}

    async def event_stream():
        last = None
        no_data_count = 0
        while True:
            db.expire_all()
            ctask = _find_generation_creation_task(
                db,
                task_id=task_id,
                user_uuid=_.user_uuid or "",
                novel_db_id=int(novel.id),
            )
            if ctask:
                no_data_count = 0
                d = build_generation_snapshot(
                    ctask,
                    live_payload=_redis_get_json(_redis_key(ctask.public_id)),
                )
                payload = json.dumps(d, ensure_ascii=False)
                if payload != last:
                    last = payload
                    yield _sse_status_event(d)
                if d.get("status") in _TERMINAL:
                    break
            else:
                no_data_count += 1
                if no_data_count >= 60:
                    yield _sse_status_event({"status": "unknown", "error": "Task not found or expired"})
                    break
            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
