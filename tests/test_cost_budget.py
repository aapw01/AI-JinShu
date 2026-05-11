"""Cost governance tests (§11)."""

from __future__ import annotations

import pytest

from app.core.database import SessionLocal
from app.models.novel import AgentEvent, Novel
from app.services.cost import budget


@pytest.fixture(autouse=True)
def _clear_cache():
    budget.invalidate_price_cache()
    yield
    budget.invalidate_price_cache()


@pytest.fixture
def session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_compute_cost_known_model():
    cost = budget.compute_cost("gpt-4o-mini", input_tokens=1000, output_tokens=500)
    expected = 1.0 * 0.00015 + 0.5 * 0.00060
    assert cost == pytest.approx(expected, rel=1e-3)


def test_compute_cost_unknown_model_uses_fallback():
    cost = budget.compute_cost("does-not-exist", input_tokens=1000, output_tokens=0)
    assert cost > 0


def test_compute_cost_zero_tokens():
    assert budget.compute_cost(None, 0, 0) == 0.0


def _make_novel(session) -> Novel:
    n = Novel(title="t", config={"cost_budget": {"usd": 0.01}})
    session.add(n)
    session.commit()
    return n


def test_check_budget_no_budget_when_unset(session):
    n = Novel(title="no-bud", config={})
    session.add(n)
    session.commit()
    verdict = budget.check_budget(n.id, db=session)
    assert verdict.status == "no_budget"


def test_check_budget_ok_warn_hardstop(session):
    n = _make_novel(session)
    # spend below 80% of $0.01 -> ok
    session.add(
        AgentEvent(
            novel_id=n.id,
            agent_name="writer",
            event_type="generate",
            payload={"cost_usd": 0.001},
        )
    )
    session.commit()
    assert budget.check_budget(n.id, db=session).status == "ok"

    # add to push burn into warn band (>= 80%)
    session.add(
        AgentEvent(
            novel_id=n.id,
            agent_name="writer",
            event_type="generate",
            payload={"cost_usd": 0.008},
        )
    )
    session.commit()
    assert budget.check_budget(n.id, db=session).status == "warn"

    # push >100% -> hard_stop
    session.add(
        AgentEvent(
            novel_id=n.id,
            agent_name="writer",
            event_type="generate",
            payload={"cost_usd": 0.005},
        )
    )
    session.commit()
    assert budget.check_budget(n.id, db=session).status == "hard_stop"
