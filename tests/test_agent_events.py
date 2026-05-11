"""Tests for app/services/agents/events.py (Phase 0 §4.1)."""

from __future__ import annotations

from typing import Literal

import pytest
from pydantic import BaseModel
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.trace import set_trace_id
from app.models.novel import AgentEvent, Novel
from app.services.agents import events as events_module
from app.services.agents.events import (
    emit_agent_event,
    get_event_payload_schema,
    get_fallback_counter,
    register_event_payload,
    reset_fallback_counters,
)


@pytest.fixture
def novel_id() -> int:
    """Provision a Novel row so foreign keys do not block the inserts."""
    db = SessionLocal()
    try:
        novel = Novel(uuid="agent-events-test", title="agent events fixture")
        db.add(novel)
        db.commit()
        db.refresh(novel)
        novel_id = novel.id
    finally:
        db.close()
    yield novel_id
    db = SessionLocal()
    try:
        db.query(AgentEvent).filter(AgentEvent.novel_id == novel_id).delete(synchronize_session=False)
        db.query(Novel).filter(Novel.id == novel_id).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _clean_counters():
    reset_fallback_counters()
    yield
    reset_fallback_counters()


@pytest.fixture(autouse=True)
def _clean_payload_registry():
    snapshot = dict(events_module._EVENT_PAYLOAD_REGISTRY)
    yield
    events_module._EVENT_PAYLOAD_REGISTRY.clear()
    events_module._EVENT_PAYLOAD_REGISTRY.update(snapshot)


def _count_events(novel_id: int, **filters) -> int:
    db = SessionLocal()
    try:
        stmt = select(AgentEvent).where(AgentEvent.novel_id == novel_id)
        for column, value in filters.items():
            stmt = stmt.where(getattr(AgentEvent, column) == value)
        return len(db.execute(stmt).scalars().all())
    finally:
        db.close()


def test_happy_path_persists_row_and_bumps_counter(novel_id: int) -> None:
    emit_agent_event(
        agent_name="consistency_check",
        event_type="check",
        novel_id=novel_id,
        chapter_num=3,
        verdict="pass",
        input_tokens=120,
        output_tokens=80,
        payload={"blocker_count": 0},
    )

    assert _count_events(novel_id, agent_name="consistency_check", event_type="check") == 1
    assert get_fallback_counter("consistency_check", "check", "pass") == 1

    db = SessionLocal()
    try:
        row = (
            db.execute(
                select(AgentEvent).where(AgentEvent.novel_id == novel_id)
            )
            .scalars()
            .one()
        )
    finally:
        db.close()
    assert row.input_tokens == 120
    assert row.output_tokens == 80
    assert row.payload == {"blocker_count": 0}


def test_trace_id_inherits_from_context_when_omitted(novel_id: int) -> None:
    set_trace_id("trace-from-ctx-001")
    try:
        emit_agent_event(
            agent_name="writer",
            event_type="invoke",
            novel_id=novel_id,
        )
    finally:
        set_trace_id(None)

    db = SessionLocal()
    try:
        row = (
            db.execute(
                select(AgentEvent).where(AgentEvent.novel_id == novel_id)
            )
            .scalars()
            .one()
        )
    finally:
        db.close()
    assert row.trace_id == "trace-from-ctx-001"


def test_explicit_trace_id_overrides_context(novel_id: int) -> None:
    set_trace_id("ctx-trace")
    try:
        emit_agent_event(
            agent_name="writer",
            event_type="invoke",
            novel_id=novel_id,
            trace_id="explicit-trace",
        )
    finally:
        set_trace_id(None)

    db = SessionLocal()
    try:
        row = (
            db.execute(select(AgentEvent).where(AgentEvent.novel_id == novel_id))
            .scalars()
            .one()
        )
    finally:
        db.close()
    assert row.trace_id == "explicit-trace"


