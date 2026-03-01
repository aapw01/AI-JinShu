"""Worker lease and heartbeat helpers for in-flight creation tasks."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.creation_task import CreationTask


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def acquire_or_refresh_lease(db: Session, *, creation_task_id: int, ttl_seconds: int) -> CreationTask | None:
    row = db.execute(select(CreationTask).where(CreationTask.id == creation_task_id)).scalar_one_or_none()
    if not row:
        return None
    now = _utc_now()
    ttl = max(5, int(ttl_seconds))
    row.last_heartbeat_at = now
    row.worker_lease_expires_at = now + timedelta(seconds=ttl)
    db.flush()
    return row


def release_lease(db: Session, *, creation_task_id: int) -> CreationTask | None:
    row = db.execute(select(CreationTask).where(CreationTask.id == creation_task_id)).scalar_one_or_none()
    if not row:
        return None
    row.last_heartbeat_at = _utc_now()
    row.worker_lease_expires_at = None
    db.flush()
    return row

