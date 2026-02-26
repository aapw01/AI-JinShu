"""Generation Celery tasks.

Book-level orchestration dispatches volume-level tasks for long-form runs.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import redis
from sqlalchemy import select

from app.workers.celery_app import app
from app.services.generation.pipeline import run_generation_pipeline

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
    r.setex(key, 86400, json.dumps(data, ensure_ascii=False))
    r.setex(novel_key, 86400, json.dumps(data, ensure_ascii=False))


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
    gt.step = data.get("step", gt.step)
    gt.current_phase = data.get("current_phase", gt.current_phase)
    gt.current_chapter = data.get("current_chapter", gt.current_chapter)
    gt.total_chapters = data.get("total_chapters", gt.total_chapters)
    gt.progress = data.get("progress", gt.progress)
    gt.message = data.get("message", gt.message)
    gt.token_usage_input = data.get("token_usage_input", gt.token_usage_input)
    gt.token_usage_output = data.get("token_usage_output", gt.token_usage_output)
    gt.estimated_cost = data.get("estimated_cost", gt.estimated_cost)
    if "error" in data:
        gt.error = data.get("error")
    if "final_report" in data:
        gt.final_report = data["final_report"]
    db.commit()


def _run_volume_generation(
    novel_id: int,
    chunk_chapters: int,
    chunk_start: int,
    parent_task_id: str,
    total_chapters: int,
    total_start_chapter: int,
    volume_no: int,
    volume_size: int,
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
    }
    try:
        cached_raw = r.get(key)
        if cached_raw:
            cached = json.loads(cached_raw)
            if isinstance(cached, dict):
                metric_state["token_usage_input"] = int(cached.get("token_usage_input") or 0)
                metric_state["token_usage_output"] = int(cached.get("token_usage_output") or 0)
                metric_state["estimated_cost"] = float(cached.get("estimated_cost") or 0.0)
    except Exception:
        pass

    def progress_cb(step: str, chapter: int, pct: float, msg: str = "", meta: dict | None = None):
        meta = meta or {}
        # Preserve last known usage/cost to avoid resetting to 0 on non-metric phases.
        if meta.get("token_usage_input") is not None:
            metric_state["token_usage_input"] = int(meta.get("token_usage_input") or 0)
        if meta.get("token_usage_output") is not None:
            metric_state["token_usage_output"] = int(meta.get("token_usage_output") or 0)
        if meta.get("estimated_cost") is not None:
            metric_state["estimated_cost"] = float(meta.get("estimated_cost") or 0.0)
        # Map chunk progress to global progress window [20, 95].
        chunk_ratio = max(0.0, min(1.0, pct / 100.0))
        chunks = max(1, (total_chapters + volume_size - 1) // volume_size)
        global_pct = 20 + ((volume_no - 1 + chunk_ratio) / chunks) * 75
        payload = {
            "status": meta.get("status", "running"),
            "step": step,
            "current_phase": meta.get("current_phase", step),
            "current_subtask": {
                "key": step,
                "label": SUBTASK_LABELS.get(step, step),
                "progress": round(global_pct, 2),
            },
            "current_chapter": chapter,
            "total_chapters": total_chapters,
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
        }
        _set_status(r, key, novel_key, payload)

    run_generation_pipeline(
        novel_id=novel_id,
        num_chapters=chunk_chapters,
        start_chapter=chunk_start,
        progress_callback=progress_cb,
        task_id=parent_task_id,
    )
    return {"ok": True, "volume_no": volume_no, "start": chunk_start, "num_chapters": chunk_chapters}


@app.task(bind=True, acks_late=True, reject_on_worker_lost=True)
def submit_volume_generation_task(
    self,
    novel_id: int,
    chunk_chapters: int,
    chunk_start: int,
    parent_task_id: str,
    total_chapters: int,
    total_start_chapter: int,
    volume_no: int,
    volume_size: int,
) -> dict[str, Any]:
    """Run one volume chunk as an independent task."""
    return _run_volume_generation(
        novel_id=novel_id,
        chunk_chapters=chunk_chapters,
        chunk_start=chunk_start,
        parent_task_id=parent_task_id,
        total_chapters=total_chapters,
        total_start_chapter=total_start_chapter,
        volume_no=volume_no,
        volume_size=volume_size,
    )


@app.task(bind=True, acks_late=True, reject_on_worker_lost=True)
def submit_book_generation_task(self, novel_id: str, num_chapters: int, start_chapter: int):
    """Book-level orchestrator: split into volume tasks and execute sequentially."""
    from app.core.config import get_settings
    from app.core.database import SessionLocal
    from app.models.novel import Novel

    settings = get_settings()
    r = redis.from_url(settings.redis_url)
    task_id = self.request.id
    key = f"generation:{task_id}"
    novel_key = f"generation:novel:{novel_id}"
    db = SessionLocal()

    try:
        from app.models.novel import GenerationTask

        gt_stmt = select(GenerationTask).where(GenerationTask.task_id == task_id)
        gt = db.execute(gt_stmt).scalar_one_or_none()
        if gt and gt.status in {"completed", "cancelled"}:
            logger.info("Skip replay for task %s because status=%s", task_id, gt.status)
            return task_id

        novel_stmt = select(Novel).where(Novel.id == novel_id)
        novel = db.execute(novel_stmt).scalar_one_or_none()
        volume_size = int(((novel.config or {}).get("volume_size") or 30)) if novel else 30
        chunks = _volume_chunks(start_chapter, num_chapters, volume_size)

        data = {
            "status": "running",
            "step": "book_orchestrator",
            "current_phase": "book_planning",
            "current_subtask": {"key": "book_planning", "label": SUBTASK_LABELS.get("book_planning"), "progress": 5},
            "current_chapter": start_chapter,
            "total_chapters": num_chapters,
            "progress": 5,
            "volume_no": 1,
            "volume_size": volume_size,
            "message": f"总控任务已启动，拆分为{len(chunks)}个卷任务",
        }
        _set_status(r, key, novel_key, data)
        _persist_generation_task(db, task_id, data)

        for volume_no, chunk_start, chunk_len in chunks:
            announce = {
                "status": "running",
                "step": "volume_dispatch",
                "current_phase": "volume_dispatch",
                "current_subtask": {"key": "volume_dispatch", "label": SUBTASK_LABELS.get("volume_dispatch")},
                "current_chapter": chunk_start,
                "total_chapters": num_chapters,
                "progress": round(10 + ((volume_no - 1) / max(len(chunks), 1)) * 70, 2),
                "volume_no": volume_no,
                "volume_size": volume_size,
                "message": f"开始第{volume_no}卷（第{chunk_start}章起，共{chunk_len}章）",
            }
            _set_status(r, key, novel_key, announce)
            _persist_generation_task(db, task_id, announce)
            _run_volume_generation(
                novel_id=int(novel_id),
                chunk_chapters=chunk_len,
                chunk_start=chunk_start,
                parent_task_id=task_id,
                total_chapters=num_chapters,
                total_start_chapter=start_chapter,
                volume_no=volume_no,
                volume_size=volume_size,
            )

        data = {
            "status": "completed",
            "step": "done",
            "current_phase": "completed",
            "current_subtask": {"key": "done", "label": SUBTASK_LABELS.get("done"), "progress": 100},
            "progress": 100,
            "current_chapter": start_chapter + num_chapters - 1,
            "total_chapters": num_chapters,
            "volume_no": chunks[-1][0] if chunks else 1,
            "volume_size": volume_size,
            "message": "总控任务完成",
        }
        if novel:
            novel.status = "completed"
            db.commit()
    except Exception as e:
        logger.error(f"Book generation failed for novel {novel_id}: {e}")
        data = {
            "status": "failed",
            "step": "failed",
            "current_phase": "failed",
            "current_subtask": {"key": "failed", "label": SUBTASK_LABELS.get("failed"), "progress": 0},
            "progress": 0,
            "total_chapters": num_chapters,
            "error": str(e),
            "message": "总控任务失败",
        }
        novel_stmt = select(Novel).where(Novel.id == novel_id)
        novel = db.execute(novel_stmt).scalar_one_or_none()
        if novel:
            novel.status = "failed"
            db.commit()
    finally:
        _set_status(r, key, novel_key, data)
        try:
            _persist_generation_task(db, task_id, data)
        except Exception as e:  # pragma: no cover
            logger.error(f"Failed to update final status in DB: {e}")
            db.rollback()
        db.close()

    return task_id


# Keep old task name as public API (directly mapped to new book orchestrator).
submit_generation_task = submit_book_generation_task
