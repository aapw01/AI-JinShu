"""Deterministic snapshot/regression tests for pure generation helpers.

These functions are the decision spine of the pipeline (scoring, review gate,
closure / pacing policy, length control). They have no I/O, so we pin their exact
structured outputs — a behavioural change must be intentional and update these.
"""
from __future__ import annotations

import pytest

from app.services.generation.consistency import ConsistencyIssue, ConsistencyReport
from app.services.generation.heuristics import (
    aesthetic_score,
    build_consistency_scorecard,
    build_review_gate,
    chapter_progress_signal,
    normalize_reviewer_payload,
)
from app.services.generation.length_control import (
    build_chapter_length_prompt_kwargs,
    count_content_words,
    resolve_chapter_length_policy,
    trim_generated_text,
)
from app.services.generation.policies import (
    ClosurePolicyEngine,
    ClosurePolicyInput,
    PacingController,
    PacingInput,
)

pytestmark = pytest.mark.offline

# --- policies: closure gate ------------------------------------------------


def _closure_input(**overrides) -> ClosurePolicyInput:
    base = dict(
        generated_chapters=10,
        target_chapters=90,
        min_total_chapters=80,
        max_total_chapters=100,
        remaining_chapters=80,
        remaining_ratio=0.8,
        phase_mode="developing",
        unresolved_count=0,
        must_close_coverage=0.5,
        closure_threshold=0.9,
        tail_rewrite_attempts=0,
        bridge_attempts=0,
    )
    base.update(overrides)
    return ClosurePolicyInput(**base)


def test_closure_continue_default():
    out = ClosurePolicyEngine.decide(_closure_input())
    assert out.action == "continue"
    assert out.reason_codes == ["continue_default"]
    assert out.confidence == 0.7


def test_closure_force_finalize_when_max_reached_and_resolved():
    out = ClosurePolicyEngine.decide(
        _closure_input(generated_chapters=100, unresolved_count=0, must_close_coverage=0.95)
    )
    assert out.action == "force_finalize"
    assert out.reason_codes == ["max_reached"]
    assert out.confidence == 0.9


def test_closure_rewrite_tail_when_max_reached_but_unresolved():
    out = ClosurePolicyEngine.decide(
        _closure_input(generated_chapters=100, unresolved_count=2, tail_rewrite_attempts=0)
    )
    assert out.action == "rewrite_tail"
    assert out.reason_codes == ["max_reached_unresolved", "tail_rewrite_available"]
    assert out.confidence == 0.85
    assert out.next_limits == {
        "bridge_budget_total": 10,
        "bridge_budget_left": 10,
        "tail_rewrite_left": 2,
    }


def test_closure_finalize_early_in_closing_window():
    out = ClosurePolicyEngine.decide(
        _closure_input(
            generated_chapters=85, phase_mode="closing", must_close_coverage=0.95
        )
    )
    assert out.action == "finalize"
    assert out.reason_codes == ["coverage_pass", "min_reached", "in_closing_window"]
    assert out.confidence == 0.9


def test_closure_bridge_chapter_when_unresolved_and_budget():
    out = ClosurePolicyEngine.decide(
        _closure_input(generated_chapters=92, unresolved_count=3, phase_mode="developing")
    )
    assert out.action == "bridge_chapter"
    assert out.reason_codes == ["unresolved_pending", "bridge_budget_available"]
    assert out.confidence == 0.8


# --- policies: pacing ------------------------------------------------------


def test_pacing_accelerates_after_low_streak():
    out = PacingController.decide(
        PacingInput(phase_mode="developing", low_progress_streak=1, progress_signal=0.2)
    )
    assert out.mode == "accelerated"
    assert out.low_progress_streak == 2
    assert out.reason_codes == ["low_progress_streak"]
    assert out.progress_signal == 0.2


def test_pacing_closing_accelerated():
    out = PacingController.decide(
        PacingInput(phase_mode="closing", low_progress_streak=1, progress_signal=0.2)
    )
    assert out.mode == "closing_accelerated"
    assert out.reason_codes == ["low_progress_streak", "closing_window"]


def test_pacing_normal_resets_streak():
    out = PacingController.decide(
        PacingInput(phase_mode="developing", low_progress_streak=3, progress_signal=0.8)
    )
    assert out.mode == "normal"
    assert out.low_progress_streak == 0
    assert out.reason_codes == ["default"]


# --- length control --------------------------------------------------------


def test_count_content_words_strips_whitespace():
    assert count_content_words("你好 世界\n第三") == 6
    assert count_content_words("") == 0


def test_resolve_chapter_length_policy_clamps():
    assert resolve_chapter_length_policy(None).target_words == 2600
    assert resolve_chapter_length_policy(100).target_words == 2000  # clamped to min
    assert resolve_chapter_length_policy(5000).target_words == 3000  # clamped to soft_max
    assert resolve_chapter_length_policy(2500).target_words == 2500


