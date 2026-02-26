"""Evaluation metrics for generation quality/behavior."""
from __future__ import annotations

from collections import Counter
from typing import Any


def compute_closure_action_metrics(actions: list[str]) -> dict[str, Any]:
    seq = [str(a or "").strip() for a in actions if str(a or "").strip()]
    distribution = dict(Counter(seq))
    if len(seq) <= 1:
        return {
            "distribution": distribution,
            "oscillation_rate": 0.0,
            "switches": 0,
            "samples": len(seq),
        }
    switches = 0
    for i in range(1, len(seq)):
        if seq[i] != seq[i - 1]:
            switches += 1
    return {
        "distribution": distribution,
        "oscillation_rate": round(switches / max(1, len(seq) - 1), 4),
        "switches": switches,
        "samples": len(seq),
    }


def compute_abrupt_ending_risk(
    latest_closure_state: dict[str, Any] | None,
    tail_contents: list[str] | None,
) -> dict[str, Any]:
    state = latest_closure_state or {}
    tails = [str(x or "") for x in (tail_contents or []) if str(x or "").strip()]
    reasons: list[str] = []
    score = 0.0

    action = str(state.get("action") or "")
    unresolved = int(state.get("unresolved_count") or 0)
    coverage = float(state.get("must_close_coverage") or 0.0)
    threshold = float(state.get("closure_threshold") or state.get("threshold") or 0.95)

    if action == "force_finalize":
        score += 0.45
        reasons.append("force_finalize")
    if unresolved > 0:
        score += 0.25
        reasons.append("unresolved_items")
    if coverage < max(0.0, threshold - 0.05):
        score += 0.20
        reasons.append("coverage_below_threshold")

    if tails:
        tail_concat = "\n".join(tails[-2:])
        ending_markers = ("终章", "尾声", "完结", "大结局", "全书完", "（完）", "【完】")
        has_ending_marker = any(m in tail_concat for m in ending_markers)
        avg_tail_len = sum(len(x) for x in tails) / max(1, len(tails))
        if not has_ending_marker:
            score += 0.1
            reasons.append("missing_ending_marker")
        if avg_tail_len < 800:
            score += 0.1
            reasons.append("short_tail_chapters")
    else:
        score += 0.2
        reasons.append("missing_tail_content")

    score = max(0.0, min(1.0, score))
    return {
        "score": round(score, 4),
        "is_abrupt": bool(score >= 0.5),
        "reasons": reasons,
    }
