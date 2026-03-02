"""Admin user management routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from collections import defaultdict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authz.deps import require_permission
from app.core.authz.types import Permission, Principal
from app.core.database import get_db
from app.core.time_utils import to_utc_iso_z
from app.models.novel import AdminAuditLog, User, GenerationTask, GenerationCheckpoint

router = APIRouter()


class UserAdminItem(BaseModel):
    uuid: str
    email: str
    role: str
    status: str
    created_at: str
    last_login_at: str | None = None


def _percentile(samples: list[float], p: float) -> float:
    if not samples:
        return 0.0
    arr = sorted(float(x) for x in samples)
    idx = int(round((len(arr) - 1) * p))
    idx = max(0, min(idx, len(arr) - 1))
    return arr[idx]


@router.get("/users", response_model=list[UserAdminItem])
def list_users(
    query: str | None = None,
    status: str | None = None,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(Permission.USER_READ)),
):
    stmt = select(User).order_by(User.created_at.desc())
    if query:
        q = f"%{query.lower()}%"
        stmt = stmt.where(User.email.ilike(q))
    if status:
        stmt = stmt.where(User.status == status)
    stmt = stmt.offset(skip).limit(limit)
    rows = db.execute(stmt).scalars().all()
    return [
        UserAdminItem(
            uuid=u.uuid,
            email=u.email,
            role=u.role,
            status=u.status,
            created_at=to_utc_iso_z(u.created_at),
            last_login_at=to_utc_iso_z(u.last_login_at),
        )
        for u in rows
    ]


@router.post("/users/{user_uuid}/disable")
def disable_user(
    user_uuid: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(Permission.USER_DISABLE)),
):
    user = db.execute(select(User).where(User.uuid == user_uuid)).scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")
    if user.role == "admin":
        raise HTTPException(400, "Cannot disable admin")
    user.status = "disabled"
    actor = db.execute(select(User).where(User.uuid == principal.user_uuid)).scalar_one_or_none()
    db.add(
        AdminAuditLog(
            actor_user_id=actor.id if actor else None,
            target_user_id=user.id,
            action="disable_user",
            metadata_={"target_uuid": user.uuid},
        )
    )
    db.commit()
    return {"ok": True}


@router.post("/users/{user_uuid}/enable")
def enable_user(
    user_uuid: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(Permission.USER_DISABLE)),
):
    user = db.execute(select(User).where(User.uuid == user_uuid)).scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")
    user.status = "active"
    actor = db.execute(select(User).where(User.uuid == principal.user_uuid)).scalar_one_or_none()
    db.add(
        AdminAuditLog(
            actor_user_id=actor.id if actor else None,
            target_user_id=user.id,
            action="enable_user",
            metadata_={"target_uuid": user.uuid},
        )
    )
    db.commit()
    return {"ok": True}


@router.get("/observability/summary")
def observability_summary(
    limit_tasks: int = 200,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.USER_READ)),
):
    tasks = (
        db.execute(select(GenerationTask).order_by(GenerationTask.updated_at.desc()).limit(max(1, min(limit_tasks, 1000))))
        .scalars()
        .all()
    )
    if not tasks:
        return {"summary": {"tasks": 0}}

    task_ids = [t.task_id for t in tasks if t.task_id]
    checkpoints = (
        db.execute(
            select(GenerationCheckpoint)
            .where(GenerationCheckpoint.task_id.in_(task_ids))
            .order_by(GenerationCheckpoint.task_id.asc(), GenerationCheckpoint.created_at.asc(), GenerationCheckpoint.id.asc())
        )
        .scalars()
        .all()
        if task_ids
        else []
    )
    by_task: dict[str, list[GenerationCheckpoint]] = defaultdict(list)
    for cp in checkpoints:
        by_task[cp.task_id].append(cp)

    node_durations: dict[str, list[float]] = defaultdict(list)
    for tid, rows in by_task.items():
        _ = tid
        for i in range(1, len(rows)):
            prev = rows[i - 1]
            cur = rows[i]
            dt = (cur.created_at - prev.created_at).total_seconds()
            if dt > 0:
                node_durations[str(cur.node)].append(float(dt))

    node_stats = {
        node: {
            "count": len(samples),
            "p50": round(_percentile(samples, 0.5), 3),
            "p95": round(_percentile(samples, 0.95), 3),
            "max": round(max(samples), 3),
        }
        for node, samples in node_durations.items()
        if samples
    }
    failures = [t for t in tasks if (t.status or "") == "failed"]
    retries = [t for t in tasks if (t.status or "") == "retrying"]
    model_error_count = sum(1 for t in failures if (t.error_code or "").startswith("MODEL_"))
    review_overfix_risk = sum(1 for t in tasks if "过度纠错" in str(t.message or ""))
    return {
        "summary": {
            "tasks": len(tasks),
            "failed": len(failures),
            "retrying": len(retries),
            "model_error_rate": round(model_error_count / max(1, len(tasks)), 4),
            "retry_hit_rate": round(len(retries) / max(1, len(tasks)), 4),
            "review_overfix_risk_rate": round(review_overfix_risk / max(1, len(tasks)), 4),
            "node_latency_seconds": node_stats,
        }
    }
