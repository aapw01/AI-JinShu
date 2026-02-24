"""Chapters routes."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db, resolve_novel
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
        created_at=c.created_at.isoformat() if c.created_at else "",
    )


@router.get("/{novel_id}/chapters", response_model=list[ChapterResponse])
def list_chapters(novel_id: str, db: Session = Depends(get_db)):
    """List chapters for a novel."""
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise HTTPException(404, "Novel not found")
    chapters = db.query(Chapter).filter(Chapter.novel_id == novel.id).order_by(Chapter.chapter_num).all()
    uuid_str = novel.uuid or str(novel.id)
    return [_to_response(c, uuid_str) for c in chapters]


@router.get("/{novel_id}/chapters/{chapter_num}", response_model=ChapterResponse)
def get_chapter(novel_id: str, chapter_num: int, db: Session = Depends(get_db)):
    """Get a specific chapter."""
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise HTTPException(404, "Novel not found")
    chapter = (
        db.query(Chapter)
        .filter(Chapter.novel_id == novel.id, Chapter.chapter_num == chapter_num)
        .first()
    )
    if not chapter:
        raise HTTPException(404, "Chapter not found")
    return _to_response(chapter, novel.uuid or str(novel.id))


@router.get("/{novel_id}/chapter-progress", response_model=list[ChapterProgressItem])
def get_chapter_progress(novel_id: str, db: Session = Depends(get_db)):
    """Return full chapter list with generation status for left sidebar."""
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise HTTPException(404, "Novel not found")

    outlines = (
        db.query(ChapterOutline)
        .filter(ChapterOutline.novel_id == novel.id)
        .order_by(ChapterOutline.chapter_num)
        .all()
    )
    chapters = (
        db.query(Chapter)
        .filter(Chapter.novel_id == novel.id)
        .order_by(Chapter.chapter_num)
        .all()
    )
    generated_map = {c.chapter_num: c for c in chapters}

    active_task = (
        db.query(GenerationTask)
        .filter(
            GenerationTask.novel_id == novel.id,
            GenerationTask.status.in_(["submitted", "running"]),
        )
        .order_by(GenerationTask.updated_at.desc())
        .first()
    )
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
    chapter = (
        db.query(Chapter)
        .filter(Chapter.novel_id == novel.id, Chapter.chapter_num == chapter_num)
        .first()
    )
    if not chapter:
        raise HTTPException(404, "Chapter not found")
    if data.title is not None:
        chapter.title = data.title
    if data.content is not None:
        chapter.content = data.content
    db.commit()
    db.refresh(chapter)
    return _to_response(chapter, novel.uuid or str(novel.id))
