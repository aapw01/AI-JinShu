"""SLI computation service (§I).

提供独立的 SLI 聚合接口，把"如何从 ``agent_events`` 算出 failure_rate /
p95_latency / error_budget_burn_rate"封装起来，让 ``promotion_engine`` 与
未来的 dashboard / alerting 共用同一份口径。

计算口径（与 §11.1 §10 附录 C 对齐）：

- ``samples``：观察窗口内的所有 events
- ``failure_rate``：``verdict='fail'`` 占比
- ``p95_latency_ms``：``duration_ms`` 的 95 分位
- ``error_budget_burn_rate_1h``：最近 1 小时 fail 数占目标 SLO 容量的比值；
  ``budget_failures = total * (1 - slo_target)``，burn_rate = ``actual / budget``。
  budget = 0 时返回 0（无意义）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.novel import AgentEvent

logger = logging.getLogger(__name__)


@dataclass
class SLIResult:
    samples: int
    failure_rate: float
    p95_latency_ms: int | None
    error_budget_burn_rate_1h: float
    fail_count: int


def _now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def compute_sli(
    db: Session,
    *,
    observation_minutes: int = 15,
    related_agents: list[str] | None = None,
    slo_target: float = 0.95,
) -> SLIResult:
    """聚合最近 ``observation_minutes`` 分钟内、可选 agent 维度的 SLI。

    一次查询拉到 ``max(observation_minutes, 60)`` 窗口的事件，然后在 Python
    端分桶：
    - 观察窗：用于 ``failure_rate / p95_latency``
    - 1 小时窗：用于 ``error_budget_burn_rate_1h``

    旧实现两次扫表，scheduler 每个 tick 都跑且每个 active flag 各调一次，
    长尾下扫描量翻倍。

    ``slo_target=0.95`` → 错误预算容量 = ``total_1h * 0.05``。
    """
    now = _now_utc()
    obs_cutoff = now - timedelta(minutes=observation_minutes)
    burn_cutoff = now - timedelta(minutes=60)
    fetch_cutoff = min(obs_cutoff, burn_cutoff)

    stmt = (
        select(
            AgentEvent.verdict,
            AgentEvent.duration_ms,
            AgentEvent.created_at,
        )
        .where(AgentEvent.created_at >= fetch_cutoff)
    )
    if related_agents:
        stmt = stmt.where(AgentEvent.agent_name.in_(related_agents))
    rows = db.execute(stmt).all()
    if not rows:
        return SLIResult(
            samples=0,
            failure_rate=0.0,
            p95_latency_ms=None,
            error_budget_burn_rate_1h=0.0,
            fail_count=0,
        )

    obs_total = 0
    obs_fail = 0
    obs_latencies: list[int] = []
    burn_total = 0
    burn_fail = 0
    for verdict, duration_ms, created_at in rows:
        v = (verdict or "")
        if created_at is not None and created_at >= obs_cutoff:
            obs_total += 1
            if v == "fail":
                obs_fail += 1
            if duration_ms:
                obs_latencies.append(int(duration_ms))
        if created_at is not None and created_at >= burn_cutoff:
            burn_total += 1
            if v == "fail":
                burn_fail += 1

    p95: int | None = None
    if obs_latencies:
        obs_latencies.sort()
        idx = max(0, int(round(0.95 * (len(obs_latencies) - 1))))
        p95 = obs_latencies[idx]

    budget_failures = max(0.0, float(burn_total) * (1.0 - float(slo_target)))
    burn_rate = (burn_fail / budget_failures) if budget_failures > 0 else 0.0

    return SLIResult(
        samples=obs_total,
        failure_rate=obs_fail / max(1, obs_total),
        p95_latency_ms=p95,
        error_budget_burn_rate_1h=round(burn_rate, 4),
        fail_count=obs_fail,
    )


__all__ = ["SLIResult", "compute_sli"]
