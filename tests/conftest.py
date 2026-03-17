"""Pytest fixtures."""

# ruff: noqa: E402
import os
import pathlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

_worker_id = os.getenv("PYTEST_XDIST_WORKER", "main")
TEST_DB = pathlib.Path(__file__).parent / f"test-{_worker_id}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from app.main import app
from app.core.database import Base, engine, SessionLocal
from app.core.authn import create_access_token
from app.models.novel import UsageLedger, User
from app.services.quota import ensure_user_quota
from app import models as _models  # noqa: F401 - ensure SQLAlchemy models are registered before create_all


def ensure_test_user(user_uuid: str, role: str = "user", status: str = "active") -> None:
    """Create a DB user row for test JWT tokens (idempotent)."""
    db = SessionLocal()
    try:
        if not db.execute(select(User).where(User.uuid == user_uuid)).scalar_one_or_none():
            db.add(User(uuid=user_uuid, email=f"{user_uuid}@test.local", password_hash="x", role=role, status=status))
            db.commit()
    finally:
        db.close()


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture
def client():
    ensure_test_user("test-admin-user", role="admin")
    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.uuid == "test-admin-user")).scalar_one()
        quota = ensure_user_quota(db, user)
        quota.status = "active"
        quota.monthly_chapter_limit = max(int(quota.monthly_chapter_limit or 0), 1_000_000)
        quota.monthly_token_limit = max(int(quota.monthly_token_limit or 0), 10_000_000_000)
        db.query(UsageLedger).filter(UsageLedger.user_id == user.id).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
    c = TestClient(app)
    token = create_access_token("test-admin-user", role="admin", status="active")
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


@pytest.fixture
def anon_client():
    return TestClient(app)
