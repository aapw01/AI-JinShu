"""LLM cost auto-write into agent_events.payload (D 闭环)."""

from __future__ import annotations

import pytest
from types import SimpleNamespace

from app.core import feature_flags
from app.core.database import SessionLocal
from app.core.llm_usage import (
    pop_last_llm_call,
    record_usage_from_response,
)
from app.models.novel import AgentEvent, Novel
from app.services.agents.events import emit_agent_event


@pytest.fixture(autouse=True)
def _clear():
    feature_flags.invalidate_flags_cache()
    db = SessionLocal()
    try:
        db.query(AgentEvent).delete()
        db.commit()
    finally:
        db.close()
    pop_last_llm_call()  # ensure clean
    yield
    pop_last_llm_call()
    feature_flags.invalidate_flags_cache()


@pytest.fixture
def session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _fake_llm_response(in_t: int, out_t: int):
    return SimpleNamespace(
        usage_metadata={
            "input_tokens": in_t,
            "output_tokens": out_t,
            "total_tokens": in_t + out_t,
        }
    )


def test_record_usage_sets_last_call_with_cost():
    record_usage_from_response(_fake_llm_response(1000, 500), stage="llm.openai.gpt-4o")
    calls = pop_last_llm_call()
    assert len(calls) == 1
    last = calls[0]
    assert last["model"] == "gpt-4o"
    assert last["provider"] == "openai"
    assert last["input_tokens"] == 1000
    assert last["output_tokens"] == 500
    assert last["cost_usd"] > 0  # priced model


def test_record_usage_unknown_model_falls_back_to_unknown_price():
    record_usage_from_response(
        _fake_llm_response(2000, 1000), stage="llm.openai.unknown-model"
    )
    calls = pop_last_llm_call()
    assert len(calls) == 1
    assert calls[0]["model"] == "unknown-model"
    assert calls[0]["cost_usd"] > 0  # fallback price still > 0


def test_record_usage_no_stage_no_cost():
    record_usage_from_response(_fake_llm_response(1000, 500))
    calls = pop_last_llm_call()
    assert len(calls) == 1
    assert calls[0]["model"] is None
    assert calls[0]["cost_usd"] == 0.0


def test_multiple_record_usage_accumulates_into_list():
    """同一 emit 之前发生 N 次 LLM 调用（重试/级联），全部都要被记下来。"""
    record_usage_from_response(_fake_llm_response(100, 50), stage="llm.openai.gpt-4o")
    record_usage_from_response(
        _fake_llm_response(200, 80), stage="llm.openai.gpt-4o-mini"
    )
    record_usage_from_response(
        _fake_llm_response(300, 100), stage="llm.anthropic.claude-3.5-sonnet"
    )
    calls = pop_last_llm_call()
    assert len(calls) == 3
    assert calls[0]["model"] == "gpt-4o"
    assert calls[1]["model"] == "gpt-4o-mini"
    assert calls[2]["model"] == "claude-3.5-sonnet"
    # pop 后桶被清空
    assert pop_last_llm_call() == []


def test_emit_agent_event_consumes_last_call(session):
    n = Novel(title="t")
    session.add(n)
    session.commit()

    record_usage_from_response(_fake_llm_response(800, 200), stage="llm.openai.gpt-4o-mini")
    emit_agent_event(
        agent_name="outline_auditor",
        event_type="audit",
        novel_id=n.id,
        verdict="pass",
    )
    row = session.query(AgentEvent).filter_by(agent_name="outline_auditor").one()
    assert row.input_tokens == 800
    assert row.output_tokens == 200
    payload = row.payload or {}
    assert payload.get("model") == "gpt-4o-mini"
    assert payload.get("cost_usd") and payload["cost_usd"] > 0


def test_emit_agent_event_explicit_args_win(session):
    n = Novel(title="t")
    session.add(n)
    session.commit()

    record_usage_from_response(_fake_llm_response(800, 200), stage="llm.openai.gpt-4o")
    emit_agent_event(
        agent_name="patch_writer",
        event_type="rewrite",
        novel_id=n.id,
        verdict="pass",
        input_tokens=999,
        output_tokens=111,
        payload={"model": "explicit-model", "cost_usd": 0.01},
    )
    row = session.query(AgentEvent).filter_by(agent_name="patch_writer").one()
    assert row.input_tokens == 999
    assert row.output_tokens == 111
    assert row.payload["model"] == "explicit-model"
    assert row.payload["cost_usd"] == 0.01


def test_emit_clears_last_call_state(session):
    n = Novel(title="t")
    session.add(n)
    session.commit()
    record_usage_from_response(_fake_llm_response(800, 200), stage="llm.openai.gpt-4o")
    emit_agent_event(
        agent_name="x",
        event_type="y",
        novel_id=n.id,
        verdict="pass",
    )
    assert pop_last_llm_call() == []


def test_emit_accumulates_retry_cost(session):
    """retry/级联场景：3 次 LLM 调用 → cost_usd 是三次之和；payload.llm_calls 详情齐。"""
    n = Novel(title="t")
    session.add(n)
    session.commit()
    record_usage_from_response(
        _fake_llm_response(100, 50), stage="llm.openai.gpt-4o"
    )
    record_usage_from_response(
        _fake_llm_response(200, 80), stage="llm.openai.gpt-4o-mini"
    )
    record_usage_from_response(
        _fake_llm_response(300, 100), stage="llm.anthropic.claude-3.5-sonnet"
    )
    emit_agent_event(
        agent_name="patch_writer",
        event_type="rewrite",
        novel_id=n.id,
        verdict="pass",
    )
    row = session.query(AgentEvent).filter_by(agent_name="patch_writer").one()
    payload = row.payload or {}
    # cost_usd 应该 = 三次调用 cost 之和（绝不能只算最后一次）
    assert "llm_calls" in payload
    assert len(payload["llm_calls"]) == 3
    summed = sum(c["cost_usd"] for c in payload["llm_calls"])
    assert abs(payload["cost_usd"] - summed) < 1e-9
    # input/output token 也累加
    total_in = sum(c["input_tokens"] for c in payload["llm_calls"])
    total_out = sum(c["output_tokens"] for c in payload["llm_calls"])
    assert row.input_tokens == total_in
    assert row.output_tokens == total_out


