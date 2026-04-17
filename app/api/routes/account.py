"""Account usage and quota endpoints."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.authn import require_auth
from app.core.authz.types import Principal
from app.core.database import get_db
from app.models.creation_task import CreationTask
from app.models.novel import (
    ChapterVersion,
    Novel,
    NovelVersion,
    RewriteRequest,
    UsageLedger,
    User,
)
from app.services.quota import ensure_user_quota

router = APIRouter()


class QuotaStatusResponse(BaseModel):
    """配额状态响应体模型。"""
    plan_key: str
    max_concurrent_tasks: int
    monthly_chapter_limit: int
    monthly_token_limit: int
    used_chapters: int
    used_tokens: int
    remaining_chapters: int
    remaining_tokens: int
    month: str


class UsageLedgerItem(BaseModel):
    """用量LedgerItem。"""
    task_id: str
    source: str
    input_tokens: int
    output_tokens: int
    chapters_generated: int
    estimated_cost: float
    created_at: str


class NotificationItem(BaseModel):
    """NotificationItem。"""
    id: str
    type: str
    title: str
    message: str
    created_at: str


class HeaderStatsResponse(BaseModel):
    """HeaderStats响应体模型。"""
    works: int
    week_chapters: int
    quality_score: float
    total_words: int


def _month_range_utc(now: datetime) -> tuple[datetime, datetime]:
    """执行 month range utc 相关辅助逻辑。"""
    start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    if now.month == 12:
        end = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
    return start, end


def _normalize_quality_score(raw: float | int | None) -> float:
    """把 quality score 规范化为统一格式。"""
    if raw is None:
        return 0.0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if value <= 1.0:
        value = value * 100.0
    elif value <= 10.0:
        value = value * 10.0
    return max(0.0, min(100.0, value))


def _scoped_novel_filters(principal: Principal) -> list[object]:
    """执行 scoped novel filters 相关辅助逻辑。"""
    if principal.role == "admin":
        return []
    return [Novel.user_id == principal.user_uuid]


@router.get("/quota", response_model=QuotaStatusResponse)
def get_quota(
    principal: Principal = Depends(require_auth()),
    db: Session = Depends(get_db),
):
    """返回配额。"""
    user = db.execute(select(User).where(User.uuid == principal.user_uuid)).scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")
    quota = ensure_user_quota(db, user)
    now = datetime.now(timezone.utc)
    start, end = _month_range_utc(now)
    used_chapters = (
        db.execute(
            select(func.coalesce(func.sum(UsageLedger.chapters_generated), 0))
            .where(UsageLedger.user_id == user.id, UsageLedger.created_at >= start, UsageLedger.created_at < end)
        ).scalar_one()
        or 0
    )
    used_tokens = (
        db.execute(
            select(func.coalesce(func.sum(UsageLedger.input_tokens + UsageLedger.output_tokens), 0))
            .where(UsageLedger.user_id == user.id, UsageLedger.created_at >= start, UsageLedger.created_at < end)
        ).scalar_one()
        or 0
    )
    db.commit()
    return QuotaStatusResponse(
        plan_key=str(quota.plan_key or "free"),
        max_concurrent_tasks=int(quota.max_concurrent_tasks or 1),
        monthly_chapter_limit=int(quota.monthly_chapter_limit or 0),
        monthly_token_limit=int(quota.monthly_token_limit or 0),
        used_chapters=int(used_chapters),
        used_tokens=int(used_tokens),
        remaining_chapters=max(0, int(quota.monthly_chapter_limit or 0) - int(used_chapters)),
        remaining_tokens=max(0, int(quota.monthly_token_limit or 0) - int(used_tokens)),
        month=f"{now.year:04d}-{now.month:02d}",
    )


@router.get("/ledger", response_model=list[UsageLedgerItem])
def list_ledger(
    limit: int = 50,
    principal: Principal = Depends(require_auth()),
    db: Session = Depends(get_db),
):
    """列出ledger。"""
    user = db.execute(select(User).where(User.uuid == principal.user_uuid)).scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")
    rows = (
        db.execute(select(UsageLedger).where(UsageLedger.user_id == user.id).order_by(UsageLedger.created_at.desc()).limit(max(1, min(200, limit))))
        .scalars()
        .all()
    )
    return [
        UsageLedgerItem(
            task_id=r.task_id,
            source=r.source,
            input_tokens=int(r.input_tokens or 0),
            output_tokens=int(r.output_tokens or 0),
            chapters_generated=int(r.chapters_generated or 0),
            estimated_cost=float(r.estimated_cost or 0.0),
            created_at=(r.created_at.isoformat() if r.created_at else ""),
        )
        for r in rows
    ]


@router.get("/notifications", response_model=list[NotificationItem])
def list_notifications(
    limit: int = 30,
    principal: Principal = Depends(require_auth()),
    db: Session = Depends(get_db),
):
    """列出notifications。"""
    user = db.execute(select(User).where(User.uuid == principal.user_uuid)).scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")
    novels = db.execute(select(Novel.id, Novel.title).where(Novel.user_id == user.uuid)).all()
    novel_ids = [int(x[0]) for x in novels]
    novel_title_map = {int(x[0]): str(x[1] or f"novel-{x[0]}") for x in novels}
    if not novel_ids:
        return []
    tasks = (
        db.execute(
            select(CreationTask)
            .where(
                CreationTask.user_uuid == user.uuid,
                CreationTask.task_type == "generation",
                CreationTask.status.in_(["completed", "failed", "cancelled"]),
            )
            .order_by(CreationTask.updated_at.desc())
            .limit(max(1, min(limit, 100)))
        )
        .scalars()
        .all()
    )
    rewrites = (
        db.execute(
            select(RewriteRequest)
            .where(RewriteRequest.novel_id.in_(novel_ids), RewriteRequest.status.in_(["completed", "failed", "cancelled"]))
            .order_by(RewriteRequest.updated_at.desc())
            .limit(max(1, min(limit, 100)))
        )
        .scalars()
        .all()
    )
    out: list[NotificationItem] = []
    for t in tasks:
        title = novel_title_map.get(int(t.resource_id), f"novel-{t.resource_id}")
        status = str(t.status or "unknown")
        out.append(
            NotificationItem(
                id=f"gen-{t.id}",
                type="generation",
                title=f"《{title}》生成{status}",
                message=str(t.message or (t.error_detail or ""))[:200],
                created_at=(t.updated_at.isoformat() if t.updated_at else ""),
            )
        )
    for r in rewrites:
        title = novel_title_map.get(int(r.novel_id), f"novel-{r.novel_id}")
        status = str(r.status or "unknown")
        out.append(
            NotificationItem(
                id=f"rw-{r.id}",
                type="rewrite",
                title=f"《{title}》重写{status}",
                message=str(r.message or (r.error or ""))[:200],
                created_at=(r.updated_at.isoformat() if r.updated_at else ""),
            )
        )
    out.sort(key=lambda x: x.created_at, reverse=True)
    return out[: max(1, min(limit, 100))]


@router.get("/header-stats", response_model=HeaderStatsResponse)
def get_header_stats(
    principal: Principal = Depends(require_auth()),
    db: Session = Depends(get_db),
):
    """返回headerstats。"""
    scope_filters = _scoped_novel_filters(principal)
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    works = (
        db.execute(select(func.count(Novel.id)).where(*scope_filters)).scalar_one()
        or 0
    )

    week_chapters = (
        db.execute(
            select(func.count(ChapterVersion.id))
            .join(NovelVersion, NovelVersion.id == ChapterVersion.novel_version_id)
            .join(Novel, Novel.id == NovelVersion.novel_id)
            .where(
                NovelVersion.is_default == 1,
                ChapterVersion.status == "completed",
                ChapterVersion.updated_at >= week_ago,
                *scope_filters,
            )
        ).scalar_one()
        or 0
    )
    total_words = (
        db.execute(
            select(func.coalesce(func.sum(func.length(func.coalesce(ChapterVersion.content, ""))), 0))
            .join(NovelVersion, NovelVersion.id == ChapterVersion.novel_version_id)
            .join(Novel, Novel.id == NovelVersion.novel_id)
            .where(
                NovelVersion.is_default == 1,
                ChapterVersion.status == "completed",
                *scope_filters,
            )
        ).scalar_one()
        or 0
    )
    quality_rows = db.execute(
        select(ChapterVersion.language_quality_score)
        .join(NovelVersion, NovelVersion.id == ChapterVersion.novel_version_id)
        .join(Novel, Novel.id == NovelVersion.novel_id)
        .where(
            NovelVersion.is_default == 1,
            ChapterVersion.status == "completed",
            ChapterVersion.language_quality_score.isnot(None),
            *scope_filters,
        )
    ).all()

    normalized_scores = [_normalize_quality_score(row[0]) for row in quality_rows]
    quality_score = round(
        sum(normalized_scores) / len(normalized_scores), 1
    ) if normalized_scores else 0.0

    return HeaderStatsResponse(
        works=int(works),
        week_chapters=int(week_chapters),
        quality_score=float(quality_score),
        total_words=int(total_words),
    )
