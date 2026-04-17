"""生成任务状态快照与缓存发布。

模块职责：
- 把 `CreationTask` + runtime_state 转成前端可消费的统一快照。
- 负责 Redis 读写、任务维度/小说维度镜像、worker stale key 清理。

面试可讲点：
- 为什么前端状态不直接读 Celery，而是读业务快照。
- 为什么需要 task / novel 两个 cache key 视角。
"""
from __future__ import annotations

import json
from typing import Any

import redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.creation_task import CreationTask

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
    "paused": "任务已暂停",
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

GENERATION_ACTIVE_STATUSES = {"queued", "dispatching", "running", "awaiting_outline_confirmation"}
GENERATION_CACHE_ACTIVE_STATUSES = {"queued", "dispatching", "running", "paused", "awaiting_outline_confirmation"}
GENERATION_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
GENERATION_NON_LIVE_STATUSES = GENERATION_TERMINAL_STATUSES | {"paused"}

_redis_pool: redis.ConnectionPool | None = None
def get_generation_redis() -> redis.Redis:
    """惰性初始化生成任务专用的 Redis 客户端。"""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = redis.ConnectionPool.from_url(get_settings().redis_url)
    return redis.Redis(connection_pool=_redis_pool)


def generation_task_key(task_id: str) -> str:
    """返回任务维度的 generation cache key。"""
    return f"generation:{task_id}"


def generation_novel_key(novel_id: int | str) -> str:
    """返回小说维度的 generation cache key。"""
    return f"generation:novel:{novel_id}"


def read_generation_cache(key: str) -> dict[str, Any] | None:
    """优先从 Redis 读取生成快照，失败时回退到数据库重建。"""
    try:
        raw = get_generation_redis().get(key)
    except redis.RedisError:
        raw = None
    payload = decode_generation_cache(raw)
    if payload is not None:
        return payload
    return read_generation_cache_from_db(key)


def read_generation_cache_from_db(key: str) -> dict[str, Any] | None:
    """当 Redis miss 时，从数据库推导当前最可信的生成状态。"""
    db = SessionLocal()
    try:
        if key.startswith("generation:novel:"):
            novel_id = key.removeprefix("generation:novel:")
            active = latest_active_generation_task(db, novel_id=int(novel_id))
            if not active:
                return None
            return build_generation_snapshot(active)

        if not key.startswith("generation:"):
            return None

        task_or_worker_id = key.removeprefix("generation:")
        row = db.execute(
            select(CreationTask).where(
                CreationTask.public_id == task_or_worker_id,
                CreationTask.task_type == "generation",
                CreationTask.resource_type == "novel",
            )
        ).scalar_one_or_none()
        if row is None:
            row = db.execute(
                select(CreationTask)
                .where(
                    CreationTask.worker_task_id == task_or_worker_id,
                    CreationTask.task_type == "generation",
                    CreationTask.resource_type == "novel",
                )
                .order_by(CreationTask.updated_at.desc(), CreationTask.id.desc())
                .limit(1)
            ).scalar_one_or_none()
        if row is None:
            return None
        return build_generation_snapshot(row)
    finally:
        db.close()


def decode_generation_cache(raw: bytes | str | None) -> dict[str, Any] | None:
    """把 Redis 原始值安全解码为字典快照。"""
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    return dict(payload) if isinstance(payload, dict) else None


def write_generation_cache(
    *,
    task_public_id: str,
    novel_id: int | str | None,
    payload: dict[str, Any],
    worker_task_id: str | None = None,
    mirror_worker: bool = False,
    clear_worker_ids: list[str] | None = None,
    mirror_novel: bool = True,
) -> dict[str, Any]:
    """把生成快照写入 Redis，并按需要镜像到小说/worker 维度。

    终态和暂停态使用更长 TTL，方便前端刷新页面后仍能看到最近结果。
    """
    snapshot = with_subtask(payload)
    status = str(snapshot.get("status") or "")
    ttl = 172800 if status in GENERATION_NON_LIVE_STATUSES else 86400
    encoded = json.dumps(snapshot, ensure_ascii=False)
    try:
        r = get_generation_redis()
        r.setex(generation_task_key(task_public_id), ttl, encoded)
        if mirror_novel and novel_id is not None:
            r.setex(generation_novel_key(novel_id), ttl, encoded)
        if mirror_worker and worker_task_id:
            r.setex(generation_task_key(worker_task_id), ttl, encoded)
        for stale_worker_id in clear_worker_ids or []:
            if stale_worker_id:
                r.delete(generation_task_key(stale_worker_id))
    except redis.RedisError:
        return snapshot
    return snapshot


def delete_generation_worker_cache(worker_task_id: str | None) -> None:
    """删除某个 worker 维度的缓存快照。"""
    if not worker_task_id:
        return
    try:
        get_generation_redis().delete(generation_task_key(worker_task_id))
    except redis.RedisError:
        return


