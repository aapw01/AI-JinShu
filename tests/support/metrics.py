"""Offline harness run metrics + baseline gate (P4).

Turns the observability that :func:`install_offline_harness` exposes (node trace,
rollback spy) plus the persisted chapters into a single structured metrics dict,
and checks it against a committed baseline. This is the deterministic,
zero-external-call stability signal used both by tests and by the CI gate
(``scripts/offline_harness_report.py``).
"""
from __future__ import annotations

from collections import Counter
from statistics import median
from typing import Any

# Baseline thresholds for a deterministic offline run. Chosen to catch real
# regressions (runaway retry loops, non-convergence, dropped chapters) while
# tolerating the fake pipeline's per-chapter quality-gate rework.
DEFAULT_BASELINE: dict[str, Any] = {
    "chapters_in_order": True,
    "all_chapters_have_content": True,
    "all_converged": True,
    "min_review_score": 0.70,
    "max_writer_per_chapter": 8.0,
    "min_reviewer_per_chapter": 1.0,
}


def summarize_run(
    *,
    node_trace: list[str],
    rollback_calls: list[dict[str, Any]],
    chapters: list[Any],
) -> dict[str, Any]:
    """Compute a structured metrics report from a completed offline run.

    ``chapters`` is the ordered list of persisted ``ChapterVersion`` rows.
    """
    counts = Counter(node_trace)
    n = len(chapters)
    scores = [
        float(getattr(row, "review_score"))
        for row in chapters
        if getattr(row, "review_score", None) is not None
    ]
    with_content = sum(
        1 for row in chapters if getattr(row, "content", None) and len(row.content) >= 100
    )
    chapter_nums = [int(getattr(row, "chapter_num")) for row in chapters]

    writer_calls = int(counts.get("writer", 0))
    reviewer_calls = int(counts.get("reviewer", 0))

    return {
        "chapters_total": n,
        "chapters_with_content": with_content,
        "chapters_in_order": chapter_nums == list(range(1, n + 1)),
        "writer_calls": writer_calls,
        "reviewer_calls": reviewer_calls,
        "finalizer_calls": int(counts.get("finalizer", 0)),
        "revise_calls": int(counts.get("revise", 0)),
        "rollback_rerun_calls": int(counts.get("rollback_rerun", 0)),
        "rollback_invocations": len(rollback_calls),
        "review_score_min": round(min(scores), 4) if scores else 0.0,
        "review_score_median": round(float(median(scores)), 4) if scores else 0.0,
        "all_converged": bool(scores) and min(scores) >= 0.70,
        "writer_per_chapter": round(writer_calls / n, 4) if n else 0.0,
        "reviewer_per_chapter": round(reviewer_calls / n, 4) if n else 0.0,
    }


def check_baseline(
    metrics: dict[str, Any], baseline: dict[str, Any] | None = None
) -> list[str]:
    """Return a list of human-readable baseline violations (empty == pass)."""
    b = baseline or DEFAULT_BASELINE
    violations: list[str] = []

    if b.get("chapters_in_order") and not metrics.get("chapters_in_order"):
        violations.append("chapters not persisted in contiguous order")

    if b.get("all_chapters_have_content"):
        total = int(metrics.get("chapters_total", 0))
        with_content = int(metrics.get("chapters_with_content", 0))
        if total == 0 or with_content != total:
            violations.append(
                f"chapters_with_content {with_content} != chapters_total {total}"
            )

    if b.get("all_converged") and not metrics.get("all_converged"):
        violations.append("not all chapters converged to the high review band")

    min_score = float(b.get("min_review_score", 0.0))
    if float(metrics.get("review_score_min", 0.0)) < min_score:
        violations.append(
            f"review_score_min {metrics.get('review_score_min')} < {min_score}"
        )

    max_wpc = float(b.get("max_writer_per_chapter", float("inf")))
    if float(metrics.get("writer_per_chapter", 0.0)) > max_wpc:
        violations.append(
            f"writer_per_chapter {metrics.get('writer_per_chapter')} > {max_wpc} (runaway loop?)"
        )

    min_rpc = float(b.get("min_reviewer_per_chapter", 0.0))
    if float(metrics.get("reviewer_per_chapter", 0.0)) < min_rpc:
        violations.append(
            f"reviewer_per_chapter {metrics.get('reviewer_per_chapter')} < {min_rpc}"
        )

    return violations
