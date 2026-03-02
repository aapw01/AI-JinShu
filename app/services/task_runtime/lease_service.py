"""Worker lease and heartbeat helpers for in-flight creation tasks."""
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
    return datetime.now(timezone.utc)


def acquire_or_refresh_lease(db: Session, *, creation_task_id: int, ttl_seconds: int) -> CreationTask | None:
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
    """Send periodic lease heartbeats in a daemon thread so long-running LLM
    calls don't cause the recovery tick to reclaim the task.

    ``heartbeat_fn`` should accept a single ``creation_task_id`` int and perform
    the DB heartbeat (open+close its own session).
    """
    if creation_task_id is None:
        yield
        return

    stop = threading.Event()

    def _loop():
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

