"""Generation submit, status, progress (SSE), cancel routes."""
import json
import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
import redis

from app.core.database import get_db
from app.core.config import get_settings
from app.models.novel import Novel, GenerationTask
from app.schemas.novel import GenerateRequest, GenerateResponse, GenerationStatusResponse
from app.tasks.generation import submit_generation_task

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
        status=payload.get("status", "unknown"),
        step=step,
        current_phase=current_phase,
        subtask_key=subtask_key,
        subtask_label=subtask_label or None,
        subtask_progress=payload.get("subtask_progress", payload.get("progress")),
        current_chapter=payload.get("current_chapter", 0) or 0,
        total_chapters=payload.get("total_chapters", 0) or 0,
        progress=payload.get("progress", 0) or 0,
        token_usage_input=payload.get("token_usage_input", 0) or 0,
        token_usage_output=payload.get("token_usage_output", 0) or 0,
        estimated_cost=payload.get("estimated_cost", 0.0) or 0.0,
        volume_no=payload.get("volume_no"),
        volume_size=payload.get("volume_size"),
        message=payload.get("message"),
        error=payload.get("error"),
    )


@router.post("/{novel_id}/generate", response_model=GenerateResponse)
def submit_generation(novel_id: str, req: GenerateRequest, db: Session = Depends(get_db)):
    """Submit novel generation task. Persists generation_tasks row."""
    from app.core.database import resolve_novel
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise HTTPException(404, "Novel not found")
    active_stmt = select(GenerationTask).where(
        GenerationTask.novel_id == novel.id,
        GenerationTask.status.in_(["submitted", "running", "awaiting_outline_confirmation"]),
    )
    active_task = db.execute(active_stmt).scalar_one_or_none()
    if active_task:
        raise HTTPException(409, f"已有进行中的生成任务: {active_task.task_id}")
    task = submit_generation_task.delay(novel.id, req.num_chapters, req.start_chapter)
    novel.status = "generating"
    novel.config = {**(novel.config or {}), "require_outline_confirmation": bool(req.require_outline_confirmation)}
    gt = GenerationTask(
        task_id=task.id,
        novel_id=novel.id,
        status="submitted",
        current_phase="queued",
        total_chapters=req.num_chapters,
        outline_confirmed=0 if req.require_outline_confirmation else 1,
        num_chapters=req.num_chapters,
        start_chapter=req.start_chapter,
    )
    db.add(gt)
    db.commit()
    return GenerateResponse(task_id=task.id, novel_id=novel.uuid or str(novel.id))


@router.delete("/{novel_id}/generation/{task_id}")
def cancel_generation(novel_id: str, task_id: str, db: Session = Depends(get_db)):
    """Cancel a running generation task."""
    from app.core.database import resolve_novel
    from app.workers.celery_app import app as celery_app

    novel = resolve_novel(db, novel_id)
    if not novel:
        raise HTTPException(404, "Novel not found")

    gt_stmt = select(GenerationTask).where(GenerationTask.task_id == task_id)
    gt = db.execute(gt_stmt).scalar_one_or_none()
    if not gt:
        raise HTTPException(404, "Task not found")

    # Revoke the Celery task
    celery_app.control.revoke(task_id, terminate=True)

    # Update task status
    gt.status = "cancelled"
    novel.status = "draft"
    db.commit()

    # Update Redis
    r = _get_redis()
    data = {
        "status": "cancelled",
        "step": "cancelled",
        "current_phase": "cancelled",
        "subtask_key": "cancelled",
        "subtask_label": "任务已取消",
        "subtask_progress": gt.progress or 0,
        "progress": gt.progress or 0,
        "message": "任务已取消",
    }
    r.setex(_redis_key(task_id), 86400, json.dumps(data))

    return {"ok": True, "message": "Task cancelled"}


