"""Per-user concurrency controls for unified creation tasks."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.creation_task import CreationTask
from app.models.novel import User, UserQuota
from app.services.quota import ensure_user_quota
from app.services.system_settings.runtime import get_effective_runtime_setting


RUNNING_SLOT_STATUSES = {"dispatching", "running"}


def get_user_concurrency_limit(db: Session, *, user_uuid: str) -> int:
    """返回用户并发limit。"""
    user = db.execute(select(User).where(User.uuid == user_uuid)).scalar_one_or_none()
    default_limit = max(1, int(get_effective_runtime_setting("creation_default_max_concurrent_tasks", int, 1) or 1))
    if not user:
        return default_limit
    quota = ensure_user_quota(db, user)
    configured = int(quota.max_concurrent_tasks or 0)
    return max(1, configured) if configured > 0 else default_limit


def count_user_running_slots(db: Session, *, user_uuid: str) -> int:
    """统计用户runningslots。"""
    count = (
        db.execute(
            select(func.count())
            .select_from(CreationTask)
            .where(
                CreationTask.user_uuid == user_uuid,
                CreationTask.status.in_(tuple(RUNNING_SLOT_STATUSES)),
            )
        ).scalar_one()
        or 0
    )
    return int(count)


def lock_user_quota_row(db: Session, *, user_uuid: str) -> UserQuota | None:
    """Acquire row-level lock on user quota (best effort on sqlite)."""
    user = db.execute(select(User).where(User.uuid == user_uuid)).scalar_one_or_none()
    if not user:
        return None
    ensure_user_quota(db, user)
    return db.execute(
        select(UserQuota)
        .where(UserQuota.user_id == user.id)
        .with_for_update()
    ).scalar_one_or_none()
