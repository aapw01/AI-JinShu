from __future__ import annotations

from app.services.generation.heuristics import (
    build_review_gate,
    normalize_progression_payload,
)
from app.services.generation.nodes.finalize import _is_quality_passed


def test_normalize_progression_payload_preserves_stage_specific_fields():
    payload = normalize_progression_payload(
        {
            "score": 0.72,
            "confidence": 0.9,
            "feedback": "推进不足",
            "duplicate_beats": ["重复了上一章的冲突"],
            "no_new_delta": ["本章没有新增不可逆变化"],
            "transition_conflict": ["上一章离场后本章无过渡回到卧室"],
        },
        "推进审校结果",
    )

    assert payload["score"] == 0.72
    assert payload["duplicate_beats"] == ["重复了上一章的冲突"]
    assert payload["no_new_delta"] == ["本章没有新增不可逆变化"]
    assert payload["transition_conflict"] == ["上一章离场后本章无过渡回到卧室"]


def test_build_review_gate_rewrites_when_progression_blockers_present():
    gate = build_review_gate(
        "正文草稿",
        {"score": 0.81, "confidence": 0.88, "must_fix": []},
        {
            "score": 0.84,
            "confidence": 0.91,
            "must_fix": [],
            "no_new_delta": ["本章没有新增变化"],
            "transition_conflict": [],
        },
    )

    assert gate["decision"] == "rewrite"
    assert gate["progression_blockers"] is True


def test_is_quality_passed_requires_reviewer_and_heuristic_aesthetic_floor():
    assert _is_quality_passed(
        review_score=0.81,
        factual_score=0.8,
        progression_score=0.8,
        language_score=0.8,
        reviewer_aesthetic=0.8,
        aesthetic_score_val=0.61,
    )
    assert not _is_quality_passed(
        review_score=0.81,
        factual_score=0.8,
        progression_score=0.8,
        language_score=0.8,
        reviewer_aesthetic=0.8,
        aesthetic_score_val=0.59,
    )