@router.post("/{novel_id}/generation/{task_id}/confirm-outline")
def confirm_outline_generation(novel_id: str, task_id: str, db: Session = Depends(get_db)):
    """Confirm outline stage and continue chapter writing."""
    from app.core.database import resolve_novel

    novel = resolve_novel(db, novel_id)
    if not novel:
        raise HTTPException(404, "Novel not found")
    gt_stmt = select(GenerationTask).where(
        GenerationTask.task_id == task_id,
        GenerationTask.novel_id == novel.id,
    )
    gt = db.execute(gt_stmt).scalar_one_or_none()
    if not gt:
        raise HTTPException(404, "Task not found")
    if gt.status != "awaiting_outline_confirmation":
        return {"ok": True, "message": "无需确认或已确认"}
    gt.status = "running"
    gt.outline_confirmed = 1
    novel.status = "generating"
    db.commit()
    r = _get_redis()
    data = {
        "status": "running",
        "current_phase": "chapter_writing",
        "step": "chapter_writing",
        "subtask_key": "chapter_writing",
        "subtask_label": "开始章节生成",
        "subtask_progress": gt.progress or 20,
        "current_chapter": gt.current_chapter or 1,
        "total_chapters": gt.total_chapters or gt.num_chapters or 0,
        "progress": gt.progress or 20,
        "message": "已确认大纲，继续生成章节",
    }
    r.setex(_redis_key(task_id), 86400, json.dumps(data))
    r.setex(_novel_key(str(novel.id)), 86400, json.dumps(data))
    return {"ok": True, "message": "已确认大纲，任务继续"}


@router.get("/{novel_id}/generation/status", response_model=GenerationStatusResponse)
def get_generation_status(novel_id: str, task_id: str | None = None, db: Session = Depends(get_db)):
    """Get generation progress (Redis real-time preferred, DB fallback)."""
    from app.core.database import resolve_novel
    novel = resolve_novel(db, novel_id)
    r = _get_redis()
    redis_novel_id = str(novel.id) if novel else novel_id
    key = _redis_key(task_id) if task_id else _novel_key(redis_novel_id)
    data = _decode_redis_payload(r.get(key))
    if data:
        return _to_status_response(data)

    if task_id:
        gt_stmt = select(GenerationTask).where(GenerationTask.task_id == task_id)
        gt = db.execute(gt_stmt).scalar_one_or_none()
        if gt:
            return _to_status_response(
                {
                    "status": gt.status,
                    "step": gt.step,
                    "current_phase": gt.current_phase,
                    "current_chapter": gt.current_chapter or 0,
                    "total_chapters": gt.total_chapters or gt.num_chapters or 0,
                    "progress": gt.progress or 0,
                    "token_usage_input": gt.token_usage_input or 0,
                    "token_usage_output": gt.token_usage_output or 0,
                    "estimated_cost": gt.estimated_cost or 0.0,
                    "message": gt.message,
                    "error": gt.error,
                }
            )
    return GenerationStatusResponse(status="unknown", progress=0, current_chapter=0)


@router.get("/{novel_id}/generation/{task_id}/llm-debug")
def llm_debug(novel_id: str, task_id: str, db: Session = Depends(get_db)):
    """Expose effective provider/model routing for debugging (base_url masked)."""
    from app.core.database import resolve_novel
    from app.core.strategy import get_model_for_stage
    from app.core.config import get_settings

    novel = resolve_novel(db, novel_id)
    if not novel:
        raise HTTPException(404, "Novel not found")
    gt_stmt = select(GenerationTask).where(GenerationTask.task_id == task_id, GenerationTask.novel_id == novel.id)
    gt = db.execute(gt_stmt).scalar_one_or_none()
    if not gt:
        raise HTTPException(404, "Task not found")
    strategy = novel.strategy or "web-novel"
    stages = ["architect", "outliner", "writer", "reviewer", "finalizer"]
    routing = {s: {"provider": get_model_for_stage(strategy, s)[0], "model": get_model_for_stage(strategy, s)[1]} for s in stages}
    settings = get_settings()
    base_url = (settings.llm_base_url or "").strip()
    masked = base_url[:24] + "***" if base_url else ""
    return {"task_id": task_id, "strategy": strategy, "routing": routing, "base_url_masked": masked}


@router.get("/{novel_id}/generation/progress")
def stream_generation_progress(novel_id: str, task_id: str):
    """SSE endpoint for real-time generation progress."""

    async def event_stream():
        r = _get_redis()
        key = _redis_key(task_id)
        last = None
        no_data_count = 0
        while True:
            data = r.get(key)
            if data:
                no_data_count = 0
                d = json.loads(data)
                payload = json.dumps(d)
                if payload != last:
                    last = payload
                    yield _sse_status_event(d)
                if d.get("status") in ("completed", "failed"):
                    break
            else:
                no_data_count += 1
                if no_data_count >= 60:  # 30 seconds without data
                    yield _sse_status_event({"status": "unknown", "error": "Task not found or expired"})
                    break
            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
