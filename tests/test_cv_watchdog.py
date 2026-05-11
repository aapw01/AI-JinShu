"""CV watchdog tests (§4.7)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def _now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)

import pytest

from app.core import feature_flags
from app.core.database import SessionLocal
from app.models.novel import AgentEvent, CVPromotionState, Novel
from app.services.cv import promotion_engine
from app.tasks import cv_watchdog


@pytest.fixture(autouse=True)
def _clear():
    feature_flags.invalidate_flags_cache()
    promotion_engine.invalidate_policy_cache()
    db = SessionLocal()
    try:
        db.query(CVPromotionState).delete()
        db.query(AgentEvent).delete()
        db.commit()
    finally:
        db.close()
    yield
    feature_flags.invalidate_flags_cache()
    promotion_engine.invalidate_policy_cache()
    db = SessionLocal()
    try:
        db.query(CVPromotionState).delete()
        db.query(AgentEvent).delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture
def session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _seed_events(
    session,
    *,
    agent_name: str,
    n_pass: int,
    n_fail: int,
    minutes_ago: int = 1,
    novel_id: int | None = None,
):
    if novel_id is None:
        n = Novel(title="t")
        session.add(n)
        session.commit()
        novel_id = n.id
    when = _now_utc() - timedelta(minutes=minutes_ago)
    for _ in range(n_pass):
        session.add(
            AgentEvent(
                novel_id=novel_id,
                agent_name=agent_name,
                event_type="run",
                verdict="pass",
                duration_ms=100,
                created_at=when,
            )
        )
    for _ in range(n_fail):
        session.add(
            AgentEvent(
                novel_id=novel_id,
                agent_name=agent_name,
                event_type="run",
                verdict="fail",
                duration_ms=200,
                created_at=when,
            )
        )
    session.commit()


def test_initial_evaluation_creates_baseline_state(session):
    decision = promotion_engine.evaluate_flag("memory.hybrid_search", db=session)
    assert decision.previous_phase == "baseline"
    assert decision.verdict in ("promote", "hold")
    state = session.query(CVPromotionState).filter_by(flag_name="memory.hybrid_search").one()
    assert state.phase in ("baseline", "canary_10")


def test_promotes_when_healthy(session):
    _seed_events(session, agent_name="hybrid_search", n_pass=30, n_fail=0)
    decision = promotion_engine.evaluate_flag("memory.hybrid_search", db=session)
    assert decision.verdict == "promote"
    assert decision.next_phase == "canary_10"
    assert decision.next_canary_pct == 10
    # also pushed to feature flag
    state = feature_flags.get_flag_state("memory.hybrid_search")
    assert state["rollout_pct"] == 10
    assert state["enabled"] is True


def test_holds_when_too_few_samples(session):
    _seed_events(session, agent_name="hybrid_search", n_pass=2, n_fail=0)
    decision = promotion_engine.evaluate_flag("memory.hybrid_search", db=session)
    assert decision.verdict == "hold"
    assert decision.next_phase == "baseline"


def test_rolls_back_on_high_failure(session):
    # Push state to canary_10 first
    _seed_events(session, agent_name="hybrid_search", n_pass=30, n_fail=0)
    promotion_engine.evaluate_flag("memory.hybrid_search", db=session)
    # Then inject high failures
    _seed_events(session, agent_name="hybrid_search", n_pass=10, n_fail=20)
    decision = promotion_engine.evaluate_flag("memory.hybrid_search", db=session)
    assert decision.verdict == "rollback"
    assert decision.next_phase == "baseline"
    assert decision.next_canary_pct == 0


def test_watchdog_iterates_registry():
    out = cv_watchdog.run_watchdog_once()
    flag_names = {item["flag"] for item in out["evaluated"]}
    assert "consistency.blocker_hard_gate" in flag_names
    # registry has 15 flags after cost.budget_enforcement was added
    assert len(out["evaluated"]) >= 14


def test_full_phase_does_not_re_promote(session):
    state = CVPromotionState(
        flag_name="extractor.self_heal", phase="stable", current_canary_pct=100
    )
    session.add(state)
    session.commit()
    decision = promotion_engine.evaluate_flag("extractor.self_heal", db=session)
    assert decision.verdict in ("hold", "rollback")
    assert decision.next_phase in ("stable", "baseline")
