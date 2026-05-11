"""Per-stage circuit breaker for LLM fallback chain (§F).

策略 yaml 在 v2 schema 下声明 ``primary / fallback_a / fallback_b`` + 一段
``circuit_breaker``。本模块提供：

- ``record_call(stage, endpoint, success)``：记录单次调用结果
- ``select_endpoint(stage)``：返回当前 stage 应该用 ``primary | fallback_a |
  fallback_b``，并通知调用方是处于 ``open`` / ``half_open`` 状态
- ``reset_breaker(stage)``：测试用

实现：进程内 dict + RLock。生产可平滑迁移到 Redis（接口不变）。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)


EndpointName = Literal["primary", "fallback_a", "fallback_b"]
BreakerState = Literal["closed", "open", "half_open"]


@dataclass
class _BreakerRecord:
    state: BreakerState = "closed"
    consecutive_failures: int = 0
    opened_at: float = 0.0
    half_open_probe_pending: bool = False


@dataclass
class CircuitConfig:
    """Defaults aligned with §4.8 yaml schema; can be overridden per stage."""

    consecutive_failures_to_open: int = 3
    cooldown_seconds: int = 3600  # 60 minutes
    half_open_probe_ratio: float = 0.1
    fallback_consecutive_failures_to_open: int = 5  # fallback 同样有熔断


@dataclass
class _StageState:
    config: CircuitConfig = field(default_factory=CircuitConfig)
    primary: _BreakerRecord = field(default_factory=_BreakerRecord)
    fallback_a: _BreakerRecord = field(default_factory=_BreakerRecord)
    fallback_b: _BreakerRecord = field(default_factory=_BreakerRecord)


_LOCK = threading.RLock()
_STATES: dict[str, _StageState] = {}


# --- public API -----------------------------------------------------------


def configure_stage(stage: str, config: CircuitConfig) -> None:
    """注册某个 stage 的熔断参数（通常在 strategy 加载时一次性调用）。"""
    with _LOCK:
        st = _STATES.setdefault(stage, _StageState())
        st.config = config


def record_call(
    stage: str, endpoint: EndpointName, *, success: bool
) -> BreakerState:
    """记录一次调用的结果。返回该 endpoint 当前的 breaker state（决策后状态）。"""
    with _LOCK:
        st = _STATES.setdefault(stage, _StageState())
        rec = _get_record(st, endpoint)
        threshold = (
            st.config.consecutive_failures_to_open
            if endpoint == "primary"
            else st.config.fallback_consecutive_failures_to_open
        )
        if success:
            if rec.state in {"open", "half_open"}:
                logger.info(
                    "circuit %s/%s closed after success", stage, endpoint
                )
            rec.state = "closed"
            rec.consecutive_failures = 0
            rec.opened_at = 0.0
            rec.half_open_probe_pending = False
        else:
            rec.consecutive_failures += 1
            if (
                rec.state == "closed"
                and rec.consecutive_failures >= threshold
            ):
                rec.state = "open"
                rec.opened_at = time.time()
                logger.warning(
                    "circuit OPENED stage=%s endpoint=%s after %s failures",
                    stage,
                    endpoint,
                    rec.consecutive_failures,
                )
            elif rec.state == "half_open":
                # half_open 探测失败 → 重新 open
                rec.state = "open"
                rec.opened_at = time.time()
                rec.half_open_probe_pending = False
        return rec.state


def select_endpoint(
    stage: str,
    *,
    exclude: tuple[EndpointName, ...] = (),
) -> tuple[EndpointName, BreakerState]:
    """根据当前 breaker 状态选择应该使用哪条 endpoint。

    决策规则：
    - primary closed → ``primary``
    - primary open & cooldown 未到 → 选择第一个 closed 的 fallback
    - primary open & cooldown 已到 → ``primary`` 转 half_open，按 probe_ratio 概率放过
    - 都熔断 → 仍然选 ``primary``，让真实失败抛出（避免 silently 降级到坏点）

    ``exclude`` 用于"我刚刚试过这条 endpoint 失败了，下次给我下一条"的语义。
    """
    excluded = set(exclude)
    with _LOCK:
        st = _STATES.setdefault(stage, _StageState())
        now = time.time()

        primary = st.primary
        if primary.state == "open":
            if now - primary.opened_at >= st.config.cooldown_seconds:
                primary.state = "half_open"
                primary.half_open_probe_pending = True
                logger.info(
                    "circuit %s/primary → half_open after cooldown", stage
                )

        if "primary" not in excluded and primary.state == "closed":
            return "primary", "closed"
        if "primary" not in excluded and primary.state == "half_open":
            if primary.half_open_probe_pending:
                primary.half_open_probe_pending = False
                return "primary", "half_open"
            chosen = _pick_first_closed_fallback(st, excluded)
            return chosen, "open"
        chosen = _pick_first_closed_fallback(st, excluded)
        primary_state = primary.state if "primary" in excluded else "open"
        return chosen, primary_state


def get_breaker_state(stage: str, endpoint: EndpointName) -> BreakerState:
    """读取某 endpoint 当前 state（测试 / 调试用）。"""
    with _LOCK:
        st = _STATES.setdefault(stage, _StageState())
        return _get_record(st, endpoint).state


def reset_breaker(stage: str | None = None) -> None:
    """全部清空 / 单 stage 清空（测试用）。"""
    with _LOCK:
        if stage is None:
            _STATES.clear()
        else:
            _STATES.pop(stage, None)


# --- helpers --------------------------------------------------------------


def _get_record(st: _StageState, endpoint: EndpointName) -> _BreakerRecord:
    if endpoint == "primary":
        return st.primary
    if endpoint == "fallback_a":
        return st.fallback_a
    return st.fallback_b


def _pick_first_closed_fallback(
    st: _StageState, excluded: set | None = None
) -> EndpointName:
    """选 ``fallback_a / fallback_b`` 里第一个非 OPEN 的；都 OPEN → 退回 primary。"""
    excluded = excluded or set()
    if "fallback_a" not in excluded and st.fallback_a.state != "open":
        return "fallback_a"
    if "fallback_b" not in excluded and st.fallback_b.state != "open":
        return "fallback_b"
    if "fallback_a" not in excluded:
        return "fallback_a"
    if "fallback_b" not in excluded:
        return "fallback_b"
    return "primary"


__all__ = [
    "BreakerState",
    "CircuitConfig",
    "EndpointName",
    "configure_stage",
    "get_breaker_state",
    "record_call",
    "reset_breaker",
    "select_endpoint",
]
