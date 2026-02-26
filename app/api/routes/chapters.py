"""Chapters routes."""
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
import redis

from app.core.config import get_settings
from app.core.database import get_db, resolve_novel
from app.core.time_utils import to_utc_iso_z
from app.models.novel import Chapter, ChapterOutline, GenerationTask
from app.schemas.novel import ChapterResponse

router = APIRouter()


class ChapterUpdate(BaseModel):
    title: str | None = None
    content: str | None = None


class ChapterProgressItem(BaseModel):
    chapter_num: int
    title: str | None = None
    status: str  # pending | generating | completed


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


@router.get("/{novel_id}/chapters", response_model=list[ChapterResponse])
def list_chapters(novel_id: str, db: Session = Depends(get_db)):
    """List chapters for a novel."""
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise HTTPException(404, "Novel not found")
    stmt = select(Chapter).where(Chapter.novel_id == novel.id).order_by(Chapter.chapter_num)
    chapters = db.execute(stmt).scalars().all()
    uuid_str = novel.uuid or str(novel.id)
    return [_to_response(c, uuid_str) for c in chapters]


@router.get("/{novel_id}/chapters/{chapter_num}", response_model=ChapterResponse)
def get_chapter(novel_id: str, chapter_num: int, db: Session = Depends(get_db)):
    """Get a specific chapter."""
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise HTTPException(404, "Novel not found")
    stmt = select(Chapter).where(Chapter.novel_id == novel.id, Chapter.chapter_num == chapter_num)
    chapter = db.execute(stmt).scalar_one_or_none()
    if not chapter:
        raise HTTPException(404, "Chapter not found")
    return _to_response(chapter, novel.uuid or str(novel.id))


@router.get("/{novel_id}/chapter-progress", response_model=list[ChapterProgressItem])
def get_chapter_progress(novel_id: str, db: Session = Depends(get_db)):
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
            if o.chapter_num in generated_map:
                raw = generated_map[o.chapter_num].status or "completed"
                status = "completed" if raw == "completed" else "generating"
            elif generating_chapter is not None and o.chapter_num == generating_chapter:
                status = "generating"
            result.append(
                ChapterProgressItem(
                    chapter_num=o.chapter_num,
                    title=o.title or generated_map.get(o.chapter_num).title if o.chapter_num in generated_map else o.title,
                    status=status,
                )
            )
        return result

    # Fallback for old novels without outlines: show generated chapters only.
    for c in chapters:
        result.append(ChapterProgressItem(chapter_num=c.chapter_num, title=c.title, status="completed"))
    return result


@router.put("/{novel_id}/chapters/{chapter_num}", response_model=ChapterResponse)
def update_chapter(
    novel_id: str, chapter_num: int, data: ChapterUpdate, db: Session = Depends(get_db)
):
    """Update a chapter's title or content."""
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise HTTPException(404, "Novel not found")
    stmt = select(Chapter).where(Chapter.novel_id == novel.id, Chapter.chapter_num == chapter_num)
    chapter = db.execute(stmt).scalar_one_or_none()
    if not chapter:
        raise HTTPException(404, "Chapter not found")
    if data.title is not None:
        chapter.title = data.title
    if data.content is not None:
        chapter.content = data.content
    db.commit()
    db.refresh(chapter)
    return _to_response(chapter, novel.uuid or str(novel.id))
