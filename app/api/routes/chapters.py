"""Chapters routes."""
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
import redis

from app.core.config import get_settings
from app.core.authz.deps import require_permission
from app.core.authz.resources import load_novel_resource
from app.core.authz.types import Permission, Principal
from app.core.database import get_db, resolve_novel
from app.core.time_utils import to_utc_iso_z
from app.models.novel import Chapter, ChapterOutline, GenerationTask, ChapterVersion
from app.schemas.novel import ChapterResponse
from app.services.generation.common import is_effective_title, resolve_chapter_title
from app.services.rewrite.service import get_chapter_version, list_chapter_versions

router = APIRouter()


class ChapterUpdate(BaseModel):
    title: str | None = None
    content: str | None = None


class ChapterProgressItem(BaseModel):
    chapter_num: int
    title: str | None = None
    status: str  # pending | generating | completed


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


def _to_response(c: Chapter, novel_uuid: str) -> ChapterResponse:
    return ChapterResponse(
        id=c.id,
        novel_id=novel_uuid,
        chapter_num=c.chapter_num,
        title=c.title,
        content=c.content,
        summary=c.summary,
        status=c.status,
        review_score=c.review_score,
        language_quality_score=c.language_quality_score,
        language_quality_report=c.language_quality_report,
        created_at=to_utc_iso_z(c.created_at),
    )


def _to_version_response(c: ChapterVersion, novel_uuid: str) -> ChapterResponse:
    return ChapterResponse(
        id=c.id,
        novel_id=novel_uuid,
        chapter_num=c.chapter_num,
        title=c.title,
        content=c.content,
        summary=c.summary,
        status=c.status,
        review_score=None,
        language_quality_score=None,
        language_quality_report=None,
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
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise HTTPException(404, "Novel not found")
    uuid_str = novel.uuid or str(novel.id)
    try:
        _, chapters = list_chapter_versions(db, novel.id, version_id)
        db.commit()
        return [_to_version_response(c, uuid_str) for c in chapters]
    except ValueError:
        raise HTTPException(404, "Version not found")


@router.get("/{novel_id}/chapters/{chapter_num}", response_model=ChapterResponse)
def get_chapter(
    novel_id: str,
    chapter_num: int,
    version_id: int | None = None,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.NOVEL_READ, resource_loader=load_novel_resource)),
):
    """Get a specific chapter."""
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise HTTPException(404, "Novel not found")
    try:
        _, chapter = get_chapter_version(db, novel.id, chapter_num, version_id)
    except ValueError:
        raise HTTPException(404, "Version not found")
    if not chapter:
        raise HTTPException(404, "Chapter not found")
    db.commit()
    return _to_version_response(chapter, novel.uuid or str(novel.id))


@router.get("/{novel_id}/chapter-progress", response_model=list[ChapterProgressItem])
def get_chapter_progress(
    novel_id: str,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.NOVEL_READ, resource_loader=load_novel_resource)),
):
    """Return full chapter list with generation status for left sidebar."""
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise HTTPException(404, "Novel not found")

    outlines_stmt = select(ChapterOutline).where(ChapterOutline.novel_id == novel.id).order_by(ChapterOutline.chapter_num)
    outlines = db.execute(outlines_stmt).scalars().all()
    chapters_stmt = select(Chapter).where(Chapter.novel_id == novel.id).order_by(Chapter.chapter_num)
    chapters = db.execute(chapters_stmt).scalars().all()
    generated_map = {c.chapter_num: c for c in chapters}

    active_stmt = (
        select(GenerationTask)
        .where(
            GenerationTask.novel_id == novel.id,
            GenerationTask.status.in_(["submitted", "running"]),
        )
        .order_by(GenerationTask.updated_at.desc())
    )
    active_task = db.execute(active_stmt).scalar_one_or_none()
    generating_chapter = _get_generating_chapter_from_redis(novel.id)
    if generating_chapter is None:
        generating_chapter = active_task.current_chapter if active_task else None

    result: list[ChapterProgressItem] = []
    if outlines:
        for o in outlines:
            status = "pending"
            chapter_row = generated_map.get(o.chapter_num)
            if o.chapter_num in generated_map:
                raw = chapter_row.status or "completed"
                status = "completed" if raw == "completed" else "generating"
            elif generating_chapter is not None and o.chapter_num == generating_chapter:
                status = "generating"
            result.append(
                ChapterProgressItem(
                    chapter_num=o.chapter_num,
                    title=_resolve_progress_title(
                        chapter_num=o.chapter_num,
                        outline_title=o.title,
                        chapter_title=chapter_row.title if chapter_row else None,
                    ),
                    status=status,
                )
            )
        return result

    # Fallback for old novels without outlines: show generated chapters only.
    for c in chapters:
        result.append(
            ChapterProgressItem(
                chapter_num=c.chapter_num,
                title=_resolve_progress_title(chapter_num=c.chapter_num, outline_title=None, chapter_title=c.title),
                status="completed",
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
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise HTTPException(404, "Novel not found")
    try:
        _, chapter = get_chapter_version(db, novel.id, chapter_num, version_id)
    except ValueError:
        raise HTTPException(404, "Version not found")
    if not chapter:
        raise HTTPException(404, "Chapter not found")
    if data.title is not None:
        chapter.title = data.title
    if data.content is not None:
        chapter.content = data.content
    db.commit()
    db.refresh(chapter)
    return _to_version_response(chapter, novel.uuid or str(novel.id))