def test_scheduler_budget_hard_stop_skips_dispatch(session):
    feature_flags.set_flag(
        "cost.budget_enforcement",
        enabled=True,
        rollout_pct=100,
        changed_by="t",
        reason="enable",
    )
    n = Novel(title="t", config={"cost_budget": {"usd": 0.001}})
    session.add(n)
    session.commit()
    # seed a $0.05 cost event → over budget
    session.add(
        AgentEvent(
            agent_name="x",
            event_type="y",
            novel_id=n.id,
            verdict="pass",
            payload={"cost_usd": 0.05},
        )
    )
    session.commit()

    from app.services.scheduler.scheduler_service import _is_budget_hard_stopped

    fake_task = SimpleNamespace(
        task_type="generation",
        resource_type="novel",
        resource_id=n.id,
    )
    assert _is_budget_hard_stopped(session, task=fake_task) is True


def test_cost_metric_accumulates_real_amount(session):
    """``agent_token_cost_total`` 必须按真实 USD 累加，不是简单 +1。"""
    from app.core.metrics import _FALLBACK_VALUES

    n = Novel(title="t")
    session.add(n)
    session.commit()
    # 清空 fallback 桶
    keys_to_clear = [k for k in _FALLBACK_VALUES if "agent_token_cost_total" in k[0]]
    for k in keys_to_clear:
        del _FALLBACK_VALUES[k]

    # 两次 LLM 调用：cost 应该相加而非各 +1
    record_usage_from_response(
        _fake_llm_response(1000, 500), stage="llm.openai.gpt-4o"
    )
    cost1 = pop_last_llm_call()[0]["cost_usd"]
    record_usage_from_response(
        _fake_llm_response(1000, 500), stage="llm.openai.gpt-4o"
    )
    cost2 = pop_last_llm_call()[0]["cost_usd"]
    # 注意：pop 把桶清了，所以再 record 一遍模拟 emit 前累计
    record_usage_from_response(
        _fake_llm_response(1000, 500), stage="llm.openai.gpt-4o"
    )
    record_usage_from_response(
        _fake_llm_response(1000, 500), stage="llm.openai.gpt-4o"
    )
    emit_agent_event(
        agent_name="cost_smoke",
        event_type="probe",
        novel_id=n.id,
        verdict="pass",
    )
    expected_total = cost1 + cost2  # 两次相同调用
    found = 0.0
    for (name, labels), v in _FALLBACK_VALUES.items():
        if name == "agent_token_cost_total" and any(
            "cost_smoke" in str(x) for x in labels
        ):
            found += float(v)
    # 由 emit_agent_event 内部按调用累加，应该和上面相同 cost 之和一致
    assert abs(found - expected_total) < 1e-6, (
        f"cost metric must accumulate real USD, got {found} expected {expected_total}"
    )


def test_budget_guardrail_metric_emits_on_check_failure(session, monkeypatch):
    """budget check 异常时 ``budget_guardrail_error_total`` 必须升。"""
    from app.core import metrics as core_metrics

    feature_flags.set_flag(
        "cost.budget_enforcement",
        enabled=True,
        rollout_pct=100,
        changed_by="t",
        reason="enable",
    )
    n = Novel(title="t", config={"cost_budget": {"usd": 100.0}})
    session.add(n)
    session.commit()

    # 让 check_budget 抛
    def _boom(*a, **kw):
        raise RuntimeError("simulated db down")

    monkeypatch.setattr("app.services.cost.budget.check_budget", _boom)

    keys_before = {
        k: v
        for k, v in core_metrics._FALLBACK_VALUES.items()
        if k[0] == "budget_guardrail_error_total"
    }

    from app.services.scheduler.scheduler_service import _is_budget_hard_stopped

    fake_task = SimpleNamespace(
        task_type="generation",
        resource_type="novel",
        resource_id=n.id,
    )
    # 异常路径不应 raise；返回 False（不阻塞 dispatch）
    assert _is_budget_hard_stopped(session, task=fake_task) is False

    keys_after = {
        k: v
        for k, v in core_metrics._FALLBACK_VALUES.items()
        if k[0] == "budget_guardrail_error_total"
    }
    # 一定有一个新的 (name, labels) entry 计数 > 之前
    assert any(
        keys_after.get(k, 0) > keys_before.get(k, 0) for k in keys_after
    ), f"guardrail metric should fire, before={keys_before} after={keys_after}"


def test_scheduler_budget_check_off_when_flag_disabled(session):
    n = Novel(title="t", config={"cost_budget": {"usd": 0.001}})
    session.add(n)
    session.commit()
    session.add(
        AgentEvent(
            agent_name="x",
            event_type="y",
            novel_id=n.id,
            verdict="pass",
            payload={"cost_usd": 0.05},
        )
    )
    session.commit()

    from app.services.scheduler.scheduler_service import _is_budget_hard_stopped

    fake_task = SimpleNamespace(
        task_type="generation",
        resource_type="novel",
        resource_id=n.id,
    )
    assert _is_budget_hard_stopped(session, task=fake_task) is False