def delete_generation_novel_cache(novel_id: int | str | None) -> None:
    """删除小说维度的生成缓存快照。"""
    if novel_id is None:
        return
    try:
        get_generation_redis().delete(generation_novel_key(novel_id))
    except redis.RedisError:
        return


def with_subtask(payload: dict[str, Any]) -> dict[str, Any]:
    """把 step/current_phase 归一化成前端统一展示的子任务结构。"""
    merged = dict(payload)
    step = str(merged.get("subtask_key") or merged.get("step") or merged.get("current_phase") or "").strip()
    if not step:
        return merged
    label = merged.get("subtask_label") or SUBTASK_LABELS.get(step, step)
    progress = merged.get("subtask_progress")
    if progress is None:
        progress = merged.get("progress")
    merged["subtask_key"] = step
    merged["subtask_label"] = label
    if progress is not None:
        merged["subtask_progress"] = progress
    merged["current_subtask"] = {
        "key": step,
        "label": label,
        "progress": merged.get("subtask_progress"),
    }
    return merged


def creation_task_payload(row: CreationTask) -> dict[str, Any]:
    """读取统一任务表中的业务 payload。"""
    payload = row.payload_json if isinstance(row.payload_json, dict) else {}
    return dict(payload)


def creation_task_runtime_state(row: CreationTask) -> dict[str, Any]:
    """读取 `resume_cursor_json.runtime_state`。"""
    cursor = row.resume_cursor_json if isinstance(row.resume_cursor_json, dict) else {}
    runtime_state = cursor.get("runtime_state")
    return dict(runtime_state) if isinstance(runtime_state, dict) else {}


def payload_book_start(payload_data: dict[str, Any]) -> int:
    """从 payload 推导整本小说的起始章节。"""
    return int(payload_data.get("book_start_chapter") or payload_data.get("start_chapter") or 1)


def payload_book_total(payload_data: dict[str, Any]) -> int:
    """从 payload 推导整本小说的目标总章节数。"""
    return int(
        payload_data.get("book_target_total_chapters")
        or payload_data.get("original_total_chapters")
        or payload_data.get("num_chapters")
        or 0
    )


def payload_book_end(payload_data: dict[str, Any]) -> int:
    """根据起始章节和目标总章数推导整本小说结束章。"""
    book_start = payload_book_start(payload_data)
    book_total = max(0, payload_book_total(payload_data))
    return int(book_start + max(book_total - 1, 0))


def creation_task_waiting_outline_confirmation(row: CreationTask) -> bool:
    """判断任务是否处于“等待大纲确认”的特殊前端态。"""
    payload = creation_task_payload(row)
    return bool(payload.get("awaiting_outline_confirmation")) and not bool(payload.get("outline_confirmed"))


def creation_task_effective_status(row: CreationTask) -> str:
    """返回前端应该展示的有效状态，而不是原始数据库状态。"""
    if row.status in {"queued", "dispatching", "running"} and creation_task_waiting_outline_confirmation(row):
        return "awaiting_outline_confirmation"
    return str(row.status or "unknown")


def creation_task_effective_phase(row: CreationTask) -> str:
    """返回任务对前端展示时应使用的有效阶段。"""
    if row.status in {"queued", "dispatching", "running"} and creation_task_waiting_outline_confirmation(row):
        return "outline_ready"
    return str(row.phase or row.status or "unknown")


def creation_task_display_totals(row: CreationTask) -> tuple[int, int]:
    """计算前端展示任务进度时应使用的当前章节与总章节数。"""
    payload_data = creation_task_payload(row)
    cursor = row.resume_cursor_json if isinstance(row.resume_cursor_json, dict) else {}
    runtime_state = creation_task_runtime_state(row)
    book_start = payload_book_start(payload_data)
    default_total = payload_book_total(payload_data)
    payload_end = payload_book_end(payload_data)
    effective_end = max(int(runtime_state.get("book_effective_end_chapter") or 0), payload_end)
    effective_total = max(int(runtime_state.get("book_target_total_chapters") or 0), default_total, effective_end)
    next_chapter = int(runtime_state.get("next_chapter") or cursor.get("next") or book_start)
    mode = str(runtime_state.get("mode") or "").strip()
    if row.status == "completed" or mode == "completed":
        current_chapter = max(book_start, effective_end)
    elif mode == "book_final_review_pending":
        current_chapter = max(book_start, effective_end)
    else:
        current_chapter = max(book_start, next_chapter)
    return int(current_chapter), int(effective_total)


def resolve_live_chapter_display(
    *,
    redis_payload: dict[str, Any] | None,
    db_current_chapter: int,
    db_total_chapters: int,
) -> tuple[int, int]:
    """综合 Redis 实时快照和数据库状态，解析前端应展示的章节进度。"""
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


