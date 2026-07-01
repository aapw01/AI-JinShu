"""Golden-corpus regression for the rule-based consistency detectors.

Unlike the precomputed-count cases, these feed crafted outline/draft/context to
the *real* DB-free detectors via ``run_consistency_eval_cases`` (live mode), so a
regression in detector logic makes a case fail. Every case is fully offline.
"""
from __future__ import annotations

import pytest

from app.services.generation.harness.consistency_eval import (
    evaluate_consistency_inputs,
    run_consistency_eval_cases,
)

pytestmark = pytest.mark.offline

# --- golden corpus ---------------------------------------------------------

_DEAD_CHARACTER_CASE = {
    "id": "dead-character-reappears",
    "inputs": {
        "chapter_num": 5,
        "outline": {"title": "故人归", "outline": "林珊，再次现身于城门之外。", "payoff": ""},
        "draft": "林珊出现在门口，众人震惊，明明她早已下葬。",
        "context": {
            "character_states": [
                {"key": "林珊", "content": {"name": "林珊", "status": "dead"}},
            ]
        },
        "prewrite": {"specification": {"characters": [{"name": "林珊"}, {"name": "主角"}]}},
    },
    "expected": {"min_blockers": 1, "min_dead_in_draft": 1},
}

_FORBIDDEN_CHARACTER_CASE = {
    "id": "forbidden-character-hard-constraint",
    "inputs": {
        "chapter_num": 6,
        "outline": {"title": "禁忌", "outline": "黑袍人，再度来袭，掀起腥风血雨。"},
        "draft": "黑袍人的身影再次笼罩全城。",
        "context": {"hard_constraints": {"forbidden_characters": ["黑袍人"]}},
        "prewrite": {},
    },
    "expected": {"min_blockers": 1, "min_hard_violations": 1},
}

_FORBIDDEN_PRESENCE_CASE = {
    "id": "entity-forbidden-presence",
    "inputs": {
        "chapter_num": 4,
        "outline": {"title": "意外", "outline": "赵四，突然出现在密室里。"},
        "draft": "赵四现身密室，令人措手不及。",
        "context": {
            "hard_constraints": {
                "entity_hard_constraints": [
                    {"entity": "赵四", "constraint_type": "forbidden_presence"},
                ]
            }
        },
        "prewrite": {},
    },
    "expected": {"min_blockers": 1, "min_hard_violations": 1},
}

_TIMELINE_JUMP_CASE = {
    "id": "timeline-jump-warning",
    "inputs": {
        "chapter_num": 2,
        "outline": {"title": "跃迁", "outline": "数月后，主角抵达新城，物是人非。"},
        "draft": "",
        "context": {
            "story_bible_context": "ch1: 主角刚刚进城，与守卫对峙。",
            "summaries": [{"chapter_num": 1, "summary": "主角进城"}],
        },
        "prewrite": {},
    },
    "expected": {"min_warnings": 1, "max_blockers": 0},
}

_CLEAN_CASE = {
    "id": "clean-first-chapter",
    "inputs": {
        "chapter_num": 1,
        "outline": {"title": "新篇", "outline": "主角踏上旅程，迎接未知的挑战。", "payoff": ""},
        "draft": "",
        "context": {},
        "prewrite": {},
    },
    "expected": {"max_blockers": 0, "max_warnings": 0},
}

GOLDEN_CASES = [
    _DEAD_CHARACTER_CASE,
    _FORBIDDEN_CHARACTER_CASE,
    _FORBIDDEN_PRESENCE_CASE,
    _TIMELINE_JUMP_CASE,
    _CLEAN_CASE,
]


# --- tests -----------------------------------------------------------------


def test_golden_corpus_all_pass():
    result = run_consistency_eval_cases(GOLDEN_CASES)
    failing = [r for r in result["case_results"] if not r["passed"]]
    assert result["passed"] == result["total"], failing


def test_dead_character_is_flagged_as_blocker():
    report = evaluate_consistency_inputs(_DEAD_CHARACTER_CASE["inputs"])
    assert report["blockers"] >= 1
    assert report["dead_in_draft_names"] == ["林珊"]
    assert any("林珊" in msg for msg in report["blocker_messages"])


def test_forbidden_character_detected_in_outline_and_draft():
    report = evaluate_consistency_inputs(_FORBIDDEN_CHARACTER_CASE["inputs"])
    assert report["blockers"] >= 1  # pre-write outline gate
    assert report["hard_violations"] >= 1  # post-write draft gate
    assert "黑袍人" in report["hard_violation_entities"]


def test_clean_chapter_has_no_blockers_or_warnings():
    report = evaluate_consistency_inputs(_CLEAN_CASE["inputs"])
    assert report["blockers"] == 0
    assert report["warnings"] == 0


def test_eval_surfaces_a_wrong_expectation():
    """Proves the eval is a real check, not a tautology: a clean input that
    *claims* to expect a blocker must FAIL."""
    bogus = {
        "id": "clean-but-expects-blocker",
        "inputs": _CLEAN_CASE["inputs"],
        "expected": {"min_blockers": 1},
    }
    result = run_consistency_eval_cases([bogus])
    assert result["failed"] == 1
    assert "blockers" in result["case_results"][0]["reasons"][0]


def test_precomputed_report_mode_still_supported():
    """Backward compatibility: the legacy precomputed-count path still works."""
    cases = [
        {"id": "legacy", "report": {"blockers": 1, "warnings": 0}, "expected": {"min_blockers": 1}},
    ]
    result = run_consistency_eval_cases(cases)
    assert result["passed"] == 1
