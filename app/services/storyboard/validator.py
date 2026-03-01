"""Quality validation and scoring for storyboard output."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from app.prompts import render_prompt
from app.services.storyboard.shot_planner import ShotDraft


@dataclass(slots=True)
class QualityGateResult:
    style_consistency_score: float
    hook_score_episode: dict[str, float]
    quality_gate_reasons: list[str]
    completeness_rate: float
    shot_density_risk: float
    rewrite_suggestions: list[str]


def validate_storyboard(
    *,
    shots: list[ShotDraft],
    lane: str,
    target_episode_seconds: int,
    style_keywords: list[str],
) -> QualityGateResult:
    reasons: list[str] = []
    hook_scores: dict[str, float] = {}

    episode_map: dict[int, list[ShotDraft]] = defaultdict(list)
    for shot in shots:
        episode_map[shot.episode_no].append(shot)

    required_fields = (
        "blocking",
        "motivation",
        "performance_note",
        "continuity_anchor",
        "action",
        "dialogue",
    )
    missing = 0
    total = 0
    for shot in shots:
        total += len(required_fields)
        for field in required_fields:
            if not getattr(shot, field, None):
                missing += 1

    completeness_rate = 1.0 if total == 0 else max(0.0, 1.0 - (missing / total))
    if completeness_rate < 0.95:
        reasons.append("导演字段不完整")

    style_hits = 0
    style_total = 0
    for shot in shots:
        corpus = " ".join(
            [
                shot.action or "",
                shot.dialogue or "",
                shot.emotion_beat or "",
                shot.motivation or "",
            ]
        )
        for kw in style_keywords:
            style_total += 1
            if kw and kw in corpus:
                style_hits += 1
    base_style = 0.68 if lane == "horizontal_cinematic" else 0.72
    style_consistency = min(1.0, base_style + (style_hits / max(1, style_total)) * 0.35)
    if style_consistency < 0.75:
        reasons.append("风格一致性不足")

    shot_density_values: list[float] = []
    for ep, ep_shots in episode_map.items():
        total_sec = sum(max(1, s.duration_sec) for s in ep_shots)
        density = len(ep_shots) / max(1.0, total_sec / 60.0)
        shot_density_values.append(density)

        hook_score = _score_episode_hook(ep_shots, lane)
        hook_scores[str(ep)] = hook_score
        if hook_score < 70:
            reasons.append(f"第{ep}集爆点不足")

        if abs(total_sec - target_episode_seconds) > max(20, int(target_episode_seconds * 0.35)):
            reasons.append(f"第{ep}集时长偏离目标")

    shot_density_risk = 0.0
    if shot_density_values:
        peak = max(shot_density_values)
        baseline = 15.0 if lane == "vertical_feed" else 10.0
        shot_density_risk = max(0.0, min(1.0, (peak - baseline) / baseline))
        if shot_density_risk > 0.6:
            reasons.append("镜头密度过高，可能信息过载")

    suggestions = _build_rewrite_suggestions(reasons)
    return QualityGateResult(
        style_consistency_score=round(style_consistency, 4),
        hook_score_episode=hook_scores,
        quality_gate_reasons=sorted(set(reasons)),
        completeness_rate=round(completeness_rate, 4),
        shot_density_risk=round(shot_density_risk, 4),
        rewrite_suggestions=suggestions,
    )


def _score_episode_hook(shots: list[ShotDraft], lane: str) -> float:
    if not shots:
        return 0.0
    first_two = shots[:2]
    conflict_signal = sum(1 for s in first_two if any(k in (s.action or "") for k in ("冲突", "对抗", "危机", "揭示")))
    reversal_signal = sum(1 for s in shots if any(k in (s.motivation or "") for k in ("反转", "揭示", "压迫")))
    emotion_signal = sum(1 for s in shots if (s.emotion_beat or "") in {"紧张", "压迫", "爆发", "悬念"})

    base = 62 if lane == "horizontal_cinematic" else 66
    score = base + conflict_signal * 8 + reversal_signal * 4 + emotion_signal * 2
    return float(max(0, min(100, score)))


def _build_rewrite_suggestions(reasons: list[str]) -> list[str]:
    out: list[str] = []
    for reason in reasons:
        if "爆点不足" in reason:
            out.append(render_prompt("storyboard_rewrite_suggestion_hook").strip())
        elif "时长偏离" in reason:
            out.append(render_prompt("storyboard_rewrite_suggestion_timing").strip())
        elif "风格一致性不足" in reason:
            out.append(render_prompt("storyboard_rewrite_suggestion_style").strip())
        elif "字段不完整" in reason:
            out.append(render_prompt("storyboard_rewrite_suggestion_fields").strip())
        elif "镜头密度过高" in reason:
            out.append(render_prompt("storyboard_rewrite_suggestion_density").strip())
    return out[:8]
