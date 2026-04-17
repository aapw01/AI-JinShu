"""统一任务的租约与心跳辅助。

模块职责：
- 为运行中的长任务刷新 `last_heartbeat_at` 和 `worker_lease_expires_at`。
- 为同步阻塞较久的 LLM 调用提供后台心跳线程。

面试可讲点：
- 心跳和租约不是一回事：心跳是“我还活着”，租约是“这条任务何时可被别人接管”。
- 为什么长时间 LLM 调用期间仍需要后台 heartbeat。
"""
from __future__ import annotations

import contextlib
import logging
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.creation_task import CreationTask

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    """返回当前 UTC 时间，统一任务与数据库时间基准。"""
    return datetime.now(timezone.utc)


def acquire_or_refresh_lease(db: Session, *, creation_task_id: int, ttl_seconds: int) -> CreationTask | None:
    """获取或刷新任务租约，并顺带更新时间戳形式的心跳。"""
    row = db.execute(
        select(CreationTask).where(CreationTask.id == creation_task_id).with_for_update()
    ).scalar_one_or_none()
    if not row:
        return None
    now = _utc_now()
    ttl = max(5, int(ttl_seconds))
    row.last_heartbeat_at = now
    row.worker_lease_expires_at = now + timedelta(seconds=ttl)
    db.flush()
    return row


def release_lease(db: Session, *, creation_task_id: int) -> CreationTask | None:
    """主动释放租约，表示当前 worker 已不再占有这条任务。"""
    row = db.execute(
        select(CreationTask).where(CreationTask.id == creation_task_id).with_for_update()
    ).scalar_one_or_none()
    if not row:
        return None
    row.last_heartbeat_at = _utc_now()
    row.worker_lease_expires_at = None
    db.flush()
    return row


@contextlib.contextmanager
def background_heartbeat(creation_task_id: int | None, *, heartbeat_fn, interval_seconds: int = 30):
    """在后台线程里定时续租，防止长时间 LLM 调用被误回收。

    生成章节时，一次模型调用可能阻塞几十秒。如果只在主流程节点结束时心跳，
    recovery tick 会误以为 worker 已失联，因此这里用守护线程做兜底。
    """
    if creation_task_id is None:
        yield
        return

    stop = threading.Event()

    def _loop():
        """按固定间隔刷新心跳，直到外层 context manager 结束。"""
        while not stop.wait(timeout=interval_seconds):
            try:
                heartbeat_fn(creation_task_id)
            except Exception:
                logger.debug("background heartbeat failed for task=%s", creation_task_id, exc_info=True)

    t = threading.Thread(target=_loop, daemon=True, name=f"hb-{creation_task_id}")
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join(timeout=5)
