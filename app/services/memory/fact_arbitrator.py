"""Fact arbitration for #9 (Phase 0 baseline rules; LLM-augmented later).

输入：同一 ``(novel_version_id, entity_id, fact_type)`` 下若干 ``StoryFact`` 行。
输出：``FactArbitrationDecision``，并返回需要软删除（``superseded_by``）的旧 fact id 列表。

Phase 0 规则（不依赖 LLM）：

- 候选必须 ``is_active=true``。
- 跨章新 fact 的 ``confidence`` 减去旧 fact ≥ ``confidence_min_gap``（yaml gate
  默认 0.1）→ 新胜出，旧 superseded。
- 否则保持原状（warn）。

LLM 仲裁、reviewer-must-pass 等高级规则放后续 PR。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.gates import get_gate
from app.models.novel import StoryFact
from app.services.agents.contracts.fact import FactArbitrationDecision

logger = logging.getLogger(__name__)


@dataclass
class ArbitrationOutcome:
    decision: FactArbitrationDecision
    superseded_ids: list[int]


def _bump_metric(decision: str) -> None:
    try:
        from app.core.metrics import fact_arbitration_total

        fact_arbitration_total.inc(decision=decision)
    except Exception:
        pass


def arbitrate_fact(
    db: Session,
    *,
    novel_id: int,
    novel_version_id: int | None,
    entity_id: int,
    fact_type: str,
) -> ArbitrationOutcome:
    """对同一 ``(entity, fact_type)`` 的活跃 fact 做一次仲裁。

    返回新的决策（``keep`` / ``supersede`` / ``warn`` / ``reject``）及需要被
    软删除的 fact id 列表（调用方负责更新 ``superseded_by`` / ``is_active``）。
    """
    rows = (
        db.execute(
            select(StoryFact)
            .where(StoryFact.novel_id == novel_id)
            .where(StoryFact.novel_version_id == novel_version_id)
            .where(StoryFact.entity_id == entity_id)
            .where(StoryFact.fact_type == fact_type)
            .where((StoryFact.is_active.is_(None)) | (StoryFact.is_active == 1))
            .order_by(StoryFact.source_chapter.desc().nullsfirst(), StoryFact.id.desc())
        )
        .scalars()
        .all()
    )

    if len(rows) <= 1:
        _bump_metric("keep")
        return ArbitrationOutcome(
            decision=FactArbitrationDecision(
                decision="keep",
                new_id=rows[0].id if rows else None,
                reason="single active fact",
            ),
            superseded_ids=[],
        )

    gate = get_gate("fact_arbitration", "confidence_min_gap", novel_id=novel_id)
    min_gap = float(gate.threshold or 0.1)

    leader = rows[0]
    challenger = rows[1]
    leader_conf = float(leader.confidence or 0.0)
    challenger_conf = float(challenger.confidence or 0.0)

    if leader_conf - challenger_conf >= min_gap:
        superseded = [r.id for r in rows[1:] if r.id != leader.id]
        _bump_metric("supersede")
        return ArbitrationOutcome(
            decision=FactArbitrationDecision(
                decision="supersede",
                new_id=leader.id,
                superseded_id=challenger.id,
                reason=f"confidence gap {leader_conf - challenger_conf:.2f} >= {min_gap}",
            ),
            superseded_ids=superseded,
        )

    # Confidence gap 不够时，尝试 LLM 仲裁（flag-controlled）
    llm_decision = _try_llm_arbitration(
        novel_id=novel_id,
        leader=leader,
        challenger=challenger,
        leader_conf=leader_conf,
        challenger_conf=challenger_conf,
    )
    if llm_decision is not None:
        if llm_decision.decision == "supersede":
            superseded = [r.id for r in rows[1:] if r.id != leader.id]
            _bump_metric("supersede")
            return ArbitrationOutcome(
                decision=FactArbitrationDecision(
                    decision="supersede",
                    new_id=leader.id,
                    superseded_id=challenger.id,
                    reason=f"llm: {llm_decision.reason}",
                ),
                superseded_ids=superseded,
            )
        if llm_decision.decision == "reject":
            _bump_metric("reject")
            return ArbitrationOutcome(
                decision=FactArbitrationDecision(
                    decision="reject",
                    new_id=leader.id,
                    reason=f"llm: {llm_decision.reason}",
                ),
                superseded_ids=[],
            )

    _bump_metric("warn")
    return ArbitrationOutcome(
        decision=FactArbitrationDecision(
            decision="warn",
            new_id=leader.id,
            reason=f"confidence gap {leader_conf - challenger_conf:.2f} < {min_gap}",
        ),
        superseded_ids=[],
    )


def _try_llm_arbitration(
    *,
    novel_id: int,
    leader: StoryFact,
    challenger: StoryFact,
    leader_conf: float,
    challenger_conf: float,
) -> Any | None:
    """flag ``memory.fact_llm_arbitration`` 开启时调 LLM 在两条 fact 中裁决。

    LLM 输出 schema：``{decision: keep|supersede|reject, reason: str}``。
    校验失败 / LLM 不可用 → 返回 None，调用方走 baseline ``warn`` 路径。
    """
    try:
        from app.core.feature_flags import is_enabled

        if not is_enabled("memory.fact_llm_arbitration", novel_id=novel_id):
            return None
    except Exception:
        return None

    try:
        from typing import Literal

        from pydantic import BaseModel, ConfigDict, Field

        from app.services.agents.llm_agent import run_llm_agent

        class _Decision(BaseModel):
            model_config = ConfigDict(extra="forbid")
            decision: Literal["keep", "supersede", "reject"]
            reason: str = Field(default="")

        return run_llm_agent(
            agent_name="fact_arbitrator",
            event_type="llm_arbitrate",
            template="fact_llm_arbitrate",
            template_kwargs={
                "leader": {
                    "value": leader.value_json,
                    "chapter": leader.source_chapter,
                    "confidence": leader_conf,
                },
                "challenger": {
                    "value": challenger.value_json,
                    "chapter": challenger.source_chapter,
                    "confidence": challenger_conf,
                },
            },
            schema=_Decision,
            novel_id=novel_id,
        )
    except Exception:
        logger.debug("fact LLM arbitration failed", exc_info=True)
        return None
