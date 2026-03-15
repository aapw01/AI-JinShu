"""Chapters routes."""
import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
import redis

from app.core.api_errors import http_error
from app.core.config import get_settings
from app.core.authz.deps import require_permission
from app.core.authz.resources import load_novel_resource
from app.core.authz.types import Permission, Principal
from app.core.database import get_db, resolve_novel
from app.core.time_utils import to_utc_iso_z
from app.models.creation_task import CreationTask
from app.models.novel import ChapterOutline, ChapterVersion
from app.schemas.novel import ChapterProgressResponse, ChapterResponse
from app.services.generation.common import is_effective_title, resolve_chapter_title
from app.services.rewrite.service import get_chapter_version, list_chapter_versions

router = APIRouter()


class ChapterUpdate(BaseModel):
    title: str | None = None
    content: str | None = None


def _resolve_volume_size(config: dict | None) -> int:
    raw = (config or {}).get("volume_size", 30) if isinstance(config, dict) else 30
    try:
        size = int(raw)
    except (TypeError, ValueError):
        size = 30
    return max(1, min(size, 200))


def _resolve_volume_no(chapter_num: int, volume_size: int) -> int:
    return ((max(chapter_num, 1) - 1) // volume_size) + 1


def _count_content_words(content: str | None) -> int:
    text = (content or "").strip()
    if not text:
        return 0
    return len("".join(text.split()))


def _resolve_progress_title(chapter_num: int, outline_title: str | None, chapter_title: str | None) -> str:
    if is_effective_title(chapter_title, chapter_num):
        return str(chapter_title).strip()
    if is_effective_title(outline_title, chapter_num):
        return str(outline_title).strip()
    return resolve_chapter_title(chapter_num=chapter_num, title=chapter_title or outline_title)


def _get_generating_chapter_from_redis(novel_db_id: int) -> int | None:
    """Get real-time generating chapter from Redis status cache."""
    try:
        r = redis.from_url(get_settings().redis_url)
        raw = r.get(f"generation:novel:{novel_db_id}")
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return None
        status = str(payload.get("status") or "")
        current_phase = str(payload.get("current_phase") or payload.get("step") or "")
        chapter = int(payload.get("current_chapter") or 0)
        if chapter <= 0:
            return None
        if status in {"running", "generating", "submitted", "awaiting_outline_confirmation"}:
            return chapter
        if current_phase in {"chapter_writing", "chapter_review", "chapter_finalizing", "consistency_check"}:
            return chapter
    except Exception:
        return None
    return None


def _get_generating_chapter_from_creation_task(task: CreationTask) -> int | None:
    payload = task.payload_json if isinstance(task.payload_json, dict) else {}
    cursor = task.resume_cursor_json if isinstance(task.resume_cursor_json, dict) else {}
    runtime_state = cursor.get("runtime_state") if isinstance(cursor.get("runtime_state"), dict) else {}

    if bool(payload.get("awaiting_outline_confirmation")) and not bool(payload.get("outline_confirmed")):
        chapter = int(payload.get("start_chapter") or 0)
        return chapter or None

    if str(runtime_state.get("node") or "") == "final_book_review":
        return None

    resume_from = int(runtime_state.get("resume_from_chapter") or cursor.get("next") or 0)
    if resume_from > 0:
        return resume_from

    chapter = int(payload.get("start_chapter") or 0)
    return chapter or None


def _to_version_response(
    c: ChapterVersion,
    novel_uuid: str,
) -> ChapterResponse:
    return ChapterResponse(
        id=c.id,
        novel_id=novel_uuid,
        version_id=int(c.novel_version_id),
        chapter_num=c.chapter_num,
        title=c.title,
        content=c.content,
        summary=c.summary,
        status=c.status,
        review_score=c.review_score,
        language_quality_score=c.language_quality_score,
        language_quality_report=c.language_quality_report,
        word_count=_count_content_words(c.content),
        created_at=to_utc_iso_z(c.created_at),
    )


@router.get("/{novel_id}/chapters", response_model=list[ChapterResponse])
def list_chapters(
    novel_id: str,
    version_id: int | None = None,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.NOVEL_READ, resource_loader=load_novel_resource)),
):
    """List chapters for a novel."""
    if version_id is None:
        raise http_error(400, "missing_version_id", "version_id is required")
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise http_error(404, "novel_not_found", "Novel not found")
    uuid_str = novel.uuid or str(novel.id)
    try:
        _, chapters = list_chapter_versions(db, novel.id, int(version_id))
        db.commit()
        return [_to_version_response(c, uuid_str) for c in chapters]
    except ValueError:
        raise http_error(404, "version_not_found", "Version not found")


