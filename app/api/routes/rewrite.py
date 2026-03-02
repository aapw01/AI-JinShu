"""Versioning and rewrite workflow routes."""
from __future__ import annotations

from datetime import datetime, timezone
from difflib import SequenceMatcher
import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.api_errors import http_error
from app.core.authz.deps import require_permission
from app.core.authz.resources import load_novel_resource
from app.core.authz.types import Permission, Principal
from app.core.database import get_db, resolve_novel
from app.core.logging_config import log_event
from app.core.time_utils import to_utc_iso_z
from app.models.novel import ChapterVersion, NovelVersion, RewriteRequest
from app.schemas.novel import (
    NovelVersionResponse,
    RewriteRequestCreate,
    RewriteRequestResponse,
)
from app.services.rewrite.service import (
    activate_version,
    create_target_version,
    ensure_default_version,
    get_chapter_version,
    get_version_or_default,
    list_versions,
    persist_annotations,
    validate_annotation_payload,
)
from app.services.scheduler.scheduler_service import get_task_by_public_id, submit_task
from app.tasks.rewrite import submit_rewrite_task  # legacy patch target for tests

router = APIRouter()
logger = logging.getLogger(__name__)


class ActivateVersionResponse(BaseModel):
    ok: bool
    active_version_id: int


def _to_version_response(v: NovelVersion, novel_public_id: str) -> NovelVersionResponse:
    return NovelVersionResponse(
        id=v.id,
        novel_id=novel_public_id,
        version_no=v.version_no,
        parent_version_id=v.parent_version_id,
        status=v.status,
        is_default=bool(v.is_default),
        created_at=to_utc_iso_z(v.created_at),
        updated_at=to_utc_iso_z(v.updated_at),
    )


def _to_rewrite_response(r: RewriteRequest, novel_public_id: str) -> RewriteRequestResponse:
    eta_seconds: int | None = None
    eta_label: str | None = None
    if r.status in {"running", "submitted"}:
        total = max(1, int(r.rewrite_to_chapter - r.rewrite_from_chapter + 1))
        current = int(r.current_chapter or 0)
        done = max(0, min(total, current - r.rewrite_from_chapter + 1)) if current > 0 else 0
        if done > 0 and r.created_at is not None:
            created = r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=timezone.utc)
            elapsed = max(1.0, (datetime.now(timezone.utc) - created).total_seconds())
            avg_sec = elapsed / done
            remaining = max(0, total - done)
            eta_seconds = int(avg_sec * remaining)
            eta_label = _format_eta(eta_seconds)
        elif done == 0:
            eta_label = "估算中"

    return RewriteRequestResponse(
        id=r.id,
        novel_id=novel_public_id,
        base_version_id=r.base_version_id,
        target_version_id=r.target_version_id,
        task_id=r.task_id,
        status=r.status,
        rewrite_from_chapter=r.rewrite_from_chapter,
        rewrite_to_chapter=r.rewrite_to_chapter,
        current_chapter=r.current_chapter,
        progress=float(r.progress or 0.0),
        eta_seconds=eta_seconds,
        eta_label=eta_label,
        message=r.message,
        error=r.error,
        created_at=to_utc_iso_z(r.created_at),
        updated_at=to_utc_iso_z(r.updated_at),
    )


def _format_eta(seconds: int) -> str:
    sec = max(0, int(seconds))
    if sec < 60:
        return f"约{sec}秒"
    minutes = sec // 60
    if minutes < 60:
        return f"约{minutes}分钟"
    hours = minutes // 60
    mins = minutes % 60
    if mins == 0:
        return f"约{hours}小时"
    return f"约{hours}小时{mins}分钟"


