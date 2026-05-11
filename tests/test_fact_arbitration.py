"""Fact arbitration tests (#9)."""

from __future__ import annotations

import pytest

from app.core.database import SessionLocal
from app.models.novel import Novel, StoryEntity, StoryFact
from app.services.memory.fact_arbitrator import arbitrate_fact


@pytest.fixture
def session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _seed(session, *, conf_a: float, conf_b: float, ch_a: int = 1, ch_b: int = 2):
    n = Novel(title="t")
    session.add(n)
    session.commit()
    e = StoryEntity(novel_id=n.id, entity_type="character", name="X")
    session.add(e)
    session.commit()
    a = StoryFact(
        novel_id=n.id,
        entity_id=e.id,
        fact_type="status",
        value_json={"v": "alive"},
        chapter_from=ch_a,
        source_chapter=ch_a,
        confidence=conf_a,
        is_active=1,
    )
    b = StoryFact(
        novel_id=n.id,
        entity_id=e.id,
        fact_type="status",
        value_json={"v": "dead"},
        chapter_from=ch_b,
        source_chapter=ch_b,
        confidence=conf_b,
        is_active=1,
    )
    session.add_all([a, b])
    session.commit()
    return n, e, a, b


def test_single_active_fact_keeps(session):
    n = Novel(title="t")
    session.add(n)
    session.commit()
    e = StoryEntity(novel_id=n.id, entity_type="character", name="Y")
    session.add(e)
    session.commit()
    f = StoryFact(
        novel_id=n.id,
        entity_id=e.id,
        fact_type="status",
        value_json={"v": "alive"},
        chapter_from=1,
        source_chapter=1,
        confidence=0.9,
        is_active=1,
    )
    session.add(f)
    session.commit()

    out = arbitrate_fact(
        session, novel_id=n.id, novel_version_id=None, entity_id=e.id, fact_type="status"
    )
    assert out.decision.decision == "keep"
    assert out.superseded_ids == []


def test_strong_gap_supersedes(session):
    n, e, a, b = _seed(session, conf_a=0.4, conf_b=0.9, ch_a=1, ch_b=2)
    out = arbitrate_fact(
        session, novel_id=n.id, novel_version_id=None, entity_id=e.id, fact_type="status"
    )
    assert out.decision.decision == "supersede"
    assert out.superseded_ids


def test_small_gap_warns(session):
    n, e, a, b = _seed(session, conf_a=0.5, conf_b=0.55, ch_a=1, ch_b=2)
    out = arbitrate_fact(
        session, novel_id=n.id, novel_version_id=None, entity_id=e.id, fact_type="status"
    )
    assert out.decision.decision == "warn"
    assert out.superseded_ids == []
