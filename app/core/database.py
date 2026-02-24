"""Database session and engine."""
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker, declarative_base

from app.core.config import get_settings

settings = get_settings()
engine_kwargs = {}
if settings.database_url.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}
engine = create_engine(settings.database_url, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """Dependency for FastAPI routes."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def resolve_novel(db, novel_id: str):
    """Resolve novel_id (uuid or integer string) to Novel instance."""
    from app.models.novel import Novel
    if "-" in novel_id and len(novel_id) > 10:
        stmt = select(Novel).where(Novel.uuid == novel_id)
        return db.execute(stmt).scalar_one_or_none()
    try:
        pk = int(novel_id)
        stmt = select(Novel).where(Novel.id == pk)
        return db.execute(stmt).scalar_one_or_none()
    except ValueError:
        return None
