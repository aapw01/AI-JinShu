"""Policy engines for generation decisions.

This module centralizes decision logic to avoid branch sprawl in pipeline nodes.
All engines are pure functions and return structured outputs for observability/UI.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ClosurePolicyInput:
    """收束状态策略输入。"""
    generated_chapters: int
    target_chapters: int
    min_total_chapters: int
    max_total_chapters: int
    remaining_chapters: int
    remaining_ratio: float
    phase_mode: str
    unresolved_count: int
    must_close_coverage: float
    closure_threshold: float
    tail_rewrite_attempts: int
    bridge_attempts: int


@dataclass(frozen=True)
class ClosurePolicyOutput:
    """收束状态策略输出。"""
    action: str
    reason_codes: list[str]
    confidence: float
    next_limits: dict[str, Any]


class ClosurePolicyEngine:
    """Unified closure gate policy."""

    @staticmethod
    def decide(inp: ClosurePolicyInput) -> ClosurePolicyOutput:
        """根据收束覆盖率、未解决线索和章节预算给出卷末动作。

        这是卷末“继续写 / 桥接章 / 尾章重写 / 直接收束”的统一决策器。
        """
        bridge_budget_total = max(0, inp.max_total_chapters - inp.target_chapters)
        bridge_budget_left = max(0, bridge_budget_total - inp.bridge_attempts)
        reason_codes: list[str] = []

        must_force_finalize = inp.generated_chapters >= inp.max_total_chapters
        in_closing_window = inp.phase_mode in {"closing", "finale"}
        can_finalize_early = (
            inp.generated_chapters >= inp.min_total_chapters
            and inp.must_close_coverage >= inp.closure_threshold
            and in_closing_window
        )
        can_soft_finalize = (
            inp.generated_chapters >= inp.target_chapters
            and inp.unresolved_count <= 1
            and inp.must_close_coverage >= max(0.85, inp.closure_threshold - 0.08)
            and in_closing_window
        )
        need_rewrite_for_coverage = (
            inp.generated_chapters >= inp.min_total_chapters
            and inp.must_close_coverage < inp.closure_threshold
            and in_closing_window
            and inp.tail_rewrite_attempts < 2
        )
        can_extend = (
            inp.generated_chapters >= inp.target_chapters
            and inp.generated_chapters < inp.max_total_chapters
            and inp.unresolved_count > 0
            and bridge_budget_left > 0
        )

        action = "continue"
        if must_force_finalize and inp.unresolved_count > 0 and inp.tail_rewrite_attempts < 2:
            action = "rewrite_tail"
            reason_codes = ["max_reached_unresolved", "tail_rewrite_available"]
        elif must_force_finalize:
            action = "force_finalize"
            reason_codes = ["max_reached"]
        elif need_rewrite_for_coverage:
            action = "rewrite_tail"
            reason_codes = ["coverage_below_threshold", "in_closing_window"]
        elif can_finalize_early:
            action = "finalize"
            reason_codes = ["coverage_pass", "min_reached", "in_closing_window"]
        elif can_soft_finalize:
            action = "finalize"
            reason_codes = ["soft_finalize", "near_threshold", "few_unresolved"]
        elif can_extend:
            action = "bridge_chapter"
            reason_codes = ["unresolved_pending", "bridge_budget_available"]
        else:
            reason_codes = ["continue_default"]

        confidence = 0.7
        if action in {"finalize", "force_finalize"}:
            confidence = 0.9 if inp.must_close_coverage >= inp.closure_threshold else 0.75
        elif action == "rewrite_tail":
            confidence = 0.85
        elif action == "bridge_chapter":
            confidence = 0.8

        return ClosurePolicyOutput(
            action=action,
            reason_codes=reason_codes,
            confidence=confidence,
            next_limits={
                "bridge_budget_total": bridge_budget_total,
                "bridge_budget_left": bridge_budget_left,
                "tail_rewrite_left": max(0, 2 - inp.tail_rewrite_attempts),
            },
        )


@dataclass(frozen=True)
class PacingInput:
    """Pacing输入。"""
    phase_mode: str
    low_progress_streak: int
    progress_signal: float


@dataclass(frozen=True)
class PacingOutput:
    """Pacing输出。"""
    mode: str
    low_progress_streak: int
    progress_signal: float
    reason_codes: list[str]


class PacingController:
    """Chapter pacing mode controller."""

    @staticmethod
    def decide(inp: PacingInput) -> PacingOutput:
        """根据推进信号和低进展 streak 调整当前节奏模式。"""
        low_progress = inp.progress_signal < 0.45
        next_streak = inp.low_progress_streak + 1 if low_progress else 0
        mode = "normal"
        reasons = ["default"]
        if next_streak >= 2:
            mode = "accelerated"
            reasons = ["low_progress_streak"]
        if inp.phase_mode in {"closing", "finale"} and mode == "accelerated":
            mode = "closing_accelerated"
            reasons = ["low_progress_streak", "closing_window"]
        elif inp.phase_mode in {"closing", "finale"}:
            reasons = ["closing_window"]
        return PacingOutput(
            mode=mode,
            low_progress_streak=next_streak,
            progress_signal=max(0.0, min(1.0, inp.progress_signal)),
            reason_codes=reasons,
        )
