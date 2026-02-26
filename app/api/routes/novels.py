"""Novels CRUD routes."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db, resolve_novel
from app.core.time_utils import to_utc_iso_z
from app.models.novel import Novel
from app.schemas.novel import (
    IdeaFrameworkRequest,
    IdeaFrameworkResponse,
    NovelCreate,
    NovelUpdate,
    NovelResponse,
)
from app.services.generation.idea_framework import generate_idea_framework

router = APIRouter()


def _to_response(novel: Novel) -> NovelResponse:
    return NovelResponse(
        id=novel.uuid or str(novel.id),
        title=novel.title,
        target_language=novel.target_language or "zh",
        genre=novel.genre,
        style=novel.style,
        status=novel.status,
        created_at=to_utc_iso_z(novel.created_at),
        updated_at=to_utc_iso_z(novel.updated_at),
    )


@router.get("", response_model=list[NovelResponse])
def list_novels(skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    """List all novels, ordered by creation date (newest first)."""
    stmt = select(Novel).order_by(Novel.created_at.desc()).offset(skip).limit(limit)
    novels = db.execute(stmt).scalars().all()
    return [_to_response(n) for n in novels]


@router.post("", response_model=NovelResponse)
def create_novel(data: NovelCreate, db: Session = Depends(get_db)):
    """Create a novel."""
    novel = Novel(
        title=data.title,
        user_id=data.user_id,
        target_language=data.target_language,
        native_style_profile=data.native_style_profile,
        genre=data.genre,
        style=data.style,
        pace=data.pace,
        audience=data.audience,
        target_length=data.target_length,
        writing_method=data.writing_method,
        strategy=data.strategy,
        user_idea=data.user_idea,
        inspiration_tags=data.inspiration_tags,
        config=data.config,
    )
    db.add(novel)
    db.commit()
    db.refresh(novel)
    return _to_response(novel)


@router.post("/idea-framework", response_model=IdeaFrameworkResponse)
def generate_idea(data: IdeaFrameworkRequest):
    """Generate editable idea framework from title."""
    framework = generate_idea_framework(
        title=data.title,
        language=data.target_language,
        genre=data.genre,
        style=data.style,
        strategy=data.strategy,
    )
    editable = "\n".join(
        [
            f"一句话创意：{framework['one_liner']}",
            f"背景设定：{framework['premise']}",
            f"核心冲突：{framework['conflict']}",
            f"开篇钩子：{framework['hook']}",
            f"连载卖点：{framework['selling_point']}",
        ]
    )
    return IdeaFrameworkResponse(
        title=data.title,
        one_liner=framework["one_liner"],
        premise=framework["premise"],
        conflict=framework["conflict"],
        hook=framework["hook"],
        selling_point=framework["selling_point"],
        editable_framework=editable[:600],
    )


@router.get("/{novel_id}", response_model=NovelResponse)
def get_novel(novel_id: str, db: Session = Depends(get_db)):
    """Get novel by ID (uuid or integer)."""
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise HTTPException(404, "Novel not found")
    return _to_response(novel)


@router.put("/{novel_id}", response_model=NovelResponse)
def update_novel(novel_id: str, data: NovelUpdate, db: Session = Depends(get_db)):
    """Update novel."""
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise HTTPException(404, "Novel not found")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(novel, k, v)
    db.commit()
    db.refresh(novel)
    return _to_response(novel)


@router.delete("/{novel_id}")
def delete_novel(novel_id: str, db: Session = Depends(get_db)):
    """Delete novel."""
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise HTTPException(404, "Novel not found")
    db.delete(novel)
    db.commit()
    return {"ok": True}
