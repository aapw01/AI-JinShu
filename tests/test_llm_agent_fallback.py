"""run_llm_agent endpoint fallback chain (§F)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from pydantic import BaseModel, ConfigDict

from app.core import llm_circuit_breaker as cb
from app.core.database import SessionLocal
from app.models.novel import AgentEvent, Novel
from app.services.agents.events import reset_fallback_counters
from app.services.agents.llm_agent import run_llm_agent


class _ToyOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int


@pytest.fixture(autouse=True)
def _clear():
    cb.reset_breaker()
    reset_fallback_counters()
    db = SessionLocal()
    try:
        db.query(AgentEvent).delete()
        db.commit()
    finally:
        db.close()
    yield
    cb.reset_breaker()


@pytest.fixture
def novel_id():
    db = SessionLocal()
    try:
        n = Novel(title="t")
        db.add(n)
        db.commit()
        return int(n.id)
    finally:
        db.close()


class _AlwaysFailLLM:
    def with_structured_output(self, *_: Any, **__: Any):
        return self

    def invoke(self, *_: Any, **__: Any):
        raise RuntimeError("boom")


def test_endpoint_attempts_recorded_on_full_failure(novel_id):
    cb.configure_stage(
        "test_stage",
        cb.CircuitConfig(consecutive_failures_to_open=1, cooldown_seconds=600),
    )

    with patch(
        "app.services.agents.llm_agent.get_llm", lambda: _AlwaysFailLLM()
    ), patch(
        "app.services.agents.llm_agent.render_prompt", lambda *_, **__: "irrelevant"
    ):
        result = run_llm_agent(
            agent_name="test_agent",
            event_type="x",
            template="x",
            template_kwargs={},
            schema=_ToyOut,
            novel_id=novel_id,
            stage="test_stage",
        )
    assert result is None

    db = SessionLocal()
    try:
        evt = (
            db.query(AgentEvent)
            .filter_by(agent_name="test_agent", event_type="x", verdict="fail")
            .one()
        )
        payload = evt.payload or {}
        assert payload.get("endpoint_attempts") == [
            "primary",
            "fallback_a",
            "fallback_b",
        ]
    finally:
        db.close()
    # primary failure 应使其 open（threshold=1）
    assert cb.get_breaker_state("test_stage", "primary") == "open"


class _ToggleLLM:
    """First call fails, second call succeeds — verifies endpoint-level retry."""

    def __init__(self):
        self.calls = 0

    def with_structured_output(self, *_: Any, **__: Any):
        return self

    def invoke(self, *_: Any, **__: Any):
        self.calls += 1
        if self.calls <= 3:
            raise RuntimeError("boom")
        return {"value": 42}


def test_endpoint_succeeds_on_fallback_a(novel_id):
    cb.configure_stage(
        "test_stage_2",
        cb.CircuitConfig(consecutive_failures_to_open=1, cooldown_seconds=600),
    )
    toggle = _ToggleLLM()
    with patch(
        "app.services.agents.llm_agent.get_llm", lambda: toggle
    ), patch(
        "app.services.agents.llm_agent.render_prompt", lambda *_, **__: "irrelevant"
    ):
        result = run_llm_agent(
            agent_name="agent_b",
            event_type="x",
            template="x",
            template_kwargs={},
            schema=_ToyOut,
            novel_id=novel_id,
            stage="test_stage_2",
        )
    assert result is not None and result.value == 42
    # fallback_a 成功，primary 失败 → primary open，fallback_a closed
    assert cb.get_breaker_state("test_stage_2", "primary") == "open"
    assert cb.get_breaker_state("test_stage_2", "fallback_a") == "closed"