@router.get("/{novel_id}/versions/{version_id}/diff")
def get_version_diff(
    novel_id: str,
    version_id: int,
    compare_to: int,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.NOVEL_READ, resource_loader=load_novel_resource)),
):
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise http_error(404, "novel_not_found", "Novel not found")
    for vid in (version_id, compare_to):
        owner = db.execute(
            select(NovelVersion.novel_id).where(NovelVersion.id == vid)
        ).scalar_one_or_none()
        if owner != novel.id:
            raise http_error(404, "version_not_found", f"Version {vid} not found for this novel")
    left = db.execute(
        select(ChapterVersion).where(ChapterVersion.novel_version_id == compare_to).order_by(ChapterVersion.chapter_num.asc())
    ).scalars().all()
    right = db.execute(
        select(ChapterVersion).where(ChapterVersion.novel_version_id == version_id).order_by(ChapterVersion.chapter_num.asc())
    ).scalars().all()
    if not left or not right:
        raise http_error(404, "version_not_found_or_empty", "Version not found or has no chapters")
    left_map = {int(c.chapter_num): c for c in left}
    right_map = {int(c.chapter_num): c for c in right}
    chapter_nums = sorted(set(left_map.keys()) | set(right_map.keys()))
    chapters = []
    changed_count = 0
    for num in chapter_nums:
        left_chapter = left_map.get(num)
        right_chapter = right_map.get(num)
        l_title = str((left_chapter.title if left_chapter else "") or "")
        r_title = str((right_chapter.title if right_chapter else "") or "")
        l_content = str((left_chapter.content if left_chapter else "") or "")
        r_content = str((right_chapter.content if right_chapter else "") or "")
        similarity = SequenceMatcher(None, l_content, r_content).ratio() if (left_chapter or right_chapter) else 0.0
        title_changed = l_title != r_title
        content_changed = similarity < 0.995
        if title_changed or content_changed:
            changed_count += 1
        chapters.append(
            {
                "chapter_num": num,
                "title_before": l_title or None,
                "title_after": r_title or None,
                "title_changed": title_changed,
                "content_similarity": round(float(similarity), 4),
                "content_changed": content_changed,
                "status_before": (left_chapter.status if left_chapter else None),
                "status_after": (right_chapter.status if right_chapter else None),
            }
        )
    return {
        "novel_id": novel.uuid or str(novel.id),
        "version_id": version_id,
        "compare_to": compare_to,
        "summary": {
            "total_chapters": len(chapter_nums),
            "changed_chapters": changed_count,
            "change_ratio": round(changed_count / max(1, len(chapter_nums)), 4),
        },
        "chapters": chapters,
    }


@router.get("/{novel_id}/versions", response_model=list[NovelVersionResponse])
def get_versions(
    novel_id: str,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.NOVEL_READ, resource_loader=load_novel_resource)),
):
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise http_error(404, "novel_not_found", "Novel not found")
    rows = list_versions(db, novel.id)
    db.commit()
    return [_to_version_response(v, novel.uuid or str(novel.id)) for v in rows]


@router.post("/{novel_id}/versions/{version_id}/activate", response_model=ActivateVersionResponse)
def activate_novel_version(
    novel_id: str,
    version_id: int,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.NOVEL_REWRITE, resource_loader=load_novel_resource)),
):
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise http_error(404, "novel_not_found", "Novel not found")
    try:
        activate_version(db, novel.id, version_id)
    except ValueError:
        raise http_error(404, "version_not_found", "Version not found")
    db.commit()
    return ActivateVersionResponse(ok=True, active_version_id=version_id)


@router.post("/{novel_id}/rewrite-requests", response_model=RewriteRequestResponse)
def create_rewrite_request(
    novel_id: str,
    req: RewriteRequestCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(Permission.NOVEL_REWRITE, resource_loader=load_novel_resource)),
):
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise http_error(404, "novel_not_found", "Novel not found")

    if not req.annotations:
        raise http_error(400, "annotations_required", "annotations 不能为空")

    ensure_default_version(db, novel.id)
    try:
        base_version = get_version_or_default(db, novel.id, req.base_version_id)
    except ValueError:
        raise http_error(404, "base_version_not_found", "Base version not found")

    chapter_nums: list[int] = []
    for ann in req.annotations:
        if ann.chapter_num <= 0:
            raise http_error(400, "invalid_chapter_num", "chapter_num 必须大于0")
        _chapter_row, chapter = get_chapter_version(db, novel.id, ann.chapter_num, base_version.id)
        if not chapter:
            raise http_error(400, "base_version_chapter_not_found", f"第{ann.chapter_num}章在基础版本中不存在")
        if not ann.instruction.strip():
            raise http_error(400, "instruction_required", "instruction 不能为空")
        try:
            validate_annotation_payload(chapter.content or "", ann.start_offset, ann.end_offset, ann.selected_text)
        except ValueError as e:
            raise http_error(400, "invalid_annotation", str(e))
        chapter_nums.append(ann.chapter_num)

    rewrite_from = min(chapter_nums)
    max_chapter_stmt = select(ChapterVersion.chapter_num).where(ChapterVersion.novel_version_id == base_version.id)
    chapter_candidates = [x[0] for x in db.execute(max_chapter_stmt).all()]
    if not chapter_candidates:
        raise http_error(400, "base_version_empty", "基础版本没有章节")
    rewrite_to = max(chapter_candidates)

    target_version = create_target_version(db, novel.id, base_version, rewrite_from)
    rewrite = RewriteRequest(
        novel_id=novel.id,
        base_version_id=base_version.id,
        target_version_id=target_version.id,
        status="submitted",
        rewrite_from_chapter=rewrite_from,
        rewrite_to_chapter=rewrite_to,
        progress=0.0,
        message=f"重写任务已创建：第{rewrite_from}章到第{rewrite_to}章",
    )
    db.add(rewrite)
    db.flush()

    persist_annotations(
        db,
        rewrite_request=rewrite,
        novel_id=novel.id,
        base_version_id=base_version.id,
        annotations=[a.model_dump() for a in req.annotations],
    )

    creation_task = submit_task(
        db,
        user_uuid=principal.user_uuid or "",
        task_type="rewrite",
        resource_type="rewrite_request",
        resource_id=int(rewrite.id),
        payload={
            "novel_id": int(novel.id),
            "rewrite_request_id": int(rewrite.id),
            "base_version_id": int(base_version.id),
            "target_version_id": int(target_version.id),
            "rewrite_from": int(rewrite_from),
            "rewrite_to": int(rewrite_to),
        },
    )
    rewrite.task_id = creation_task.public_id
    rewrite.status = "queued"
    target_version.source_task_id = creation_task.public_id
    target_version.status = "draft"

    db.commit()
    db.refresh(rewrite)
    log_event(
        logger,
        "rewrite.request.created",
        novel_id=novel.id,
        task_id=creation_task.public_id,
        run_state="queued",
        chapter_num=rewrite_from,
        volume_no=None,
        base_version_id=base_version.id,
        target_version_id=target_version.id,
    )
    return _to_rewrite_response(rewrite, novel.uuid or str(novel.id))


