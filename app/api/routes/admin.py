"""Admin user management routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from collections import defaultdict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authz.deps import require_permission
from app.core.authz.engine import authorize
from app.core.authz.errors import forbidden
from app.core.authz.types import Permission, Principal
from app.core.database import get_db
from app.core.time_utils import to_utc_iso_z
from app.models.novel import (
    AdminAuditLog,
    User,
    UserQuota,
    GenerationTask,
    GenerationCheckpoint,
)
from app.services.quota import ensure_user_quota
from app.schemas.system_settings import (
    AdminModelSettingsResponse,
    AdminModelSettingsUpdateRequest,
    AdminRuntimeSettingsResponse,
    AdminRuntimeSettingsUpdateRequest,
)
from app.services.system_settings.repository import (
    RUNTIME_SETTING_KEYS,
    SettingsValidationError,
    replace_model_settings,
    set_runtime_overrides,
)
from app.services.system_settings.runtime import (
    get_effective_model_config,
    get_embedding_runtime,
    get_model_settings_for_admin,
    get_primary_chat_runtime,
    get_runtime_overrides,
    get_runtime_settings_with_sources,
    invalidate_caches,
)

router = APIRouter()


class UserAdminItem(BaseModel):
    """用户AdminItem。"""
    uuid: str
    email: str
    role: str
    status: str
    email_verified: bool
    created_at: str
    last_login_at: str | None = None
    plan_key: str | None = None
    max_concurrent_tasks: int | None = None
    monthly_chapter_limit: int | None = None
    monthly_token_limit: int | None = None


class UpdateQuotaRequest(BaseModel):
    """Update配额请求体模型。"""
    plan_key: str | None = None
    max_concurrent_tasks: int | None = None
    monthly_chapter_limit: int | None = None
    monthly_token_limit: int | None = None


def _percentile(samples: list[float], p: float) -> float:
    """执行 percentile 相关辅助逻辑。"""
    if not samples:
        return 0.0
    arr = sorted(float(x) for x in samples)
    idx = int(round((len(arr) - 1) * p))
    idx = max(0, min(idx, len(arr) - 1))
    return arr[idx]


def _log_settings_audit(
    *,
    db: Session,
    principal: Principal,
    action: str,
    metadata: dict[str, object] | None = None,
) -> None:
    """执行 log settings audit 相关辅助逻辑。"""
    actor = db.execute(select(User).where(User.uuid == principal.user_uuid)).scalar_one_or_none()
    db.add(
        AdminAuditLog(
            actor_user_id=actor.id if actor else None,
            target_user_id=None,
            action=action,
            metadata_=metadata or {},
        )
    )


@router.get("/users", response_model=list[UserAdminItem])
def list_users(
    query: str | None = None,
    status: str | None = None,
    email_verified: str | None = None,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(Permission.USER_READ)),
):
    """列出用户。"""
    stmt = select(User).order_by(User.created_at.desc())
    if query:
        q = f"%{query.lower()}%"
        stmt = stmt.where(User.email.ilike(q))
    if status:
        stmt = stmt.where(User.status == status)
    if email_verified is not None:
        if email_verified.lower() in ("true", "1", "yes"):
            stmt = stmt.where(User.email_verified_at.isnot(None))
        elif email_verified.lower() in ("false", "0", "no"):
            stmt = stmt.where(User.email_verified_at.is_(None))
    stmt = stmt.offset(skip).limit(limit)
    rows = db.execute(stmt).scalars().all()

    # Batch-load quotas for all returned users
    user_ids = [u.id for u in rows]
    quotas: dict[int, UserQuota] = {}
    if user_ids:
        quota_rows = (
            db.execute(select(UserQuota).where(UserQuota.user_id.in_(user_ids)))
            .scalars()
            .all()
        )
        quotas = {q.user_id: q for q in quota_rows}

    return [
        UserAdminItem(
            uuid=u.uuid,
            email=u.email,
            role=u.role,
            status=u.status,
            email_verified=u.email_verified_at is not None,
            created_at=to_utc_iso_z(u.created_at),
            last_login_at=to_utc_iso_z(u.last_login_at),
            plan_key=quotas[u.id].plan_key if u.id in quotas else None,
            max_concurrent_tasks=(
                quotas[u.id].max_concurrent_tasks if u.id in quotas else None
            ),
            monthly_chapter_limit=(
                quotas[u.id].monthly_chapter_limit if u.id in quotas else None
            ),
            monthly_token_limit=(
                quotas[u.id].monthly_token_limit if u.id in quotas else None
            ),
        )
        for u in rows
    ]


@router.post("/users/{user_uuid}/disable")
def disable_user(
    user_uuid: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(Permission.USER_DISABLE)),
):
    """执行 disable user 相关辅助逻辑。"""
    user = db.execute(select(User).where(User.uuid == user_uuid)).scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")
    if user.role == "admin":
        raise HTTPException(400, "Cannot disable admin")
    user.status = "disabled"
    actor = db.execute(
        select(User).where(User.uuid == principal.user_uuid)
    ).scalar_one_or_none()
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
    """执行 enable user 相关辅助逻辑。"""
    user = db.execute(select(User).where(User.uuid == user_uuid)).scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")
    user.status = "active"
    actor = db.execute(
        select(User).where(User.uuid == principal.user_uuid)
    ).scalar_one_or_none()
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


@router.put("/users/{user_uuid}/quota")
def update_user_quota(
    user_uuid: str,
    data: UpdateQuotaRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(Permission.USER_QUOTA_UPDATE)),
):
    """更新用户配额。"""
    user = db.execute(select(User).where(User.uuid == user_uuid)).scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")
    quota = ensure_user_quota(db, user)
    changes: dict[str, dict[str, object]] = {}
    if data.plan_key is not None:
        changes["plan_key"] = {"old": quota.plan_key, "new": data.plan_key}
        quota.plan_key = data.plan_key
    if data.max_concurrent_tasks is not None:
        changes["max_concurrent_tasks"] = {
            "old": quota.max_concurrent_tasks,
            "new": data.max_concurrent_tasks,
        }
        quota.max_concurrent_tasks = max(1, data.max_concurrent_tasks)
    if data.monthly_chapter_limit is not None:
        changes["monthly_chapter_limit"] = {
            "old": quota.monthly_chapter_limit,
            "new": data.monthly_chapter_limit,
        }
        quota.monthly_chapter_limit = max(0, data.monthly_chapter_limit)
    if data.monthly_token_limit is not None:
        changes["monthly_token_limit"] = {
            "old": quota.monthly_token_limit,
            "new": data.monthly_token_limit,
        }
        quota.monthly_token_limit = max(0, data.monthly_token_limit)
    actor = db.execute(
        select(User).where(User.uuid == principal.user_uuid)
    ).scalar_one_or_none()
    db.add(
        AdminAuditLog(
            actor_user_id=actor.id if actor else None,
            target_user_id=user.id,
            action="update_quota",
            metadata_={"target_uuid": user.uuid, "changes": changes},
        )
    )
    db.commit()
    return {
        "ok": True,
        "quota": {
            "plan_key": quota.plan_key,
            "max_concurrent_tasks": quota.max_concurrent_tasks,
            "monthly_chapter_limit": quota.monthly_chapter_limit,
            "monthly_token_limit": quota.monthly_token_limit,
        },
    }


@router.get("/observability/summary")
def observability_summary(
    limit_tasks: int = 200,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.USER_READ)),
):
    """执行 observability summary 相关辅助逻辑。"""
    tasks = (
        db.execute(
            select(GenerationTask)
            .order_by(GenerationTask.updated_at.desc())
            .limit(max(1, min(limit_tasks, 1000)))
        )
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
            .order_by(
                GenerationCheckpoint.task_id.asc(),
                GenerationCheckpoint.created_at.asc(),
                GenerationCheckpoint.id.asc(),
            )
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
    model_error_count = sum(
        1 for t in failures if (t.error_code or "").startswith("MODEL_")
    )
    review_overfix_risk = sum(1 for t in tasks if "过度纠错" in str(t.message or ""))
    return {
        "summary": {
            "tasks": len(tasks),
            "failed": len(failures),
            "retrying": len(retries),
            "model_error_rate": round(model_error_count / max(1, len(tasks)), 4),
            "retry_hit_rate": round(len(retries) / max(1, len(tasks)), 4),
            "review_overfix_risk_rate": round(
                review_overfix_risk / max(1, len(tasks)), 4
            ),
            "node_latency_seconds": node_stats,
        }
    }


_RUNTIME_KEYS_IN_ORDER = [
    "creation_scheduler_enabled",
    "creation_default_max_concurrent_tasks",
]


@router.get("/settings/models", response_model=AdminModelSettingsResponse)
def get_system_model_settings(
    include_secrets: bool = Query(default=False),
    principal: Principal = Depends(require_permission(Permission.SYSTEM_SETTINGS_READ)),
):
    """返回系统模型设置。"""
    if include_secrets:
        allowed = authorize(principal, Permission.SYSTEM_SETTINGS_WRITE, None)
        if not allowed.allowed:
            raise forbidden("Permission denied")
    return AdminModelSettingsResponse.model_validate(
        get_model_settings_for_admin(include_secrets=include_secrets)
    )


@router.put("/settings/models", response_model=AdminModelSettingsResponse)
def put_system_model_settings(
    req: AdminModelSettingsUpdateRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(Permission.SYSTEM_SETTINGS_WRITE)),
):
    """执行 put system model settings 相关辅助逻辑。"""
    try:
        replace_model_settings(db, primary_chat=req.primary_chat.model_dump(), embedding=req.embedding.model_dump())
        _log_settings_audit(
            db=db,
            principal=principal,
            action="update_system_model_settings",
            metadata={
                "primary_chat_provider": req.primary_chat.provider,
                "primary_chat_model": req.primary_chat.model,
                "embedding_enabled": req.embedding.enabled,
                "embedding_model": req.embedding.model,
            },
        )
        db.commit()
    except SettingsValidationError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    invalidate_caches()
    return AdminModelSettingsResponse.model_validate(get_model_settings_for_admin())


@router.get("/settings/runtime", response_model=AdminRuntimeSettingsResponse)
def get_system_runtime_settings(
    _: Principal = Depends(require_permission(Permission.SYSTEM_SETTINGS_READ)),
):
    """返回系统运行时设置。"""
    payload = get_runtime_settings_with_sources(_RUNTIME_KEYS_IN_ORDER)
    items = [
        {"key": key, "value": payload.get(key, {}).get("value"), "source": payload.get(key, {}).get("source", "env")}
        for key in _RUNTIME_KEYS_IN_ORDER
    ]
    return AdminRuntimeSettingsResponse.model_validate({"items": items})


@router.put("/settings/runtime", response_model=AdminRuntimeSettingsResponse)
def put_system_runtime_settings(
    req: AdminRuntimeSettingsUpdateRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(Permission.SYSTEM_SETTINGS_WRITE)),
):
    """执行 put system runtime settings 相关辅助逻辑。"""
    bad_keys = [k for k in req.updates.keys() if k not in RUNTIME_SETTING_KEYS]
    if bad_keys:
        raise HTTPException(status_code=400, detail=f"Unsupported setting key(s): {', '.join(sorted(bad_keys))}")
    try:
        set_runtime_overrides(db, req.updates)
        _log_settings_audit(
            db=db,
            principal=principal,
            action="update_system_runtime_settings",
            metadata={"updated_keys": sorted(list(req.updates.keys()))},
        )
        db.commit()
    except SettingsValidationError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    invalidate_caches()
    payload = get_runtime_settings_with_sources(_RUNTIME_KEYS_IN_ORDER)
    items = [
        {"key": key, "value": payload.get(key, {}).get("value"), "source": payload.get(key, {}).get("source", "env")}
        for key in _RUNTIME_KEYS_IN_ORDER
    ]
    return AdminRuntimeSettingsResponse.model_validate({"items": items})


@router.get("/settings/effective")
def get_effective_system_settings(
    _: Principal = Depends(require_permission(Permission.SYSTEM_SETTINGS_READ)),
):
    """返回生效值系统设置。"""
    return {
        "models": get_model_settings_for_admin(),
        "runtime_overrides": get_runtime_overrides(),
        "effective": {
            "primary_chat": get_primary_chat_runtime(),
            "embedding": get_embedding_runtime(),
            "security_mode": get_effective_model_config().get("security_mode", "plaintext"),
        },
    }
