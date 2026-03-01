"""Character state manager - uses novel_memory table."""
from typing import Optional
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.novel import NovelMemory


class CharacterStateManager:
    """Manage character states via novel_memory table."""

    def get_states(
        self, novel_id: int, chapter_num: int, db: Optional[Session] = None
    ) -> list[dict]:
        """Get character states at given chapter from novel_memory."""
        should_close = db is None
        db = db or SessionLocal()
        try:
            stmt = select(NovelMemory).where(
                NovelMemory.novel_id == novel_id,
                NovelMemory.memory_type == "character",
            )
            rows = db.execute(stmt).scalars().all()
            return [{"key": r.key, "content": r.content} for r in rows]
        finally:
            if should_close:
                db.close()

    def update_state(
        self,
        novel_id: int,
        character_id: str,
        state: dict,
        db: Optional[Session] = None,
    ):
        """Update character state in novel_memory."""
        should_close = db is None
        db = db or SessionLocal()
        try:
            stmt = select(NovelMemory).where(
                NovelMemory.novel_id == novel_id,
                NovelMemory.memory_type == "character",
                NovelMemory.key == character_id,
            )
            existing = db.execute(stmt).scalar_one_or_none()
            if existing:
                existing.content = state
            else:
                try:
                    with db.begin_nested():
                        db.add(
                            NovelMemory(
                                novel_id=novel_id,
                                memory_type="character",
                                key=character_id,
                                content=state,
                            )
                        )
                        db.flush()
                except IntegrityError:
                    existing = db.execute(stmt).scalar_one_or_none()
                    if existing:
                        existing.content = state
                    else:
                        raise
            if should_close:
                db.commit()
            else:
                db.flush()
        except Exception:
            if should_close:
                db.rollback()
            raise
        finally:
            if should_close:
                db.close()