@router.get("/{novel_id}/chapters/{chapter_num}", response_model=ChapterResponse)
def get_chapter(
    novel_id: str,
    chapter_num: int,
    version_id: int | None = None,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.NOVEL_READ, resource_loader=load_novel_resource)),
):
    """Get a specific chapter."""
    if version_id is None:
        raise http_error(400, "missing_version_id", "version_id is required")
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise http_error(404, "novel_not_found", "Novel not found")
    try:
        _, chapter = get_chapter_version(db, novel.id, chapter_num, int(version_id))
    except ValueError:
        raise http_error(404, "version_not_found", "Version not found")
    if not chapter:
        raise http_error(404, "chapter_not_found", "Chapter not found")
    db.commit()
    return _to_version_response(chapter, novel.uuid or str(novel.id))


@router.get("/{novel_id}/chapter-progress", response_model=list[ChapterProgressResponse])
def get_chapter_progress(
    novel_id: str,
    version_id: int | None = None,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.NOVEL_READ, resource_loader=load_novel_resource)),
):
    """Return full chapter list with generation status for left sidebar."""
    if version_id is None:
        raise http_error(400, "missing_version_id", "version_id is required")
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise http_error(404, "novel_not_found", "Novel not found")

    outlines_stmt = (
        select(ChapterOutline)
        .where(
            ChapterOutline.novel_id == novel.id,
            ChapterOutline.novel_version_id == int(version_id),
        )
        .order_by(ChapterOutline.chapter_num)
    )
    outlines = db.execute(outlines_stmt).scalars().all()
    _, version_chapters = list_chapter_versions(db, novel.id, int(version_id))
    generated_map = {c.chapter_num: c for c in version_chapters}

    active_stmt = (
        select(CreationTask)
        .where(
            CreationTask.resource_type == "novel",
            CreationTask.resource_id == novel.id,
            CreationTask.task_type == "generation",
            CreationTask.status.in_(["queued", "dispatching", "running"]),
        )
        .order_by(CreationTask.updated_at.desc())
        .limit(1)
    )
    active_task = db.execute(active_stmt).scalar_one_or_none()
    generating_chapter = _get_generating_chapter_from_redis(novel.id)
    if generating_chapter is None and active_task:
        generating_chapter = _get_generating_chapter_from_creation_task(active_task)
    volume_size = _resolve_volume_size(novel.config if isinstance(novel.config, dict) else None)

    result: list[ChapterProgressResponse] = []
    if outlines:
        for o in outlines:
            status = "pending"
            chapter_row = generated_map.get(o.chapter_num)
            if o.chapter_num in generated_map:
                raw = chapter_row.status or "completed"
                status = "completed" if raw in ("completed", "quality_blocked") else "generating"
            elif generating_chapter is not None and o.chapter_num == generating_chapter:
                status = "generating"
            result.append(
                ChapterProgressResponse(
                    chapter_num=o.chapter_num,
                    title=_resolve_progress_title(
                        chapter_num=o.chapter_num,
                        outline_title=o.title,
                        chapter_title=chapter_row.title if chapter_row else None,
                    ),
                    status=status,
                    volume_no=_resolve_volume_no(o.chapter_num, volume_size),
                    volume_size=volume_size,
                )
            )
        return result

    # Outlines may be absent for drafts; return generated chapter rows only.
    for c in version_chapters:
        result.append(
            ChapterProgressResponse(
                chapter_num=c.chapter_num,
                title=_resolve_progress_title(chapter_num=c.chapter_num, outline_title=None, chapter_title=c.title),
                status="completed",
                volume_no=_resolve_volume_no(c.chapter_num, volume_size),
                volume_size=volume_size,
            )
        )
    return result


@router.put("/{novel_id}/chapters/{chapter_num}", response_model=ChapterResponse)
def update_chapter(
    novel_id: str,
    chapter_num: int,
    data: ChapterUpdate,
    version_id: int | None = None,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.NOVEL_UPDATE, resource_loader=load_novel_resource)),
):
    """Update a chapter's title or content."""
    if version_id is None:
        raise http_error(400, "missing_version_id", "version_id is required")
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise http_error(404, "novel_not_found", "Novel not found")
    try:
        _, chapter = get_chapter_version(db, novel.id, chapter_num, int(version_id))
    except ValueError:
        raise http_error(404, "version_not_found", "Version not found")
    if not chapter:
        raise http_error(404, "chapter_not_found", "Chapter not found")
    if data.title is not None:
        chapter.title = data.title
    if data.content is not None:
        chapter.content = data.content
    db.commit()
    db.refresh(chapter)
    return _to_version_response(chapter, novel.uuid or str(novel.id))
