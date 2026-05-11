"""CV promotion engine (Phase 0 §4.7).

负责：把 ``cv_promotion_state`` 表中的每个 flag 在 ``baseline → canary_10 →
canary_50 → full → stable_2_baselines`` 阶段间搬运。决策完全基于
``agent_events`` 上的 SLI 指标，不依赖 LLM。

每次 ``evaluate_flag(flag_name)`` 调用：
1. 读 yaml ``presets/cv/policy.yaml`` 拿到该 flag 的阶段策略（晋升/回滚阈
   值、最少观察样本数）。yaml 缺失则使用 ``DEFAULT_POLICY``。
2. 从 ``agent_events`` 聚合最近 N 条记录的失败率 / 平均时延 / 成本。
3. 与策略阈值对比 → 输出 ``promote / hold / rollback`` 决策。
4. 决策落 ``cv_promotion_state``（更新 phase / current_canary_pct /
   verdict / payload）；若 ``promote`` 则把对应 feature flag 的
   ``rollout_pct`` 也同步上调。

复杂度刻意保持在"规则引擎"层级，复杂统计推断（贝叶斯、SPRT）留作后续 PR。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


def _now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.feature_flags import set_flag
from app.models.novel import AgentEvent, CVPromotionState

logger = logging.getLogger(__name__)


_POLICY_PATH = Path(__file__).resolve().parents[3] / "presets" / "cv" / "policy.yaml"
_CACHE_TTL = 5.0
_lock = threading.Lock()
_cache: dict[str, Any] = {"ts": 0.0, "value": None}


_PHASES = ["baseline", "canary_10", "canary_50", "full", "stable"]
_PHASE_TO_PCT = {"baseline": 0, "canary_10": 10, "canary_50": 50, "full": 100, "stable": 100}


class PhaseGate(BaseModel):
    """每个晋升阶段的 3 类 gate（SLI / 性能 / 错误预算燃尽）。

    SLI 类：``min_samples`` + ``max_failure_rate`` + ``rollback_failure_rate``。
    性能类：``max_p95_latency_ms`` —— 仅在 promote 时检查。
    错误预算类：``rollback_burn_rate_1h_over`` —— 1h burn rate 超过此阈值即 rollback。
    """

    model_config = ConfigDict(extra="forbid")

    min_samples: int = Field(default=20, ge=1)
    max_failure_rate: float = Field(default=0.05, ge=0.0, le=1.0)
    max_p95_latency_ms: int | None = None
    rollback_failure_rate: float = Field(default=0.20, ge=0.0, le=1.0)
    rollback_burn_rate_1h_over: float | None = Field(default=None, ge=0.0)


class FlagPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    canary_10: PhaseGate = Field(default_factory=PhaseGate)
    canary_50: PhaseGate = Field(default_factory=PhaseGate)
    full: PhaseGate = Field(default_factory=PhaseGate)
    stable: PhaseGate = Field(default_factory=PhaseGate)
    observation_minutes: int = Field(default=15, ge=1)


class PolicyFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    default: FlagPolicy = Field(default_factory=FlagPolicy)
    flags: dict[str, FlagPolicy] = Field(default_factory=dict)


_DEFAULT_POLICY = FlagPolicy()


def _load_policy_file() -> PolicyFile | None:
    now = time.monotonic()
    with _lock:
        if (now - _cache["ts"]) < _CACHE_TTL and _cache["value"] is not None:
            return _cache["value"]
    if not _POLICY_PATH.exists():
        return None
    try:
        raw = yaml.safe_load(_POLICY_PATH.read_text(encoding="utf-8")) or {}
        parsed = PolicyFile.model_validate(raw)
    except Exception:
        logger.exception("cv: failed to parse policy.yaml")
        return None
    with _lock:
        _cache["ts"] = now
        _cache["value"] = parsed
    return parsed


def _resolve_policy(flag_name: str) -> FlagPolicy:
    """优先级：``presets/cv/<flag>.yaml`` > ``presets/cv/policy.yaml flags[<flag>]`` > ``policy.default`` > 内置默认。"""
    per_flag = _load_per_flag_policy(flag_name)
    if per_flag is not None:
        return per_flag
    pf = _load_policy_file()
    if pf is None:
        return _DEFAULT_POLICY
    return pf.flags.get(flag_name, pf.default)


def _load_per_flag_policy(flag_name: str) -> FlagPolicy | None:
    """读 ``presets/cv/<flag_name>.yaml``。文件名中 ``.`` → ``_`` 安全替换。"""
    if not flag_name:
        return None
    safe_name = flag_name.replace("/", "_")
    candidate = _POLICY_PATH.parent / f"{safe_name}.yaml"
    if not candidate.exists():
        return None
    try:
        raw = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
    except Exception:
        logger.exception("cv: failed to read per-flag policy %s", candidate)
        return None
    try:
        return FlagPolicy.model_validate(raw)
    except Exception:
        logger.exception("cv: per-flag policy invalid %s", candidate)
        return None


def _phase_to_gate(policy: FlagPolicy, current_phase: str) -> PhaseGate:
    """决策当前 phase → 下一 phase 时使用哪个 gate（即下一 phase 的入口阈值）。"""
    if current_phase == "baseline":
        return policy.canary_10
    if current_phase == "canary_10":
        return policy.canary_50
    if current_phase == "canary_50":
        return policy.full
    return policy.stable


def _next_phase(current: str) -> str:
    try:
        idx = _PHASES.index(current)
    except ValueError:
        return "baseline"
    return _PHASES[min(idx + 1, len(_PHASES) - 1)]


@dataclass
class SLISnapshot:
    samples: int
    failure_rate: float
    p95_latency_ms: int | None
    burn_rate_1h: float = 0.0


def _sample_sli(
    db: Session, *, observation_minutes: int, related_agents: list[str] | None
) -> SLISnapshot:
    """委托给 ``app.services.cv.sli.compute_sli``，把 burn_rate 也带回。"""
    from app.services.cv.sli import compute_sli

    sli = compute_sli(
        db,
        observation_minutes=observation_minutes,
        related_agents=related_agents,
    )
    return SLISnapshot(
        samples=sli.samples,
        failure_rate=sli.failure_rate,
        p95_latency_ms=sli.p95_latency_ms,
        burn_rate_1h=sli.error_budget_burn_rate_1h,
    )


@dataclass
class PromotionDecision:
    flag_name: str
    previous_phase: str
    next_phase: str
    next_canary_pct: int
    verdict: Literal["promote", "hold", "rollback", "init"]
    reason: str
    sli: SLISnapshot


def _ensure_state_row(db: Session, flag_name: str) -> CVPromotionState:
    row = (
        db.execute(select(CVPromotionState).where(CVPromotionState.flag_name == flag_name))
        .scalar_one_or_none()
    )
    if row is not None:
        return row
    row = CVPromotionState(
        flag_name=flag_name,
        phase="baseline",
        current_canary_pct=0,
        baseline_at=_now_utc(),
        verdict="init",
        payload={},
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


_RELATED_AGENTS_BY_FLAG: dict[str, list[str]] = {
    # 把 flag → 相关 agent_name 列表，用于 SLI 采样过滤。缺省读全表。
    "consistency.blocker_hard_gate": ["consistency_check"],
    "extractor.self_heal": ["fact_extractor"],
    "memory.fact_arbitration_v1": ["fact_arbitrator"],
    "quality.outline_promise_audit": ["outline_auditor"],
    "consistency.spacetime_v1": ["spacetime_extractor"],
    "quality.voice_drift_audit": ["voice_drift"],
    "consistency.foreshadow_lifecycle_v1": ["foreshadow_lifecycle"],
    "repair.precision_rewrite": ["patch_writer"],
    "memory.context_embedding_score": ["context_selector"],
    "quality.reader_lens_audit": ["reader_lens"],
    "consistency.alias_registry_v1": ["alias_builder"],
    "memory.volume_brief_distill": ["volume_brief_distiller"],
    "memory.hybrid_search": ["hybrid_search"],
    "memory.cross_encoder_rerank": ["cross_encoder"],
}


def evaluate_flag(flag_name: str, *, db: Session | None = None) -> PromotionDecision:
    """对单个 flag 跑一次决策。线程/进程安全（每次新事务）。"""
    owns = db is None
    session = db or SessionLocal()
    try:
        state = _ensure_state_row(session, flag_name)
        policy = _resolve_policy(flag_name)
        sli = _sample_sli(
            session,
            observation_minutes=policy.observation_minutes,
            related_agents=_RELATED_AGENTS_BY_FLAG.get(flag_name),
        )

        # rollback gate（任何阶段触发即降回 baseline）
        rollback_gate = _phase_to_gate(policy, state.phase)
        rollback_reason: str | None = None
        if (
            sli.samples >= rollback_gate.min_samples
            and sli.failure_rate >= rollback_gate.rollback_failure_rate
        ):
            rollback_reason = (
                f"failure_rate={sli.failure_rate:.3f} >= "
                f"rollback_thr={rollback_gate.rollback_failure_rate}"
            )
        elif (
            rollback_gate.rollback_burn_rate_1h_over is not None
            and sli.burn_rate_1h >= rollback_gate.rollback_burn_rate_1h_over
        ):
            rollback_reason = (
                f"burn_rate_1h={sli.burn_rate_1h:.2f} >= "
                f"thr={rollback_gate.rollback_burn_rate_1h_over}"
            )
        if rollback_reason is not None:
            decision = PromotionDecision(
                flag_name=flag_name,
                previous_phase=state.phase,
                next_phase="baseline",
                next_canary_pct=0,
                verdict="rollback",
                reason=rollback_reason,
                sli=sli,
            )
        elif state.phase == "stable":
            decision = PromotionDecision(
                flag_name=flag_name,
                previous_phase="stable",
                next_phase="stable",
                next_canary_pct=100,
                verdict="hold",
                reason="already stable",
                sli=sli,
            )
        else:
            gate = _phase_to_gate(policy, state.phase)
            healthy = sli.samples >= gate.min_samples and sli.failure_rate <= gate.max_failure_rate
            if healthy and (
                gate.max_p95_latency_ms is None
                or (sli.p95_latency_ms or 0) <= gate.max_p95_latency_ms
            ):
                nxt = _next_phase(state.phase)
                decision = PromotionDecision(
                    flag_name=flag_name,
                    previous_phase=state.phase,
                    next_phase=nxt,
                    next_canary_pct=_PHASE_TO_PCT[nxt],
                    verdict="promote",
                    reason=(
                        f"failure_rate={sli.failure_rate:.3f} samples={sli.samples} "
                        f"under thr={gate.max_failure_rate}"
                    ),
                    sli=sli,
                )
            else:
                decision = PromotionDecision(
                    flag_name=flag_name,
                    previous_phase=state.phase,
                    next_phase=state.phase,
                    next_canary_pct=state.current_canary_pct,
                    verdict="hold",
                    reason=(
                        f"need samples >= {gate.min_samples} & failure <= {gate.max_failure_rate}; "
                        f"got samples={sli.samples} failure={sli.failure_rate:.3f}"
                    ),
                    sli=sli,
                )

        # apply decision
        state.phase = decision.next_phase
        state.current_canary_pct = decision.next_canary_pct
        state.last_check_at = _now_utc()
        state.verdict = decision.verdict
        state.payload = {
            "samples": sli.samples,
            "failure_rate": sli.failure_rate,
            "p95_latency_ms": sli.p95_latency_ms,
            "burn_rate_1h": sli.burn_rate_1h,
            "reason": decision.reason,
        }
        session.commit()

        # Prometheus
        try:
            from app.core.metrics import (
                cv_promotion_decision_total,
                cv_promotion_gate_violations_total,
            )

            cv_promotion_decision_total.inc(flag=flag_name, decision=decision.verdict)
            if decision.verdict == "rollback":
                cv_promotion_gate_violations_total.inc(
                    flag=flag_name, gate_name="rollback"
                )
        except Exception:
            logger.debug("cv metric bridge failed", exc_info=True)

        # 同步 feature flag 的 rollout_pct（promote/rollback 都要写）
        if decision.verdict in {"promote", "rollback"}:
            try:
                set_flag(
                    flag_name,
                    rollout_pct=decision.next_canary_pct,
                    enabled=True if decision.next_canary_pct > 0 else False,
                    changed_by="cv_watchdog",
                    reason=f"cv {decision.verdict}: {decision.reason}",
                )
            except Exception:
                logger.exception("cv: set_flag failed for %s", flag_name)

        return decision
    finally:
        if owns:
            try:
                session.close()
            except Exception:
                logger.debug("evaluate_flag close failed", exc_info=True)


def invalidate_policy_cache() -> None:
    with _lock:
        _cache["ts"] = 0.0
        _cache["value"] = None
