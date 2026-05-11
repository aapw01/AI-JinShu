"""SLI 计算 + per-flag policy 加载（§E + §I）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core import feature_flags
from app.core.database import SessionLocal
from app.models.novel import AgentEvent, CVPromotionState, Novel
from app.services.cv import promotion_engine
from app.services.cv.sli import compute_sli


@pytest.fixture(autouse=True)
def _clear():
    feature_flags.invalidate_flags_cache()
    promotion_engine.invalidate_policy_cache()
    db = SessionLocal()
    try:
        db.query(AgentEvent).delete()
        db.query(CVPromotionState).delete()
        db.commit()
    finally:
        db.close()
    yield
    feature_flags.invalidate_flags_cache()
    promotion_engine.invalidate_policy_cache()


@pytest.fixture
def session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


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


def _seed(db, *, novel_id: int, n: int, fail: int, agent: str = "a"):
    now = datetime.now(UTC).replace(tzinfo=None)
    for i in range(n):
        db.add(
            AgentEvent(
                agent_name=agent,
                event_type="x",
                novel_id=novel_id,
                verdict="fail" if i < fail else "pass",
                duration_ms=120,
                created_at=now,
                payload={},
            )
        )
    db.commit()


def test_compute_sli_basic(session, novel_id):
    _seed(session, novel_id=novel_id, n=100, fail=10)
    out = compute_sli(session, observation_minutes=15, related_agents=["a"])
    assert out.samples == 100
    assert out.failure_rate == 0.1
    assert out.p95_latency_ms == 120
    assert out.fail_count == 10


def test_compute_sli_burn_rate(session, novel_id):
    """SLO=0.95 → budget=5；fail=10 → burn_rate = 10 / 5 = 2.0."""
    _seed(session, novel_id=novel_id, n=100, fail=10)
    out = compute_sli(
        session, observation_minutes=120, related_agents=["a"], slo_target=0.95
    )
    assert abs(out.error_budget_burn_rate_1h - 2.0) < 0.01


def test_compute_sli_no_data(session):
    out = compute_sli(session, observation_minutes=15)
    assert out.samples == 0
    assert out.failure_rate == 0.0
    assert out.error_budget_burn_rate_1h == 0.0


def test_compute_sli_filters_by_agent(session, novel_id):
    _seed(session, novel_id=novel_id, n=50, fail=5, agent="a")
    _seed(session, novel_id=novel_id, n=50, fail=20, agent="b")
    only_a = compute_sli(session, observation_minutes=15, related_agents=["a"])
    only_b = compute_sli(session, observation_minutes=15, related_agents=["b"])
    assert only_a.samples == 50 and only_a.fail_count == 5
    assert only_b.samples == 50 and only_b.fail_count == 20


def test_compute_sli_old_data_excluded(session, novel_id):
    """超过 observation_minutes 的 events 不计入。"""
    db = session
    old = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=120)
    db.add(
        AgentEvent(
            agent_name="x",
            event_type="x",
            novel_id=novel_id,
            verdict="fail",
            duration_ms=100,
            created_at=old,
            payload={},
        )
    )
    db.commit()
    out = compute_sli(db, observation_minutes=15)
    assert out.samples == 0


def test_per_flag_policy_loads_from_disk():
    """presets/cv/consistency.blocker_hard_gate.yaml 必须被读到。"""
    policy = promotion_engine._resolve_policy("consistency.blocker_hard_gate")
    # max_p95_latency_ms is set in the per-flag yaml, default policy doesn't have it
    assert policy.canary_10.max_p95_latency_ms == 12000
    assert policy.canary_10.rollback_burn_rate_1h_over == 5.0


def test_per_flag_policy_falls_back_to_default():
    """没有 per-flag 文件的 flag 落到 ``policy.yaml flags[]`` 或默认。"""
    policy = promotion_engine._resolve_policy("memory.context_embedding_score")
    assert policy.canary_10.min_samples >= 1


def test_evaluate_flag_uses_per_flag_policy_burn_rate(session, novel_id):
    """专门验证 ``rollback_burn_rate_1h_over`` 路径单独生效——而不是被
    ``rollback_failure_rate`` 抢先触发。

    repair.precision_rewrite canary_10 gate：
        rollback_failure_rate=0.30, rollback_burn_rate_1h_over=5.0, slo_target=0.95
    seed: 200 条事件 / 55 条 fail → failure_rate=0.275 < 0.30（不该触发）
                                   burn_rate = 55/(200*0.05) = 5.5 ≥ 5.0（应触发）
    """
    _seed(session, novel_id=novel_id, n=200, fail=55, agent="patch_writer")
    feature_flags.set_flag(
        "repair.precision_rewrite",
        enabled=True,
        rollout_pct=10,
        changed_by="t",
        reason="test",
    )
    decision = promotion_engine.evaluate_flag(
        "repair.precision_rewrite", db=session
    )
    assert decision.verdict == "rollback", decision
    state = (
        session.query(CVPromotionState)
        .filter_by(flag_name="repair.precision_rewrite")
        .one()
    )
    payload = state.payload or {}
    reason = str(payload.get("reason", ""))
    # 必须明确是 burn_rate 触发（而不是 failure_rate 抢先触发）
    assert "burn_rate_1h" in reason, (
        f"rollback reason should be burn-rate, got reason={reason!r} payload={payload}"
    )
    assert "failure_rate" not in reason, (
        f"failure_rate should not trigger this scenario, got reason={reason!r}"
    )
    assert float(payload.get("burn_rate_1h", 0.0)) >= 5.0, (
        f"burn_rate_1h must reach threshold, got {payload}"
    )
