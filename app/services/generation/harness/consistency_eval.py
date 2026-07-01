"""Consistency-eval runner for long-form quality regression.

Two modes:

1. **Precomputed** — a case supplies a ``report`` dict of metric counts, compared
   against ``expected`` bounds. Cheap, but only checks the comparison logic.
2. **Live** (preferred) — a case supplies ``inputs`` (outline/draft/context/
   prewrite/chapter_num) and the runner executes the *real* DB-free consistency
   detectors to PRODUCE the report, then compares. This makes the eval a genuine
   regression on detector behaviour (dead characters, hard constraints, timeline
   jumps, anti-repetition, transition gaps), not a tautology.

Only the DB-free / ledger-free detectors are run so the eval stays deterministic
and fully offline; the DB-backed checks (duplicate-chapter, thread-ledger
foreshadowing / overload) are intentionally skipped here.
"""
from __future__ import annotations

from typing import Any


def _count(value: Any) -> int:
    if isinstance(value, int):
        return int(value)
    if isinstance(value, list | tuple | set):
        return len(value)
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 1


def _metric_name(expectation_key: str) -> str:
    if expectation_key.startswith("min_"):
        return expectation_key.removeprefix("min_")
    if expectation_key.startswith("max_"):
        return expectation_key.removeprefix("max_")
    return expectation_key


def evaluate_consistency_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    """Run the DB-free consistency detectors and return a metric report.

    Mirrors the rule-based portion of ``check_consistency`` (pre-write) plus the
    rule-based portion of ``node_cross_chapter_check`` (post-write dead-character
    and hard-constraint gates), without touching the database or thread ledger.
    """
    from app.services.generation.consistency import (
        ConsistencyReport,
        _check_character_existence,
        _check_entity_hard_constraints,
        _check_hard_constraints,
        _check_outline_dependency,
        _check_progression_conflicts,
        _check_timeline_conflicts,
        _check_transition_conflicts,
        _get_dead_characters,
        detect_hard_constraint_violations,
        extract_unknown_characters,
    )

    outline = inputs.get("outline") if isinstance(inputs.get("outline"), dict) else {}
    context = inputs.get("context") if isinstance(inputs.get("context"), dict) else {}
    prewrite = inputs.get("prewrite") if isinstance(inputs.get("prewrite"), dict) else {}
    draft = str(inputs.get("draft") or "")
    chapter_num = int(inputs.get("chapter_num") or 1)

    report = ConsistencyReport()
    _check_character_existence(report, outline, prewrite, context, chapter_num)
    _check_hard_constraints(report, outline, context, chapter_num)
    _check_entity_hard_constraints(report, outline, context, chapter_num)
    _check_outline_dependency(report, context, chapter_num)
    _check_timeline_conflicts(report, outline, context, chapter_num)
    _check_progression_conflicts(report, outline, context, chapter_num)
    _check_transition_conflicts(report, outline, context, chapter_num)

    # Post-write rule signals (rule portion of node_cross_chapter_check).
    dead = _get_dead_characters(context)
    dead_in_draft = sorted(name for name in dead if name and name in draft)
    hard_in_draft = detect_hard_constraint_violations(text=draft, context=context)
    unknown = sorted(extract_unknown_characters(draft, prewrite)) if draft else []

    return {
        "blockers": len(report.blockers),
        "warnings": len(report.warnings),
        "dead_in_draft": len(dead_in_draft),
        "hard_violations": len(hard_in_draft),
        "unknown_characters": len(unknown),
        "blocker_messages": [i.message for i in report.blockers],
        "warning_messages": [i.message for i in report.warnings],
        "dead_in_draft_names": dead_in_draft,
        "hard_violation_entities": [v.get("entity") for v in hard_in_draft],
        "unknown_character_names": unknown,
    }


def _resolve_report(case: dict[str, Any]) -> dict[str, Any]:
    """Live report from ``inputs`` when present, else the precomputed ``report``."""
    if isinstance(case.get("inputs"), dict):
        return evaluate_consistency_inputs(case["inputs"])
    return case.get("report") if isinstance(case.get("report"), dict) else {}


def _evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    report = _resolve_report(case)
    expected = case.get("expected") if isinstance(case.get("expected"), dict) else {}
    reasons: list[str] = []

    for key, expected_value in sorted(expected.items()):
        metric = _metric_name(str(key))
        if metric not in report:
            reasons.append(f"missing report metric: {metric}")
            continue
        actual = _count(report.get(metric))
        expected_count = _count(expected_value)
        if str(key).startswith("min_") and actual < expected_count:
            reasons.append(f"{metric} expected >= {expected_count}, got {actual}")
        elif str(key).startswith("max_") and actual > expected_count:
            reasons.append(f"{metric} expected <= {expected_count}, got {actual}")
        elif not str(key).startswith(("min_", "max_")) and actual != expected_count:
            reasons.append(f"{metric} expected == {expected_count}, got {actual}")

    return {
        "id": str(case.get("id") or ""),
        "passed": not reasons,
        "reasons": reasons,
        "report": dict(report),
        "expected": dict(expected),
    }


def run_consistency_eval_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Score consistency eval cases against expected blocker/warning bounds.

    Each case is either precomputed (``report``) or live (``inputs``); see the
    module docstring.
    """
    case_results = [_evaluate_case(case) for case in cases]
    total = len(case_results)
    passed = sum(1 for result in case_results if result["passed"])
    failed = total - passed
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": (passed / total) if total else 1.0,
        "case_results": case_results,
    }
