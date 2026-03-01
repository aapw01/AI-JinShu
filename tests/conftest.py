"""Pytest fixtures."""
import os
import pathlib

import pytest
from fastapi.testclient import TestClient

_worker_id = os.getenv("PYTEST_XDIST_WORKER", "main")
TEST_DB = pathlib.Path(__file__).parent / f"test-{_worker_id}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from app.main import app
from app.core.database import Base, engine
from app.core.authn import create_access_token
from app import models as _models  # noqa: F401 - ensure SQLAlchemy models are registered before create_all


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture
def client():
    c = TestClient(app)
    token = create_access_token("test-admin-user", role="admin", status="active")
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


@pytest.fixture
def anon_client():
    return TestClient(app)
