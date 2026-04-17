"""Quota enforcement and usage ledger helpers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.constants import (
    QUOTA_ADMIN_MONTHLY_CHAPTER_LIMIT,
    QUOTA_ADMIN_MONTHLY_TOKEN_LIMIT,
    QUOTA_ENFORCE_CONCURRENCY_LIMIT,
    QUOTA_FREE_MONTHLY_CHAPTER_LIMIT,
    QUOTA_FREE_MONTHLY_TOKEN_LIMIT,
)
from app.models.creation_task import CreationTask
from app.models.novel import Novel, UsageLedger, User, UserQuota
from app.services.system_settings.runtime import get_effective_runtime_setting


class QuotaReason(str, Enum):
    """枚举配额校验失败时可返回给调用方的原因。"""
    QUOTA_SUSPENDED = "quota_suspended"
    CONCURRENCY_LIMIT_EXCEEDED = "concurrency_limit_exceeded"
    MONTHLY_CHAPTER_LIMIT_EXCEEDED = "monthly_chapter_limit_exceeded"
    MONTHLY_TOKEN_LIMIT_EXCEEDED = "monthly_token_limit_exceeded"


@dataclass
class QuotaCheckResult:
    """封装配额检查的结果、原因和用户提示。"""
    ok: bool
    reason: QuotaReason | None = None
    user_message: str | None = None


def _quota_user_message(reason: QuotaReason) -> str:
    """执行 quota user message 相关辅助逻辑。"""
    mapping = {
        QuotaReason.MONTHLY_TOKEN_LIMIT_EXCEEDED: "本月可用 token 已用尽，请下月再试或联系管理员调整额度",
        QuotaReason.MONTHLY_CHAPTER_LIMIT_EXCEEDED: "本月可生成章节额度已用尽，请下月再试或联系管理员调整额度",
        QuotaReason.QUOTA_SUSPENDED: "当前账号配额状态不可用，请联系管理员",
        QuotaReason.CONCURRENCY_LIMIT_EXCEEDED: "当前进行中的任务数已达上限，请稍后再试",
    }
    return mapping.get(reason, "当前请求超出配额限制，请稍后再试")


def _month_range_utc(now: datetime) -> tuple[datetime, datetime]:
    """执行 month range utc 相关辅助逻辑。"""
    start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    if now.month == 12:
        end = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
    return start, end


def ensure_user_quota(db: Session, user: User) -> UserQuota:
    """确保用户配额存在并可用。"""
    quota = db.execute(select(UserQuota).where(UserQuota.user_id == user.id)).scalar_one_or_none()
    if quota:
        return quota
    is_admin = user.role == "admin"
    default_concurrency = max(1, int(get_effective_runtime_setting("creation_default_max_concurrent_tasks", int, 1) or 1))
    quota = UserQuota(
        user_id=user.id,
        plan_key="team" if is_admin else "free",
        max_concurrent_tasks=default_concurrency,
        monthly_chapter_limit=(
            QUOTA_ADMIN_MONTHLY_CHAPTER_LIMIT if is_admin else QUOTA_FREE_MONTHLY_CHAPTER_LIMIT
        ),
        monthly_token_limit=(
            QUOTA_ADMIN_MONTHLY_TOKEN_LIMIT if is_admin else QUOTA_FREE_MONTHLY_TOKEN_LIMIT
        ),
    )
    if user.role == "admin":
        quota.max_concurrent_tasks = max(3, default_concurrency)
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
    """检查生成配额。"""
    quota = ensure_user_quota(db, user)
    if quota.status != "active":
        return QuotaCheckResult(False, QuotaReason.QUOTA_SUSPENDED, _quota_user_message(QuotaReason.QUOTA_SUSPENDED))

    if QUOTA_ENFORCE_CONCURRENCY_LIMIT:
        active_count = (
            db.execute(
                select(func.count())
                .select_from(CreationTask)
                .where(
                    CreationTask.user_uuid == user.uuid,
                    CreationTask.status.in_(["queued", "dispatching", "running", "paused"]),
                )
            ).scalar_one()
            or 0
        )
        if int(active_count) >= int(quota.max_concurrent_tasks or 1):
            return QuotaCheckResult(
                False,
                QuotaReason.CONCURRENCY_LIMIT_EXCEEDED,
                _quota_user_message(QuotaReason.CONCURRENCY_LIMIT_EXCEEDED),
            )

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
        return QuotaCheckResult(
            False,
            QuotaReason.MONTHLY_CHAPTER_LIMIT_EXCEEDED,
            _quota_user_message(QuotaReason.MONTHLY_CHAPTER_LIMIT_EXCEEDED),
        )

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
        return QuotaCheckResult(
            False,
            QuotaReason.MONTHLY_TOKEN_LIMIT_EXCEEDED,
            _quota_user_message(QuotaReason.MONTHLY_TOKEN_LIMIT_EXCEEDED),
        )
    return QuotaCheckResult(True)


def record_generation_usage(
    db: Session,
    *,
    task_id: str,
    novel_id: int,
    source: str = "generation",
) -> None:
    """记录生成用量。"""
    task = db.execute(
        select(CreationTask)
        .where(
            CreationTask.task_type == "generation",
            CreationTask.resource_type == "novel",
            CreationTask.resource_id == novel_id,
            or_(CreationTask.worker_task_id == task_id, CreationTask.public_id == task_id),
        )
        .order_by(CreationTask.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    novel = db.execute(select(Novel).where(Novel.id == novel_id)).scalar_one_or_none()
    if not task or not novel or not novel.user_id:
        return
    user = db.execute(select(User).where(User.uuid == novel.user_id)).scalar_one_or_none()
    if not user:
        return
    result = task.result_json if isinstance(task.result_json, dict) else {}
    payload = task.payload_json if isinstance(task.payload_json, dict) else {}
    start_chapter = int(result.get("start_chapter") or payload.get("start_chapter") or 1)
    completed_chapters = int(result.get("completed_chapters") or 0)
    if completed_chapters <= 0 and task.status == "completed":
        current_chapter = int(result.get("current_chapter") or 0)
        if current_chapter >= start_chapter:
            completed_chapters = current_chapter - start_chapter + 1
        else:
            completed_chapters = max(0, int(payload.get("num_chapters") or 0))

    input_tokens = int(result.get("token_usage_input") or 0)
    output_tokens = int(result.get("token_usage_output") or 0)
    estimated_cost = float(result.get("estimated_cost") or 0.0)
    existed = db.execute(select(UsageLedger).where(UsageLedger.task_id == task_id)).scalar_one_or_none()
    if existed:
        existed.input_tokens = input_tokens
        existed.output_tokens = output_tokens
        existed.chapters_generated = completed_chapters
        existed.estimated_cost = estimated_cost
        return
    db.add(
        UsageLedger(
            user_id=user.id,
            novel_id=novel_id,
            task_id=task_id,
            source=source,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            chapters_generated=completed_chapters,
            estimated_cost=estimated_cost,
        )
    )
