"""#1 outline_revise node behaviour (Phase 0 pass-through skeleton)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core import feature_flags
from app.services.generation.nodes.outline_revise import (
    node_outline_revise,
    route_after_revise,
)


@pytest.fixture(autouse=True)
def _clear_flags():
    feature_flags.invalidate_flags_cache()
    yield
    feature_flags.invalidate_flags_cache()


def _state_with_blockers(*, attempts: int = 0):
    blockers = [SimpleNamespace(category="hard_constraint")]
    report = SimpleNamespace(blockers=blockers)
    return {
        "novel_id": 1,
        "current_chapter": 3,
        "outline": {"title": "ch3"},
        "consistency_report": report,
        "consistency_revise_attempts": attempts,
    }


def test_flag_off_passthrough_returns_empty():
    state = _state_with_blockers()
    assert node_outline_revise(state) == {}


def test_flag_off_route_always_consistency_check():
    state = _state_with_blockers()
    assert route_after_revise(state) == "consistency_check"


def test_flag_on_increments_attempts():
    feature_flags.set_flag(
        "consistency.blocker_hard_gate",
        enabled=True,
        rollout_pct=100,
        changed_by="test",
        reason="enable for #1 test",
    )
    state = _state_with_blockers(attempts=0)
    out = node_outline_revise(state)
    assert out["consistency_revise_attempts"] == 1
    # outline 浅拷贝触发 LangGraph 视为已变更
    assert out["outline"] == {"title": "ch3"}


def test_flag_on_route_save_blocked_after_max_attempts():
    feature_flags.set_flag(
        "consistency.blocker_hard_gate",
        enabled=True,
        rollout_pct=100,
        changed_by="test",
        reason="enable",
    )
    # consistency.yaml 默认 max_outline_revise=2, downgrade_to=save_blocked
    state = _state_with_blockers(attempts=2)
    assert route_after_revise(state) == "save_blocked"


def test_flag_on_route_continues_when_under_max():
    feature_flags.set_flag(
        "consistency.blocker_hard_gate",
        enabled=True,
        rollout_pct=100,
        changed_by="test",
        reason="enable",
    )
    state = _state_with_blockers(attempts=1)
    assert route_after_revise(state) == "consistency_check"


def test_flag_on_no_blockers_returns_empty():
    feature_flags.set_flag(
        "consistency.blocker_hard_gate",
        enabled=True,
        rollout_pct=100,
        changed_by="test",
        reason="enable",
    )
    state = {
        "novel_id": 1,
        "current_chapter": 3,
        "outline": {"title": "ch3"},
        "consistency_report": SimpleNamespace(blockers=[]),
    }
    assert node_outline_revise(state) == {}


def test_llm_revise_path_merges_changes(monkeypatch):
    feature_flags.set_flag(
        "consistency.blocker_hard_gate",
        enabled=True,
        rollout_pct=100,
        changed_by="test",
        reason="enable",
    )
    from app.services.generation.nodes import outline_revise as mod

    def _fake_run(*, schema, **_kwargs):
        return schema.model_validate(
            {
                "revised_outline": {"chapter_objective": "改写后的目标", "extra": "ok"},
                "changed_fields": ["chapter_objective"],
                "rationale": "去掉硬约束冲突",
            }
        )

    monkeypatch.setattr(mod, "run_llm_agent", _fake_run)

    state = _state_with_blockers(attempts=0)
    out = mod.node_outline_revise(state)
    assert out["consistency_revise_attempts"] == 1
    # 合并保留原 title，且写入 LLM 修订字段
    assert out["outline"]["title"] == "ch3"
    assert out["outline"]["chapter_objective"] == "改写后的目标"
    assert out["outline"]["extra"] == "ok"