def test_build_chapter_length_prompt_kwargs_defaults():
    assert build_chapter_length_prompt_kwargs(None) == {
        "word_count": 2600,
        "min_word_count": 2000,
        "target_word_count": 2600,
        "soft_max_word_count": 3000,
        "hard_ceiling_word_count": 3500,
        "ideal_min_word_count": 2200,
        "ideal_max_word_count": 2800,
    }


def test_trim_generated_text_cuts_at_sentence_boundary():
    text = "abcdefghij。klmnop"
    assert trim_generated_text(text, 12) == "abcdefghij。"
    # No boundary in the tail window -> hard cut.
    assert trim_generated_text("abcdefghijklmnop", 12) == "abcdefghijkl"
    # Short text is returned unchanged.
    assert trim_generated_text("短文本", 100) == "短文本"


# --- heuristics: review gate ----------------------------------------------


def test_review_gate_accepts_clean_high_confidence():
    gate = build_review_gate("正文", {"must_fix": [], "confidence": 0.8, "score": 0.8})
    assert gate["decision"] == "accept_with_minor_polish"
    assert gate["must_fix_total"] == 0
    assert gate["avg_confidence"] == 0.8
    assert gate["min_score"] == 0.8
    assert gate["over_correction_risk"] is False


def test_review_gate_rewrites_on_validated_must_fix():
    draft = "这里包含关键证据的原文片段。"
    payload = {
        "must_fix": [
            {"category": "plot", "severity": "must_fix", "claim": "问题", "evidence": "关键证据", "confidence": 0.9}
        ],
        "confidence": 0.9,
        "score": 0.5,
    }
    gate = build_review_gate(draft, payload)
    assert gate["decision"] == "rewrite"
    assert gate["must_fix_validated"] == 1
    assert gate["evidence_coverage"] == 1.0


def test_review_gate_progression_blocker_forces_rewrite():
    payload = {"must_fix": [], "confidence": 0.9, "score": 0.9, "no_new_delta": ["无新推进"]}
    gate = build_review_gate("正文", payload)
    assert gate["progression_blockers"] is True
    assert gate["decision"] == "rewrite"


# --- heuristics: reviewer payload normalisation ---------------------------


def test_normalize_reviewer_payload_dict_clamps_score():
    out = normalize_reviewer_payload({"score": 1.5, "confidence": 0.8})
    assert out["score"] == 1.0
    assert out["confidence"] == 0.8


def test_normalize_reviewer_payload_tuple():
    out = normalize_reviewer_payload((0.9, "反馈"))
    assert out["score"] == 0.9
    assert out["confidence"] == 0.55
    assert out["feedback"] == "反馈"


def test_normalize_reviewer_payload_invalid():
    out = normalize_reviewer_payload(None, default_feedback="兜底")
    assert out["score"] == 0.75
    assert out["confidence"] == 0.4
    assert out["risks"] == ["invalid_reviewer_payload"]
    assert out["feedback"] == "兜底"


# --- heuristics: scoring ---------------------------------------------------


def test_aesthetic_score_bounds_and_determinism():
    assert aesthetic_score("") == 0.0
    sample = "他握紧剑。风很冷。\n\n远处传来钟声，一声接着一声。"
    first = aesthetic_score(sample)
    assert 0.0 <= first <= 1.0
    assert aesthetic_score(sample) == first  # deterministic


def test_chapter_progress_signal_maxes_out():
    signal = chapter_progress_signal(
        outline={"payoff": "回收", "purpose": "推进", "mini_climax": "高潮", "suspense_level": "高"},
        summary_text="摘" * 260,
        final_content="本章出现冲突与反转。",
        extracted_facts={"events": [1, 2, 3, 4, 5, 6]},
        review_score=1.0,
        factual_score=1.0,
    )
    assert signal == 1.0


def test_chapter_progress_signal_low_when_empty():
    signal = chapter_progress_signal(
        outline={}, summary_text="", final_content="", extracted_facts={}, review_score=0.0, factual_score=0.0
    )
    assert signal == 0.0


# --- heuristics: consistency scorecard -------------------------------------


def test_build_consistency_scorecard_scores_and_groups():
    report = ConsistencyReport(
        issues=[
            ConsistencyIssue("blocker", "plot", "剧情冲突"),
            ConsistencyIssue("warning", "character", "角色提示"),
        ],
        passed=False,
    )
    card = build_consistency_scorecard(report)
    assert card["score"] == 0.6
    assert card["blockers"] == 1
    assert card["warnings"] == 1
    assert card["categories"] == {"plot": 1, "character": 1}
    assert card["reason_codes"] == ["character:1", "plot:1"]
    assert card["passed"] is False
