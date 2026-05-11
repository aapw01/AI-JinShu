"""Foreshadow lifecycle service (#6).

把 ``story_foreshadows`` 表上的 lifecycle 字段（已由 alembic 008 加好）按规则
向前推进：

- ``planned`` → ``planted``：当 foreshadow_id 对应的 ``plant_chapter`` 被填上。
- ``planted`` → ``paid``：当 ``payoff_chapter`` 被填上且 ``match_confidence``
  超过 ``foreshadow.payoff_match_strict.threshold``（默认 0.7）。
- ``planted`` → ``stale``：当 (current_chapter - plant_chapter) >= yaml gate
  ``foreshadow.unplanted_overdue.threshold``（默认 5）且仍未兑现。

LLM 语义匹配（embedding/cross-encoder）在 #6 后续 PR 接入；本模块只做：

1. 状态机推进（不动 plant/payoff anchor 字段，写好的就保留）。
2. flag 守门 (``consistency.foreshadow_lifecycle_v1``)。
3. emit ``foreshadow_lifecycle.transition`` 事件。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.feature_flags import is_enabled
from app.core.gates import get_gate
from app.models.novel import StoryForeshadow
from app.services.agents.events import emit_agent_event

logger = logging.getLogger(__name__)


@dataclass
class TransitionResult:
    foreshadow_id: str
    from_state: str
    to_state: str
    reason: str


def advance_foreshadows(
    db: Session,
    *,
    novel_id: int,
    novel_version_id: int | None,
    current_chapter: int,
) -> list[TransitionResult]:
    """对当前 novel_version 下的全部 foreshadows 跑一次状态推进。

    flag 关闭直接返回 []。
    """
    if not is_enabled("consistency.foreshadow_lifecycle_v1", novel_id=novel_id):
        return []

    payoff_gate = get_gate("foreshadow", "payoff_match_strict", novel_id=novel_id)
    overdue_gate = get_gate("foreshadow", "unplanted_overdue", novel_id=novel_id)
    payoff_threshold = float(payoff_gate.threshold or 0.7)
    overdue_threshold = float(overdue_gate.threshold or 5.0)

    rows = (
        db.execute(
            select(StoryForeshadow)
            .where(StoryForeshadow.novel_id == novel_id)
            .where(StoryForeshadow.novel_version_id == novel_version_id)
        )
        .scalars()
        .all()
    )
    transitions: list[TransitionResult] = []
    dirty = False
    for row in rows:
        cur = (row.lifecycle_state or "planned").strip().lower()
        # planned → planted
        if cur == "planned" and row.plant_chapter:
            row.lifecycle_state = "planted"
            transitions.append(
                TransitionResult(
                    foreshadow_id=row.foreshadow_id,
                    from_state="planned",
                    to_state="planted",
                    reason=f"plant_chapter={row.plant_chapter}",
                )
            )
            dirty = True
            cur = "planted"

        # planted → paid
        if cur == "planted" and row.payoff_chapter:
            mc = float(row.match_confidence or 0.0)
            if mc >= payoff_threshold:
                row.lifecycle_state = "paid"
                transitions.append(
                    TransitionResult(
                        foreshadow_id=row.foreshadow_id,
                        from_state="planted",
                        to_state="paid",
                        reason=f"match_confidence={mc:.2f} >= {payoff_threshold}",
                    )
                )
                dirty = True
                continue

        # planted → stale (overdue 未兑现)
        if cur == "planted" and not row.payoff_chapter and row.plant_chapter:
            gap = max(0, int(current_chapter) - int(row.plant_chapter))
            if gap >= overdue_threshold:
                row.lifecycle_state = "stale"
                transitions.append(
                    TransitionResult(
                        foreshadow_id=row.foreshadow_id,
                        from_state="planted",
                        to_state="stale",
                        reason=f"gap={gap} >= overdue={overdue_threshold}",
                    )
                )
                dirty = True

    if dirty:
        db.commit()

    for t in transitions:
        emit_agent_event(
            agent_name="foreshadow_lifecycle",
            event_type="transition",
            novel_id=int(novel_id),
            chapter_num=current_chapter,
            verdict="warn" if t.to_state == "stale" else "pass",
            payload={
                "foreshadow_id": t.foreshadow_id,
                "from_state": t.from_state,
                "to_state": t.to_state,
                "reason": t.reason,
            },
        )
        try:
            from app.core.metrics import foreshadow_state_transition_total

            foreshadow_state_transition_total.inc(
                **{"from": t.from_state, "to": t.to_state}
            )
        except Exception:
            logger.debug("foreshadow metric failed", exc_info=True)

    return transitions


__all__ = ["TransitionResult", "advance_foreshadows"]
