"""Database session and engine."""
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker, declarative_base

from app.core.config import get_settings

settings = get_settings()
engine_kwargs: dict = {}
if settings.database_url.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    # P2: larger pool for API server (default 5/10 is too small under concurrent generation)
    engine_kwargs["pool_size"] = 10
    engine_kwargs["max_overflow"] = 20
engine = create_engine(settings.database_url, **engine_kwargs)

if settings.database_url.startswith("sqlite"):
    # Keep sqlite behavior closer to PostgreSQL in tests/dev by enforcing FK cascades.
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def use_null_pool() -> None:
    """P1: Switch to NullPool — call from Celery worker_process_init to prevent fork-unsafe connection reuse."""
    if settings.database_url.startswith("sqlite"):
        return
    global engine, SessionLocal
    from sqlalchemy.pool import NullPool
    engine.dispose()
    engine = create_engine(settings.database_url, poolclass=NullPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


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
