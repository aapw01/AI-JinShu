"""Quota enforcement and usage ledger helpers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.novel import GenerationTask, Novel, UsageLedger, User, UserQuota


@dataclass
class QuotaCheckResult:
    ok: bool
    reason: str | None = None


def _month_range_utc(now: datetime) -> tuple[datetime, datetime]:
    start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    if now.month == 12:
        end = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
    return start, end


def ensure_user_quota(db: Session, user: User) -> UserQuota:
    settings = get_settings()
    quota = db.execute(select(UserQuota).where(UserQuota.user_id == user.id)).scalar_one_or_none()
    if quota:
        return quota
    is_admin = user.role == "admin"
    quota = UserQuota(
        user_id=user.id,
        plan_key="team" if is_admin else "free",
        max_concurrent_tasks=max(1, int(settings.creation_default_max_concurrent_tasks or 1)),
        monthly_chapter_limit=(
            int(settings.quota_admin_monthly_chapter_limit)
            if is_admin
            else int(settings.quota_free_monthly_chapter_limit)
        ),
        monthly_token_limit=(
            int(settings.quota_admin_monthly_token_limit)
            if is_admin
            else int(settings.quota_free_monthly_token_limit)
        ),
    )
    if user.role == "admin":
        quota.max_concurrent_tasks = max(3, int(settings.creation_default_max_concurrent_tasks or 1))
    try:
        with db.begin_nested():
            db.add(quota)
            db.flush()
        return quota
    except IntegrityError:
        # Concurrent request may have created the row first.
        existing = db.execute(select(UserQuota).where(UserQuota.user_id == user.id)).scalar_one_or_none()
        if existing:
            return existing
        raise


def check_generation_quota(
    db: Session,
    *,
    user: User,
    requested_chapters: int,
) -> QuotaCheckResult:
    quota = ensure_user_quota(db, user)
    if quota.status != "active":
        return QuotaCheckResult(False, "quota_suspended")

    if get_settings().quota_enforce_concurrency_limit:
        active_count = (
            db.execute(
                select(func.count())
                .select_from(GenerationTask)
                .join(Novel, Novel.id == GenerationTask.novel_id)
                .where(
                    Novel.user_id == user.uuid,
                    GenerationTask.status.in_(["submitted", "running", "retrying", "paused", "awaiting_outline_confirmation"]),
                )
            ).scalar_one()
            or 0
        )
        if int(active_count) >= int(quota.max_concurrent_tasks or 1):
            return QuotaCheckResult(False, "concurrency_limit_exceeded")

    now = datetime.now(timezone.utc)
    month_start, month_end = _month_range_utc(now)
    used_chapters = (
        db.execute(
            select(func.coalesce(func.sum(UsageLedger.chapters_generated), 0))
            .where(
                UsageLedger.user_id == user.id,
                UsageLedger.created_at >= month_start,
                UsageLedger.created_at < month_end,
            )
        ).scalar_one()
        or 0
    )
    if int(used_chapters) + max(1, int(requested_chapters)) > int(quota.monthly_chapter_limit or 0):
        return QuotaCheckResult(False, "monthly_chapter_limit_exceeded")

    used_tokens = (
        db.execute(
            select(func.coalesce(func.sum(UsageLedger.input_tokens + UsageLedger.output_tokens), 0))
            .where(
                UsageLedger.user_id == user.id,
                UsageLedger.created_at >= month_start,
                UsageLedger.created_at < month_end,
            )
        ).scalar_one()
        or 0
    )
    if int(used_tokens) >= int(quota.monthly_token_limit or 0):
        return QuotaCheckResult(False, "monthly_token_limit_exceeded")
    return QuotaCheckResult(True)


def record_generation_usage(
    db: Session,
    *,
    task_id: str,
    novel_id: int,
    source: str = "generation",
) -> None:
    task = db.execute(select(GenerationTask).where(GenerationTask.task_id == task_id)).scalar_one_or_none()
    novel = db.execute(select(Novel).where(Novel.id == novel_id)).scalar_one_or_none()
    if not task or not novel or not novel.user_id:
        return
    user = db.execute(select(User).where(User.uuid == novel.user_id)).scalar_one_or_none()
    if not user:
        return
    existed = db.execute(select(UsageLedger).where(UsageLedger.task_id == task_id)).scalar_one_or_none()
    if existed:
        existed.input_tokens = int(task.token_usage_input or 0)
        existed.output_tokens = int(task.token_usage_output or 0)
        existed.chapters_generated = max(0, int(task.current_chapter or 0) - int(task.start_chapter or 1) + 1)
        existed.estimated_cost = float(task.estimated_cost or 0.0)
        return
    db.add(
        UsageLedger(
            user_id=user.id,
            novel_id=novel_id,
            task_id=task_id,
            source=source,
            input_tokens=int(task.token_usage_input or 0),
            output_tokens=int(task.token_usage_output or 0),
            chapters_generated=max(0, int(task.current_chapter or 0) - int(task.start_chapter or 1) + 1),
            estimated_cost=float(task.estimated_cost or 0.0),
        )
    )
