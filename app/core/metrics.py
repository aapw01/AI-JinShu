"""Centralized Prometheus metric registry (§10).

按文档附录 C 集中注册全部命名 metric，supress 高基数维度（``novel_id`` /
``chapter_num`` / ``character_id`` 等只走 ``agent_events.payload``）。

设计：
- ``prometheus_client`` 软依赖；缺失时 fallback 到进程内 dict，行为与
  ``app.services.agents.events`` 同模式，便于测试断言。
- 每个 metric 暴露三个操作之一：``inc(...)`` / ``observe(...)`` / ``set(...)``。
- 各 agent / scheduler / cv watchdog 直接 import 实例即可调用。
- 测试可读 ``get_metric_value(name, labels)`` 取累计值。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

try:  # pragma: no cover - optional
    from prometheus_client import Counter as _PromCounter  # type: ignore[import-not-found]
    from prometheus_client import Gauge as _PromGauge  # type: ignore[import-not-found]
    from prometheus_client import Histogram as _PromHistogram  # type: ignore[import-not-found]

    _PROM_AVAILABLE = True
except Exception:  # pragma: no cover
    _PromCounter = None  # type: ignore[assignment]
    _PromGauge = None  # type: ignore[assignment]
    _PromHistogram = None  # type: ignore[assignment]
    _PROM_AVAILABLE = False


_LOCK = threading.Lock()
_FALLBACK_VALUES: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}


# --- public ---------------------------------------------------------------
def get_metric_value(name: str, labels: dict[str, str] | None = None) -> float:
    """读取累计值（fallback 路径，仅供测试）。"""
    key = (name, tuple(sorted((labels or {}).items())))
    with _LOCK:
        return _FALLBACK_VALUES.get(key, 0.0)


def reset_metrics() -> None:
    """清空 fallback 计数器（仅供测试 setup）。"""
    with _LOCK:
        _FALLBACK_VALUES.clear()


def _fallback_inc(
    name: str, labels: dict[str, str], value: float = 1.0
) -> None:
    """累加 ``value`` 到 ``(name, labels)`` 桶。`value=0` 时 no-op。"""
    if value <= 0:
        return
    key = (name, tuple(sorted(labels.items())))
    with _LOCK:
        _FALLBACK_VALUES[key] = _FALLBACK_VALUES.get(key, 0.0) + value


def _fallback_set(name: str, labels: dict[str, str], value: float) -> None:
    key = (name, tuple(sorted(labels.items())))
    with _LOCK:
        _FALLBACK_VALUES[key] = value


def _fallback_observe(name: str, labels: dict[str, str], value: float) -> None:
    # histogram 在 fallback 路径下只累计 sum；测试可断言 sum > 0
    key = (name, tuple(sorted(labels.items())))
    with _LOCK:
        _FALLBACK_VALUES[key] = _FALLBACK_VALUES.get(key, 0.0) + value


class _SafeCounter:
    def __init__(self, name: str, doc: str, labels: tuple[str, ...] = ()):
        self._name = name
        self._labels = labels
        self._prom = (
            _PromCounter(name, doc, labelnames=labels)  # type: ignore[misc]
            if _PROM_AVAILABLE and _PromCounter is not None
            else None
        )

    def inc(self, value: float = 1.0, /, **labels: str) -> None:
        """累加 ``value``（默认 1.0）。

        ``value`` 是 positional-only，避免和 prom 自带的 ``labels()`` 冲突。
        ``agent_token_cost_total / agent_token_input_total`` 这类"累计金额/数量"
        必须传真实 amount，否则只能告诉你"调用了几次"，无法做 cost dashboard。
        """
        amount = float(value)
        if amount <= 0:
            return
        try:
            if self._prom is not None:
                if self._labels:
                    self._prom.labels(**labels).inc(amount)
                else:
                    self._prom.inc(amount)
        except Exception:
            logger.debug("counter %s inc failed", self._name, exc_info=True)
        _fallback_inc(self._name, labels, value=amount)


class _SafeGauge:
    def __init__(self, name: str, doc: str, labels: tuple[str, ...] = ()):
        self._name = name
        self._labels = labels
        self._prom = (
            _PromGauge(name, doc, labelnames=labels)  # type: ignore[misc]
            if _PROM_AVAILABLE and _PromGauge is not None
            else None
        )

    def set(self, value: float, **labels: str) -> None:
        try:
            if self._prom is not None:
                if self._labels:
                    self._prom.labels(**labels).set(value)
                else:
                    self._prom.set(value)
        except Exception:
            logger.debug("gauge %s set failed", self._name, exc_info=True)
        _fallback_set(self._name, labels, float(value))


class _SafeHistogram:
    def __init__(
        self,
        name: str,
        doc: str,
        labels: tuple[str, ...] = (),
        buckets: tuple[float, ...] | None = None,
    ):
        self._name = name
        self._labels = labels
        self._prom: Any = None
        if _PROM_AVAILABLE and _PromHistogram is not None:
            kwargs: dict[str, Any] = {"labelnames": labels}
            if buckets:
                kwargs["buckets"] = buckets
            self._prom = _PromHistogram(name, doc, **kwargs)  # type: ignore[misc]

    def observe(self, value: float, **labels: str) -> None:
        try:
            if self._prom is not None:
                if self._labels:
                    self._prom.labels(**labels).observe(value)
                else:
                    self._prom.observe(value)
        except Exception:
            logger.debug("histogram %s observe failed", self._name, exc_info=True)
        _fallback_observe(self._name, labels, float(value))


# --- Registry (按 §10 附录 C 表) ----------------------------------------------
# #1 一致性硬门控
consistency_blocker_total = _SafeCounter(
    "consistency_blocker_total", "Consistency blocker count by category", ("category",)
)
consistency_outline_revise_attempts = _SafeHistogram(
    "consistency_outline_revise_attempts",
    "Histogram of outline_revise attempts per chapter",
    buckets=(1, 2, 3, 5),
)
consistency_final_decision_total = _SafeCounter(
    "consistency_final_decision_total",
    "Final decision after consistency hard gate",
    ("decision",),
)

# #2 alias registry
alias_registry_size = _SafeGauge(
    "alias_registry_size", "Total alias rows by size bucket", ("size_bucket",)
)
unknown_character_false_positive_rate = _SafeGauge(
    "unknown_character_false_positive_rate",
    "Unknown char FPR per dataset",
    ("dataset",),
)

# #3a / #3b / #3c
volume_brief_distill_duration_ms = _SafeHistogram(
    "volume_brief_distill_duration_ms", "Volume brief distill duration", buckets=(50, 200, 500, 2000, 5000)
)
volume_brief_cache_hit_rate = _SafeGauge(
    "volume_brief_cache_hit_rate", "Volume brief cache hit ratio"
)
memory_search_duration_ms = _SafeHistogram(
    "memory_search_duration_ms", "Memory search duration", ("path",), buckets=(10, 50, 200, 500, 2000)
)
memory_search_recall_at_5 = _SafeGauge(
    "memory_search_recall_at_5", "Recall@5 per dataset", ("dataset",)
)
memory_search_timeout_total = _SafeCounter(
    "memory_search_timeout_total", "Memory search timeouts by path", ("path",)
)
memory_rerank_duration_ms = _SafeHistogram(
    "memory_rerank_duration_ms", "Cross-encoder rerank duration", buckets=(10, 50, 200, 500, 1000)
)
memory_rerank_topk_swap_rate = _SafeGauge(
    "memory_rerank_topk_swap_rate", "Top-K swap rate after rerank"
)

# #4 spacetime
spacetime_extract_success_rate = _SafeGauge(
    "spacetime_extract_success_rate", "Spacetime extractor success rate"
)
spacetime_conflict_total = _SafeCounter(
    "spacetime_conflict_total", "Spacetime conflict by kind", ("kind",)
)

# #5 voice drift
voice_drift_score = _SafeGauge(
    "voice_drift_score", "Voice drift score by size bucket", ("size_bucket",)
)
voice_drift_warnings_total = _SafeCounter(
    "voice_drift_warnings_total", "Voice drift warning count"
)

# #6 foreshadow lifecycle
foreshadow_state_transition_total = _SafeCounter(
    "foreshadow_state_transition_total",
    "Foreshadow transitions",
    ("from", "to"),
)
foreshadow_payoff_match_confidence = _SafeHistogram(
    "foreshadow_payoff_match_confidence", "Payoff match confidence", buckets=(0.3, 0.5, 0.7, 0.85, 0.95)
)

# #7 outline auditor
outline_audit_unfulfilled_total = _SafeCounter(
    "outline_audit_unfulfilled_total", "Unfulfilled promises by kind", ("kind",)
)
outline_audit_partial_rate = _SafeGauge(
    "outline_audit_partial_rate", "Partial fulfillment rate"
)

# #8 patch writer
precision_rewrite_attempt_total = _SafeCounter(
    "precision_rewrite_attempt_total", "Precision rewrite attempts"
)
precision_rewrite_success_total = _SafeCounter(
    "precision_rewrite_success_total", "Precision rewrite successes"
)
precision_rewrite_token_saved_ratio = _SafeHistogram(
    "precision_rewrite_token_saved_ratio",
    "Token saved ratio vs full rewrite",
    buckets=(0.1, 0.3, 0.5, 0.7, 0.9),
)
precision_rewrite_anchor_miss_total = _SafeCounter(
    "precision_rewrite_anchor_miss_total", "Anchor miss count"
)

# #9 fact arbitration
fact_arbitration_total = _SafeCounter(
    "fact_arbitration_total", "Fact arbitration decisions", ("decision",)
)
fact_active_count = _SafeGauge(
    "fact_active_count", "Active facts by size bucket", ("size_bucket",)
)

# #10 context selection
context_selection_path_total = _SafeCounter(
    "context_selection_path_total", "Context selection paths", ("scoring",)
)

# #11 fact extraction failure / recovery
fact_extraction_failures_total = _SafeCounter(
    "fact_extraction_failures_total", "Fact extraction failures", ("kind",)
)
fact_extraction_recovered_total = _SafeCounter(
    "fact_extraction_recovered_total", "Fact extraction recovered count"
)
fact_extraction_escalated_total = _SafeCounter(
    "fact_extraction_escalated_total", "Fact extraction escalated to manual"
)

# #12 reader lens
reader_lens_first_read_fluency = _SafeHistogram(
    "reader_lens_first_read_fluency", "First read fluency", buckets=(0.3, 0.5, 0.7, 0.85, 0.95)
)
reader_lens_info_density = _SafeHistogram(
    "reader_lens_info_density", "Info density", buckets=(0.3, 0.5, 0.7, 0.85, 0.95)
)
reader_lens_audit_total = _SafeCounter(
    "reader_lens_audit_total", "Reader lens audits"
)

# Cost governance / R9 —— 必须用 ``.inc(amount, agent=..., stage=...)`` 累加真实数额
agent_token_cost_total = _SafeCounter(
    "agent_token_cost_total",
    "Cumulative LLM USD cost by agent / stage / tier (累加调用的 cost_usd)",
    ("agent", "stage", "model_tier"),
)
agent_token_input_total = _SafeCounter(
    "agent_token_input_total",
    "Cumulative LLM input tokens by agent / stage (累加调用的 input_tokens)",
    ("agent", "stage"),
)
agent_token_output_total = _SafeCounter(
    "agent_token_output_total",
    "Cumulative LLM output tokens by agent / stage (累加调用的 output_tokens)",
    ("agent", "stage"),
)

# F: endpoint-level fallback signal
llm_endpoint_failure_total = _SafeCounter(
    "llm_endpoint_failure_total",
    "LLM endpoint failures by stage / endpoint",
    ("stage", "endpoint"),
)

# Cost guardrail health：scheduler 内预算检查失败的脉冲，用于"guardrail 还
# 在不在线"告警，绝不能让 budget 检查异常静默关掉成本保护。
budget_guardrail_error_total = _SafeCounter(
    "budget_guardrail_error_total",
    "Budget guardrail errors (check failures, missing config)",
    ("reason",),
)

# CV §4.7 / §6.4
cv_promotion_decision_total = _SafeCounter(
    "cv_promotion_decision_total", "CV decisions", ("flag", "decision")
)
cv_promotion_gate_violations_total = _SafeCounter(
    "cv_promotion_gate_violations_total",
    "CV gate violations by flag / gate_name",
    ("flag", "gate_name"),
)

# Flag lifecycle §4.2.1
flag_lifecycle_overdue_total = _SafeGauge(
    "flag_lifecycle_overdue_total", "Number of flags overdue for cleanup"
)
flag_toggle_total = _SafeCounter(
    "flag_toggle_total", "Flag toggles by direction", ("flag", "direction")
)

# Chaos
chaos_injection_recovery_total = _SafeCounter(
    "chaos_injection_recovery_total", "Chaos injection recoveries", ("kind",)
)


__all__ = [
    "agent_token_cost_total",
    "agent_token_input_total",
    "agent_token_output_total",
    "alias_registry_size",
    "budget_guardrail_error_total",
    "chaos_injection_recovery_total",
    "llm_endpoint_failure_total",
    "consistency_blocker_total",
    "consistency_final_decision_total",
    "consistency_outline_revise_attempts",
    "context_selection_path_total",
    "cv_promotion_decision_total",
    "cv_promotion_gate_violations_total",
    "fact_active_count",
    "fact_arbitration_total",
    "fact_extraction_escalated_total",
    "fact_extraction_failures_total",
    "fact_extraction_recovered_total",
    "flag_lifecycle_overdue_total",
    "flag_toggle_total",
    "foreshadow_payoff_match_confidence",
    "foreshadow_state_transition_total",
    "get_metric_value",
    "memory_rerank_duration_ms",
    "memory_rerank_topk_swap_rate",
    "memory_search_duration_ms",
    "memory_search_recall_at_5",
    "memory_search_timeout_total",
    "outline_audit_partial_rate",
    "outline_audit_unfulfilled_total",
    "precision_rewrite_anchor_miss_total",
    "precision_rewrite_attempt_total",
    "precision_rewrite_success_total",
    "precision_rewrite_token_saved_ratio",
    "reader_lens_audit_total",
    "reader_lens_first_read_fluency",
    "reader_lens_info_density",
    "reset_metrics",
    "spacetime_conflict_total",
    "spacetime_extract_success_rate",
    "unknown_character_false_positive_rate",
    "voice_drift_score",
    "voice_drift_warnings_total",
    "volume_brief_cache_hit_rate",
    "volume_brief_distill_duration_ms",
]
