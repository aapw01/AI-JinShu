"""#11 self-heal runner integration test."""

from __future__ import annotations

import pytest

from app.core import feature_flags
from app.core.database import SessionLocal
from app.models.novel import (
    FactExtractionFailure,
    Novel,
    StoryEntity,
    StoryFact,
)
from app.services.agents import extractor_self_heal
from app.tasks import extractor_recovery


@pytest.fixture(autouse=True)
def _clear():
    feature_flags.invalidate_flags_cache()
    db = SessionLocal()
    try:
        db.query(StoryFact).delete()
        db.query(StoryEntity).delete()
        db.query(FactExtractionFailure).delete()
        db.commit()
    finally:
        db.close()
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


def test_install_wires_runner():
    extractor_self_heal.install()
    assert extractor_recovery._runner_impl is extractor_self_heal._self_heal_runner


def test_self_heal_skips_when_no_chapter_text(session):
    n = Novel(title="t")
    session.add(n)
    session.commit()
    failure = FactExtractionFailure(
        novel_id=n.id,
        chapter_num=3,
        failure_kind="parse_error",
        error_payload={},
        retry_count=0,
        status="pending",
    )
    session.add(failure)
    session.commit()

    feature_flags.set_flag(
        "extractor.self_heal",
        enabled=True,
        rollout_pct=100,
        changed_by="t",
        reason="enable",
    )
    extractor_self_heal.install()
    out = extractor_recovery.run_recovery_once()
    # 没有 chapter_text → False → 标记 failed/escalated 取决于 retry_count
    assert out.get("failed", 0) >= 1


def test_self_heal_persists_facts_when_runner_recovers(session, monkeypatch):
    n = Novel(title="t")
    session.add(n)
    session.commit()
    failure = FactExtractionFailure(
        novel_id=n.id,
        chapter_num=4,
        failure_kind="llm_error",
        error_payload={
            "chapter_text": "夜里，李寻欢饮酒赋诗。",
            "language": "zh",
        },
        retry_count=0,
        status="pending",
    )
    session.add(failure)
    session.commit()

    feature_flags.set_flag(
        "extractor.self_heal",
        enabled=True,
        rollout_pct=100,
        changed_by="t",
        reason="enable",
    )

    class _FakeAgent:
        def run(self, **_kwargs):
            return {
                "facts": [
                    {
                        "entity_name": "李寻欢",
                        "entity_type": "character",
                        "fact_type": "habit",
                        "value": "夜饮赋诗",
                        "chapter_from": 4,
                        "confidence": 0.7,
                    }
                ],
                "events": [],
                "entities": [],
            }

    monkeypatch.setattr(
        "app.services.generation.agents.FactExtractorAgent", _FakeAgent
    )
    extractor_self_heal.install()
    out = extractor_recovery.run_recovery_once()
    assert out.get("recovered", 0) >= 1
    fact_count = session.query(StoryFact).filter_by(novel_id=n.id).count()
    assert fact_count == 1
    fact = session.query(StoryFact).filter_by(novel_id=n.id).one()
    assert fact.fact_type == "habit"
    assert fact.confidence == 0.7
    assert fact.is_active == 1
