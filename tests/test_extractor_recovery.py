"""Fact extractor recovery loop tests (#11 §11.4)."""

from __future__ import annotations

import pytest

from app.core import feature_flags
from app.core.database import SessionLocal
from app.models.novel import FactExtractionFailure, Novel
from app.tasks import extractor_recovery


@pytest.fixture(autouse=True)
def _clear():
    feature_flags.invalidate_flags_cache()
    extractor_recovery.register_runner(extractor_recovery._default_runner)
    yield
    feature_flags.invalidate_flags_cache()
    extractor_recovery.register_runner(extractor_recovery._default_runner)


@pytest.fixture
def session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_flag_off_skips_loop(session):
    out = extractor_recovery.run_recovery_once()
    assert out.get("skipped_disabled") == 1


def _enable_flag():
    feature_flags.set_flag(
        "extractor.self_heal",
        enabled=True,
        rollout_pct=100,
        changed_by="test",
        reason="enable for tests",
    )


def _seed_failure(session) -> FactExtractionFailure:
    n = Novel(title="t")
    session.add(n)
    session.commit()
    f = FactExtractionFailure(
        novel_id=n.id,
        chapter_num=3,
        failure_kind="parse_error",
        retry_count=0,
        status="pending",
    )
    session.add(f)
    session.commit()
    return f


def test_default_runner_marks_failed(session):
    _enable_flag()
    f = _seed_failure(session)
    out = extractor_recovery.run_recovery_once()
    assert out.get("failed", 0) >= 1
    session.refresh(f)
    assert f.retry_count == 1
    assert f.status == "pending"


def test_runner_recovers(session):
    _enable_flag()
    f = _seed_failure(session)

    extractor_recovery.register_runner(lambda _row: True)
    out = extractor_recovery.run_recovery_once()
    assert out.get("recovered", 0) >= 1
    session.refresh(f)
    assert f.status == "recovered"


def test_escalates_after_max_retries(session):
    _enable_flag()
    f = _seed_failure(session)
    f.retry_count = 2  # next failure will hit MAX_RETRIES (3)
    session.commit()

    out = extractor_recovery.run_recovery_once()
    assert out.get("escalated", 0) >= 1
    session.refresh(f)
    assert f.status == "escalated"
