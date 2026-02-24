"""Pytest fixtures."""
import os
import pathlib

import pytest
from fastapi.testclient import TestClient

TEST_DB = pathlib.Path(__file__).parent / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"

from app.main import app
from app.core.database import Base, engine


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture
def client():
    return TestClient(app)
