"""数据库引擎与 Session 工厂。

模块职责：
- 创建 SQLAlchemy engine / SessionLocal / Base。
- 为 FastAPI 路由提供 `get_db()` 依赖。
- 为 Celery worker 提供 fork-safe 的连接池切换能力。

系统位置：
- 上游是配置系统 `app.core.config`。
- 下游是所有 ORM model、路由依赖、任务处理器。

面试可讲点：
- 为什么 API 进程和 Celery worker 需要不同的连接池策略。
- 为什么 SQLite 在测试/开发环境要显式打开外键约束。
- 为什么统一通过 SessionLocal 管理会话生命周期。
"""
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
        """为每个 SQLite 连接开启外键约束，避免测试环境行为过于宽松。"""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def use_null_pool() -> None:
    """把连接池切到 `NullPool`，避免 Celery fork 后复用父进程连接。

    这是典型的“API 能跑，但 worker 偶发报连接异常”的坑点。
    API 进程适合保留连接池以提升吞吐；Celery worker 在多进程 fork 后，
    更安全的做法是让每个进程自己重新建立数据库连接。
    """
    if settings.database_url.startswith("sqlite"):
        return
    global engine, SessionLocal
    from sqlalchemy.pool import NullPool
    engine.dispose()
    engine = create_engine(settings.database_url, poolclass=NullPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """FastAPI 路由使用的数据库依赖。

    每次请求获取一个独立 Session，请求结束后统一关闭，避免连接泄漏。
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def resolve_novel(db, novel_id: str):
    """把前端传入的小说标识解析成真正的 `Novel` 记录。

    这个项目同时兼容整数主键和公开 uuid，因此这里做一层统一解析，
    避免路由层到处散落“先判断是不是 uuid 再查库”的重复逻辑。
    """
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