@router.get("/{novel_id}/rewrite-requests/{request_id}/status", response_model=RewriteRequestResponse)
def get_rewrite_status(
    novel_id: str,
    request_id: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(Permission.NOVEL_READ, resource_loader=load_novel_resource)),
):
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise http_error(404, "novel_not_found", "Novel not found")
    row = db.execute(
        select(RewriteRequest).where(RewriteRequest.id == request_id, RewriteRequest.novel_id == novel.id)
    ).scalar_one_or_none()
    if not row:
        raise http_error(404, "rewrite_request_not_found", "Rewrite request not found")
    if row.task_id:
        task = get_task_by_public_id(db, public_id=row.task_id, user_uuid=principal.user_uuid)
        if task:
            row.status = task.status
            row.progress = float(task.progress or row.progress or 0.0)
            row.message = task.message or row.message
            row.error = task.error_detail or row.error
    return _to_rewrite_response(row, novel.uuid or str(novel.id))


@router.post("/{novel_id}/rewrite-requests/{request_id}/retry", response_model=RewriteRequestResponse)
def retry_rewrite_request(
    novel_id: str,
    request_id: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(Permission.NOVEL_REWRITE, resource_loader=load_novel_resource)),
):
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise http_error(404, "novel_not_found", "Novel not found")

    row = db.execute(
        select(RewriteRequest).where(RewriteRequest.id == request_id, RewriteRequest.novel_id == novel.id)
    ).scalar_one_or_none()
    if not row:
        raise http_error(404, "rewrite_request_not_found", "Rewrite request not found")
    if row.status not in {"failed", "cancelled"}:
        raise http_error(409, "rewrite_not_retryable", f"当前状态 {row.status} 不支持重试")

    creation_task = submit_task(
        db,
        user_uuid=principal.user_uuid or "",
        task_type="rewrite",
        resource_type="rewrite_request",
        resource_id=int(row.id),
        payload={
            "novel_id": int(novel.id),
            "rewrite_request_id": int(row.id),
            "base_version_id": int(row.base_version_id),
            "target_version_id": int(row.target_version_id),
            "rewrite_from": int(row.current_chapter or row.rewrite_from_chapter),
            "rewrite_to": int(row.rewrite_to_chapter),
        },
    )
    row.task_id = creation_task.public_id
    row.status = "queued"
    row.error = None
    row.message = f"重试已入队：从第{row.current_chapter or row.rewrite_from_chapter}章继续"

    target = db.execute(select(NovelVersion).where(NovelVersion.id == row.target_version_id)).scalar_one_or_none()
    if target:
        target.status = "draft"
        target.source_task_id = creation_task.public_id

    db.commit()
    db.refresh(row)
    log_event(
        logger,
        "rewrite.request.retry",
        novel_id=novel.id,
        task_id=creation_task.public_id,
        run_state="queued",
        chapter_num=row.current_chapter or row.rewrite_from_chapter,
        base_version_id=row.base_version_id,
        target_version_id=row.target_version_id,
    )
    return _to_rewrite_response(row, novel.uuid or str(novel.id))
