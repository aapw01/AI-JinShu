"""Lock helpers for scheduler critical sections."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.services.scheduler.concurrency_service import lock_user_quota_row


def acquire_user_dispatch_lock(db: Session, *, user_uuid: str) -> None:
    """Acquire user-level dispatch lock (DB row lock on user quota)."""
    lock_user_quota_row(db, user_uuid=user_uuid)

