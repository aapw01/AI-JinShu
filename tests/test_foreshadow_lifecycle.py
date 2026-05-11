"""#6 foreshadow lifecycle tests."""

from __future__ import annotations

import pytest

from app.core import feature_flags
from app.core.database import SessionLocal
from app.models.novel import Novel, StoryForeshadow
from app.services.memory.foreshadow_lifecycle import advance_foreshadows


@pytest.fixture(autouse=True)
def _clear():
    feature_flags.invalidate_flags_cache()
    db = SessionLocal()
    try:
        db.query(StoryForeshadow).delete()
        db.commit()
    finally:
        db.close()
    yield
    feature_flags.invalidate_flags_cache()


@pytest.fixture
def session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _make_novel(session) -> Novel:
    n = Novel(title="t")
    session.add(n)
    session.commit()
    return n


def _seed_fs(session, novel_id, **kwargs):
    fs = StoryForeshadow(
        foreshadow_id=kwargs["foreshadow_id"],
        novel_id=novel_id,
        novel_version_id=None,
        title=kwargs.get("title", "t"),
        planted_chapter=kwargs.get("planted_chapter", 1),
        lifecycle_state=kwargs.get("lifecycle_state", "planned"),
        plant_chapter=kwargs.get("plant_chapter"),
        payoff_chapter=kwargs.get("payoff_chapter"),
        match_confidence=kwargs.get("match_confidence"),
    )
    session.add(fs)
    session.commit()
    return fs


def test_flag_off_passthrough(session):
    n = _make_novel(session)
    _seed_fs(session, n.id, foreshadow_id="f1", plant_chapter=1)
    out = advance_foreshadows(
        session, novel_id=n.id, novel_version_id=None, current_chapter=2
    )
    assert out == []


def _enable():
    feature_flags.set_flag(
        "consistency.foreshadow_lifecycle_v1",
        enabled=True,
        rollout_pct=100,
        changed_by="t",
        reason="enable",
    )


def test_planned_to_planted(session):
    _enable()
    n = _make_novel(session)
    _seed_fs(session, n.id, foreshadow_id="f1", plant_chapter=1, lifecycle_state="planned")
    out = advance_foreshadows(
        session, novel_id=n.id, novel_version_id=None, current_chapter=1
    )
    assert any(t.from_state == "planned" and t.to_state == "planted" for t in out)


def test_planted_to_paid(session):
    _enable()
    n = _make_novel(session)
    _seed_fs(
        session,
        n.id,
        foreshadow_id="f1",
        plant_chapter=1,
        payoff_chapter=4,
        match_confidence=0.9,
        lifecycle_state="planted",
    )
    out = advance_foreshadows(
        session, novel_id=n.id, novel_version_id=None, current_chapter=4
    )
    assert any(t.to_state == "paid" for t in out)


def test_low_confidence_does_not_pay(session):
    _enable()
    n = _make_novel(session)
    _seed_fs(
        session,
        n.id,
        foreshadow_id="f1",
        plant_chapter=1,
        payoff_chapter=4,
        match_confidence=0.3,
        lifecycle_state="planted",
    )
    out = advance_foreshadows(
        session, novel_id=n.id, novel_version_id=None, current_chapter=4
    )
    assert all(t.to_state != "paid" for t in out)


def test_planted_to_stale_overdue(session):
    _enable()
    n = _make_novel(session)
    _seed_fs(
        session,
        n.id,
        foreshadow_id="f1",
        plant_chapter=1,
        lifecycle_state="planted",
    )
    # gate threshold default = 5
    out = advance_foreshadows(
        session, novel_id=n.id, novel_version_id=None, current_chapter=10
    )
    assert any(t.to_state == "stale" for t in out)
