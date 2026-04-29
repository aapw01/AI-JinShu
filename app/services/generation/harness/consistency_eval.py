"""Small consistency-eval runner for precomputed long-form quality reports."""
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


def _evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    report = case.get("report") if isinstance(case.get("report"), dict) else {}
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
    """Score consistency eval cases against expected blocker/warning bounds."""
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
