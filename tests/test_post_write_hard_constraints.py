"""Tests for post-write hard-constraint gate in cross_chapter_check.

Pre-write 阶段对 forbidden_characters / entity_hard_constraints 走的是 soft-fail
（_route_consistency 始终返回 beats），这意味着 writer 完全可能忽略 prompt 里
的硬约束告警。这里验证 post-write 的兜底门控真的会：

1. 把硬约束违规以 must_fix 形式写入 review_suggestions.cross_chapter_contradictions
2. 把违规结构化记录到 review_suggestions.hard_constraint_violations
3. 强制 review_gate.decision = "rewrite"
"""
from __future__ import annotations

import pytest

from app.services.generation.consistency import (
    detect_entity_constraint_violations,
    detect_forbidden_character_violations,
    detect_hard_constraint_violations,
)
from app.services.generation.nodes.cross_chapter_check import node_cross_chapter_check


@pytest.fixture
def _patch_cross_chapter(monkeypatch):
    """Disable LLM calls / progress hooks so the test isolates rule-based logic."""
    monkeypatch.setattr(
        "app.services.generation.nodes.cross_chapter_check.progress",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.generation.nodes.cross_chapter_check.chapter_progress",
        lambda *_args, **_kwargs: 0.58,
    )
    monkeypatch.setattr(
        "app.services.generation.nodes.cross_chapter_check.get_model_for_stage",
        lambda *_args, **_kwargs: ("openai", "mock-reviewer"),
    )
    monkeypatch.setattr(
        "app.services.generation.nodes.cross_chapter_check.get_inference_for_stage",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "app.services.generation.nodes.cross_chapter_check._get_dead_characters",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.generation.nodes.cross_chapter_check.extract_unknown_characters",
        lambda *_args, **_kwargs: set(),
    )


def _base_state(*, draft: str, hard_constraints: dict) -> dict:
    return {
        "current_chapter": 12,
        "draft": draft,
        "strategy": "web-novel",
        "num_chapters": 30,
        "target_language": "zh",
        "context": {
            "summaries": [],
            "full_recent_summaries": [],
            "character_states": [],
            "hard_constraints": hard_constraints,
        },
        "prewrite": {"specification": {"characters": [{"name": "主角"}]}},
        "reviewer": object(),  # unused: cross/unknown LLM checks short-circuit on empty data
        "review_suggestions": {},
        "review_gate": {},
        "progress_callback": None,
    }


# ---------------------------------------------------------------------------
# Helper-level coverage
# ---------------------------------------------------------------------------

def test_helper_detects_forbidden_character_in_text():
    out = detect_forbidden_character_violations(
        text="李青此刻竟然出现在城门口。",
        context={"hard_constraints": {"forbidden_characters": ["李青", "未提及者"]}},
    )
    assert len(out) == 1
    assert out[0]["entity"] == "李青"
    assert out[0]["constraint_type"] == "forbidden_characters"


def test_helper_detects_entity_forbidden_presence_and_pattern():
    text = "王岳带兵冲入城内，与主角并肩作战。林陌断臂之后又双手挥剑。"
    context = {
        "hard_constraints": {
            "entity_hard_constraints": [
                {"entity": "王岳", "constraint_type": "forbidden_presence"},
                {
                    "entity": "林陌",
                    "constraint_type": "forbidden_action_pattern",
                    "forbidden_patterns": ["双手挥剑", "双臂发力"],
                },
                {"entity": "未出场者", "constraint_type": "forbidden_presence"},
            ]
        }
    }
    out = detect_entity_constraint_violations(text=text, context=context)
    entities = sorted(v["entity"] for v in out)
    assert entities == ["林陌", "王岳"]
    pattern_violation = next(v for v in out if v["entity"] == "林陌")
    assert pattern_violation["matched_pattern"] == "双手挥剑"


def test_unified_helper_combines_both_categories():
    out = detect_hard_constraint_violations(
        text="李青未死，王岳也回来了。",
        context={
            "hard_constraints": {
                "forbidden_characters": ["李青"],
                "entity_hard_constraints": [
                    {"entity": "王岳", "constraint_type": "forbidden_presence"}
                ],
            }
        },
    )
    assert {v["entity"] for v in out} == {"李青", "王岳"}


def test_helper_no_op_when_no_hard_constraints():
    assert detect_hard_constraint_violations(text="任何文本", context={}) == []
    assert detect_hard_constraint_violations(text="", context={"hard_constraints": {"forbidden_characters": ["李青"]}}) == []


# ---------------------------------------------------------------------------
# Node-level integration: forces rewrite on hard-constraint violation
# ---------------------------------------------------------------------------

def test_node_forces_rewrite_when_forbidden_character_in_draft(_patch_cross_chapter):
    state = _base_state(
        draft="李青从暗处走出，递给主角一封信。",
        hard_constraints={"forbidden_characters": ["李青"]},
    )

    out = node_cross_chapter_check(state)

    assert out, "post-write hard-constraint gate must surface a result"
    assert out["review_gate"]["decision"] == "rewrite"

    suggestions = out["review_suggestions"]
    contradictions = suggestions["cross_chapter_contradictions"]
    assert any(c.get("severity") == "must_fix" and "李青" in c.get("claim", "") for c in contradictions)

    violations = suggestions["hard_constraint_violations"]
    assert len(violations) == 1
    assert violations[0]["entity"] == "李青"
    assert violations[0]["constraint_type"] == "forbidden_characters"
    assert violations[0]["chapter_num"] == 12


def test_node_forces_rewrite_on_entity_forbidden_action_pattern(_patch_cross_chapter):
    state = _base_state(
        draft="林陌断臂之后还能双手挥剑，敌军溃散。",
        hard_constraints={
            "entity_hard_constraints": [
                {
                    "entity": "林陌",
                    "constraint_type": "forbidden_action_pattern",
                    "forbidden_patterns": ["双手挥剑"],
                }
            ]
        },
    )

    out = node_cross_chapter_check(state)

    assert out["review_gate"]["decision"] == "rewrite"
    violation = out["review_suggestions"]["hard_constraint_violations"][0]
    assert violation["entity"] == "林陌"
    assert violation["matched_pattern"] == "双手挥剑"


def test_node_no_op_when_draft_does_not_violate_hard_constraints(_patch_cross_chapter):
    state = _base_state(
        draft="主角独自走在长街上，思索下一步行动。",
        hard_constraints={
            "forbidden_characters": ["李青"],
            "entity_hard_constraints": [
                {"entity": "王岳", "constraint_type": "forbidden_presence"},
            ],
        },
    )

    out = node_cross_chapter_check(state)

    assert out == {}