def build_generation_snapshot(
    row: CreationTask,
    *,
    live_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构建生成snapshot。"""
    payload_data = creation_task_payload(row)
    result_data = row.result_json if isinstance(row.result_json, dict) else {}
    runtime_state = creation_task_runtime_state(row)
    current_chapter, total_chapters = creation_task_display_totals(row)
    effective_status = creation_task_effective_status(row)
    effective_phase = creation_task_effective_phase(row)

    snapshot: dict[str, Any] = {
        "task_id": row.public_id,
        "status": effective_status,
        "run_state": effective_status,
        "step": effective_phase,
        "current_phase": effective_phase,
        "current_chapter": current_chapter,
        "total_chapters": total_chapters,
        "progress": float(row.progress or (100.0 if row.status == "completed" else 0.0) or 0.0),
        "token_usage_input": int(result_data.get("token_usage_input") or 0),
        "token_usage_output": int(result_data.get("token_usage_output") or 0),
        "estimated_cost": float(result_data.get("estimated_cost") or 0.0),
        "volume_no": int(runtime_state.get("volume_no") or 0) or None,
        "volume_size": None,
        "pacing_mode": None,
        "low_progress_streak": None,
        "progress_signal": None,
        "decision_state": None,
        "eta_seconds": None,
        "eta_label": None,
        "message": row.message,
        "error": row.error_detail,
        "error_code": row.error_code,
        "error_category": row.error_category,
        "retryable": bool((row.retry_count or 0) < (row.max_retries or 0)),
        "trace_id": result_data.get("trace_id") or payload_data.get("trace_id"),
    }
    snapshot = with_subtask(snapshot)

    if isinstance(live_payload, dict) and row.status not in GENERATION_NON_LIVE_STATUSES:
        for key in (
            "run_state",
            "step",
            "current_phase",
            "progress",
            "token_usage_input",
            "token_usage_output",
            "estimated_cost",
            "volume_no",
            "volume_size",
            "pacing_mode",
            "low_progress_streak",
            "progress_signal",
            "decision_state",
            "message",
            "trace_id",
            "eta_seconds",
            "eta_label",
            "subtask_key",
            "subtask_label",
            "subtask_progress",
            "current_subtask",
        ):
            value = live_payload.get(key)
            if value is not None:
                snapshot[key] = value
        live_current, live_total = resolve_live_chapter_display(
            redis_payload=live_payload,
            db_current_chapter=current_chapter,
            db_total_chapters=total_chapters,
        )
        snapshot["current_chapter"] = live_current
        snapshot["total_chapters"] = live_total
        snapshot = with_subtask(snapshot)

    if row.status in GENERATION_NON_LIVE_STATUSES:
        snapshot["status"] = row.status
        snapshot["run_state"] = row.status
        snapshot["step"] = row.phase or row.status
        snapshot["current_phase"] = row.phase or row.status
        snapshot["progress"] = float(row.progress or snapshot.get("progress") or 0.0)
        snapshot["message"] = row.message or snapshot.get("message")
        snapshot["error"] = row.error_detail or snapshot.get("error")
        snapshot["error_code"] = row.error_code or snapshot.get("error_code")
        snapshot["error_category"] = row.error_category or snapshot.get("error_category")
        snapshot["retryable"] = bool((row.retry_count or 0) < (row.max_retries or 0))
        snapshot = with_subtask(snapshot)
    return snapshot


def read_generation_snapshot_for_task(row: CreationTask) -> dict[str, Any]:
    """读取单个生成任务的最新快照。"""
    live_payload = read_generation_cache(generation_task_key(row.public_id))
    return build_generation_snapshot(row, live_payload=live_payload)


def latest_active_generation_task(db: Session, *, novel_id: int) -> CreationTask | None:
    """返回指定小说最近一条仍处于活动态的生成任务。"""
    return db.execute(
        select(CreationTask)
        .where(
            CreationTask.task_type == "generation",
            CreationTask.resource_type == "novel",
            CreationTask.resource_id == int(novel_id),
            CreationTask.status.in_(tuple(GENERATION_CACHE_ACTIVE_STATUSES)),
        )
        .order_by(CreationTask.updated_at.desc(), CreationTask.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def sync_generation_novel_snapshot(db: Session, *, novel_id: int) -> None:
    """同步生成小说snapshot。"""
    active = latest_active_generation_task(db, novel_id=novel_id)
    if not active:
        delete_generation_novel_cache(novel_id)
        return
    snapshot = read_generation_snapshot_for_task(active)
    write_generation_cache(
        task_public_id=active.public_id,
        novel_id=novel_id,
        payload=snapshot,
        worker_task_id=active.worker_task_id,
        mirror_worker=False,
        mirror_novel=True,
    )
