"""Test that named Prometheus metrics fire from agents (C 命名 metric)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core import feature_flags, metrics
from app.core.database import SessionLocal
from app.models.novel import (
    AgentEvent,
    Novel,
    NovelVersion,
    StoryEntity,
    StoryFact,
    StoryForeshadow,
)
from app.services.memory import fact_arbitrator, foreshadow_lifecycle


@pytest.fixture(autouse=True)
def _clear():
    metrics.reset_metrics()
    feature_flags.invalidate_flags_cache()
    db = SessionLocal()
    try:
        for cls in (AgentEvent, StoryFact, StoryEntity, StoryForeshadow):
            db.query(cls).delete()
        db.commit()
    finally:
        db.close()
    yield
    metrics.reset_metrics()
    feature_flags.invalidate_flags_cache()


@pytest.fixture
def session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _make_novel(session) -> tuple[Novel, NovelVersion]:
    n = Novel(title="t")
    session.add(n)
    session.commit()
    nv = NovelVersion(novel_id=n.id, version_no=1, status="draft")
    session.add(nv)
    session.commit()
    return n, nv


def test_outline_revise_metric_fires():
    feature_flags.set_flag(
        "consistency.blocker_hard_gate",
        enabled=True,
        rollout_pct=100,
        changed_by="t",
        reason="enable",
    )
    from app.services.generation.nodes.outline_revise import node_outline_revise

    blocker = SimpleNamespace(category="character_existence", message="x")
    state = {
        "novel_id": 1,
        "current_chapter": 3,
        "outline": {"title": "t"},
        "consistency_report": SimpleNamespace(blockers=[blocker]),
    }
    node_outline_revise(state)
    assert metrics.get_metric_value(
        "consistency_blocker_total", {"category": "character_existence"}
    ) == 1


def test_foreshadow_metric_fires(session):
    feature_flags.set_flag(
        "consistency.foreshadow_lifecycle_v1",
        enabled=True,
        rollout_pct=100,
        changed_by="t",
        reason="enable",
    )
    n, _nv = _make_novel(session)
    fs = StoryForeshadow(
        foreshadow_id="f1",
        novel_id=n.id,
        novel_version_id=None,
        title="t",
        planted_chapter=1,
        plant_chapter=1,
        lifecycle_state="planned",
    )
    session.add(fs)
    session.commit()
    foreshadow_lifecycle.advance_foreshadows(
        session, novel_id=n.id, novel_version_id=None, current_chapter=1
    )
    assert metrics.get_metric_value(
        "foreshadow_state_transition_total", {"from": "planned", "to": "planted"}
    ) == 1


def test_fact_arbitration_metric_fires(session):
    n, nv = _make_novel(session)
    e = StoryEntity(novel_id=n.id, novel_version_id=nv.id, entity_type="character", name="X")
    session.add(e)
    session.commit()

    f1 = StoryFact(
        novel_id=n.id,
        novel_version_id=nv.id,
        entity_id=e.id,
        fact_type="trait",
        value_json={"v": "old"},
        chapter_from=1,
        confidence=0.4,
        is_active=1,
        source_chapter=1,
    )
    f2 = StoryFact(
        novel_id=n.id,
        novel_version_id=nv.id,
        entity_id=e.id,
        fact_type="trait",
        value_json={"v": "new"},
        chapter_from=2,
        confidence=0.9,
        is_active=1,
        source_chapter=2,
    )
    session.add_all([f1, f2])
    session.commit()
    fact_arbitrator.arbitrate_fact(
        session,
        novel_id=n.id,
        novel_version_id=nv.id,
        entity_id=e.id,
        fact_type="trait",
    )
    assert metrics.get_metric_value("fact_arbitration_total", {"decision": "supersede"}) >= 1


def test_flag_toggle_metric():
    feature_flags.set_flag(
        "consistency.alias_registry_v1",
        enabled=True,
        rollout_pct=10,
        changed_by="t",
        reason="enable for test",
    )
    feature_flags.set_flag(
        "consistency.alias_registry_v1",
        enabled=False,
        changed_by="t",
        reason="disable for test",
    )
    on = metrics.get_metric_value(
        "flag_toggle_total",
        {"flag": "consistency.alias_registry_v1", "direction": "on"},
    )
    off = metrics.get_metric_value(
        "flag_toggle_total",
        {"flag": "consistency.alias_registry_v1", "direction": "off"},
    )
    assert on >= 1 and off >= 1


def test_cv_metric_fires(session):
    from app.services.cv.promotion_engine import evaluate_flag

    evaluate_flag("memory.hybrid_search", db=session)
    assert (
        metrics.get_metric_value(
            "cv_promotion_decision_total",
            {"flag": "memory.hybrid_search", "decision": "hold"},
        )
        + metrics.get_metric_value(
            "cv_promotion_decision_total",
            {"flag": "memory.hybrid_search", "decision": "promote"},
        )
        >= 1
    )