def test_db_failure_is_swallowed_and_does_not_raise(monkeypatch, caplog) -> None:
    """主路径绝不能因为 emit 失败抛异常（§4.1 swallow 约束）。"""

    class _BoomSession:
        def add(self, *_args, **_kwargs):
            raise RuntimeError("simulated db failure")

        def commit(self):  # pragma: no cover - won't be reached
            raise AssertionError("commit should not run after add failure")

        def rollback(self):
            return None

        def close(self):
            return None

        def flush(self):  # pragma: no cover
            raise AssertionError("flush should not run")

    monkeypatch.setattr(events_module, "SessionLocal", lambda: _BoomSession())

    with caplog.at_level("ERROR"):
        emit_agent_event(
            agent_name="writer",
            event_type="invoke",
            novel_id=999_999,
        )

    assert get_fallback_counter("writer", "invoke", "") == 0
    assert any("emit_agent_event failed to persist" in rec.message for rec in caplog.records)


def test_missing_required_fields_are_logged_and_swallowed(novel_id: int, caplog) -> None:
    with caplog.at_level("WARNING"):
        emit_agent_event(
            agent_name="",
            event_type="check",
            novel_id=novel_id,
        )
    assert _count_events(novel_id) == 0
    assert any("without agent_name" in rec.message for rec in caplog.records)


def test_payload_schema_violation_emits_meta_event(novel_id: int) -> None:
    class StrictPayload(BaseModel):
        kind: Literal["a", "b"]
        score: float

    register_event_payload(("strict_agent", "judge"))(StrictPayload)
    assert get_event_payload_schema("strict_agent", "judge") is StrictPayload

    emit_agent_event(
        agent_name="strict_agent",
        event_type="judge",
        novel_id=novel_id,
        verdict="warn",
        payload={"kind": "not-allowed", "score": "x"},
    )

    db = SessionLocal()
    try:
        rows = (
            db.execute(
                select(AgentEvent)
                .where(AgentEvent.novel_id == novel_id)
                .order_by(AgentEvent.id.asc())
            )
            .scalars()
            .all()
        )
    finally:
        db.close()

    assert len(rows) == 2
    main, meta = rows
    assert main.agent_name == "strict_agent"
    assert main.payload == {"kind": "not-allowed", "score": "x"}
    assert meta.agent_name == "agent_events_meta"
    assert meta.event_type == "schema_violation"
    assert meta.error_code == "payload_schema_violation"
    assert meta.payload["source_agent"] == "strict_agent"
    assert meta.payload["source_event_type"] == "judge"
    assert "ValidationError" in meta.payload["schema_error"]

    assert get_fallback_counter("strict_agent", "judge", "warn") == 1
    assert get_fallback_counter("agent_events_meta", "schema_violation", "warn") == 1


def test_payload_schema_pass_normalizes_via_pydantic(novel_id: int) -> None:
    class GoodPayload(BaseModel):
        attempt: int
        note: str = ""

    register_event_payload(("ok_agent", "check"))(GoodPayload)

    emit_agent_event(
        agent_name="ok_agent",
        event_type="check",
        novel_id=novel_id,
        payload={"attempt": 2, "note": "ok", "extra_dropped": "x"},
    )

    db = SessionLocal()
    try:
        row = (
            db.execute(select(AgentEvent).where(AgentEvent.novel_id == novel_id))
            .scalars()
            .one()
        )
    finally:
        db.close()

    assert row.payload == {"attempt": 2, "note": "ok"}


def test_unknown_error_category_is_logged_but_not_blocking(novel_id: int, caplog) -> None:
    with caplog.at_level("WARNING"):
        emit_agent_event(
            agent_name="writer",
            event_type="failure",
            novel_id=novel_id,
            verdict="fail",
            error_category="unknown-category",
        )

    assert _count_events(novel_id, agent_name="writer", event_type="failure") == 1
    assert any("unknown error_category" in rec.message for rec in caplog.records)
