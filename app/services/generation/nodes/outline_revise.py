"""Outline revise node for #1 consistency hard gate.

Phase 0 状态机骨架：

- ``flag=consistency.blocker_hard_gate`` 关闭时：return ``{}``（pass-through），
  路由仍走原 ``_route_consistency`` 的 ``beats`` 分支。
- 开启时：若 ``state["consistency_report"].blockers`` 非空且
  ``consistency_revise_attempts`` 未达 yaml 配置的 ``max_outline_revise``，
  自增计数后路由回 ``consistency_check``；超过则按 yaml ``downgrade_to``
  字段决定走 ``save_blocked`` 还是 ``beats``（warn）。

**真正的 LLM 修订实现** 留作后续 prompt 调优 PR。本节点目前只负责：

1. 维护 ``consistency_revise_attempts`` 计数。
2. emit ``agent_events``（``consistency_check.revise_attempt``）。
3. 触发 outline 字段的浅拷贝刷新（让 graph 把 outline 当作变更过的字段写回）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.feature_flags import is_enabled
from app.core.gates import get_gate
from app.services.agents.events import emit_agent_event
from app.services.agents.llm_agent import run_llm_agent

logger = logging.getLogger(__name__)


_FLAG = "consistency.blocker_hard_gate"


class _OutlineReviseLLM(BaseModel):
    """Structured output schema for the outline_revise LLM call."""

    model_config = ConfigDict(extra="forbid")

    revised_outline: dict[str, Any] = Field(default_factory=dict)
    changed_fields: list[str] = Field(default_factory=list)
    rationale: str = ""


def _try_llm_revise(
    *,
    novel_id: int,
    chapter_num: int,
    outline: dict[str, Any],
    blockers: list[Any],
    attempt_no: int,
    max_attempts: int,
) -> tuple[dict[str, Any] | None, list[str], str | None]:
    """调用 LLM 真实修订；失败 / 限流 / 关闭 → 返回 (None, [], None)，让外层
    继续走"浅拷贝触发再走一遍 consistency"的保守路径。
    """
    try:
        outline_json = json.dumps(outline or {}, ensure_ascii=False)
    except Exception:
        outline_json = "{}"
    payload_blockers = [
        {
            "category": getattr(b, "category", "unknown"),
            "message": getattr(b, "message", str(b)),
        }
        for b in (blockers or [])
        if b is not None
    ]
    parsed = run_llm_agent(
        agent_name="consistency_check",
        event_type="revise_llm",
        template="outline_revise",
        template_kwargs={
            "outline_json": outline_json,
            "blockers": payload_blockers,
            "attempt_no": attempt_no,
            "max_attempts": max_attempts,
        },
        schema=_OutlineReviseLLM,
        novel_id=novel_id,
        chapter_num=chapter_num,
    )
    if parsed is None or not isinstance(parsed.revised_outline, dict):
        return None, [], None
    return parsed.revised_outline, list(parsed.changed_fields or []), parsed.rationale or None


def node_outline_revise(state: dict[str, Any]) -> dict[str, Any]:
    """Outline revise pass-through 节点（Phase 0）。"""
    novel_id = state.get("novel_id")
    if not is_enabled(_FLAG, novel_id=novel_id):
        return {}

    report = state.get("consistency_report")
    blockers = []
    if report is not None:
        blockers = list(getattr(report, "blockers", None) or [])
    if not blockers:
        return {}

    attempts = int(state.get("consistency_revise_attempts") or 0) + 1

    # 取 max_attempts 用于 prompt 信息提示（非门控；门控在 route_after_revise）
    gate = get_gate("consistency", "hard_constraint", novel_id=novel_id)
    max_attempts = max(1, int(gate.max_outline_revise or 2))

    revised_outline = dict(state.get("outline") or {})
    changed_fields: list[str] = []
    rationale: str | None = None
    fallback_model_used = False

    llm_outline, llm_changed, llm_rationale = _try_llm_revise(
        novel_id=int(novel_id) if novel_id is not None else 0,
        chapter_num=int(state.get("current_chapter") or 0),
        outline=revised_outline,
        blockers=blockers,
        attempt_no=attempts,
        max_attempts=max_attempts,
    )
    if llm_outline:
        # 浅合并：LLM 修订项覆盖原字段；保留原大纲未触及字段。
        merged = dict(revised_outline)
        merged.update(llm_outline)
        revised_outline = merged
        changed_fields = llm_changed
        rationale = llm_rationale
    else:
        fallback_model_used = True  # LLM 不可用 → 标记一次"降级修订"

    emit_agent_event(
        agent_name="consistency_check",
        event_type="revise_attempt",
        novel_id=int(novel_id) if novel_id is not None else 0,
        chapter_num=state.get("current_chapter"),
        verdict="warn",
        payload={
            "attempt": attempts,
            "blocker_categories": [
                getattr(b, "category", str(b)) for b in blockers if b is not None
            ],
            "blocker_count": len(blockers),
            "outline_diff_chars": max(
                0,
                len(json.dumps(revised_outline, ensure_ascii=False))
                - len(json.dumps(state.get("outline") or {}, ensure_ascii=False)),
            ),
            "fallback_model_used": fallback_model_used,
            "changed_fields": changed_fields,
            "rationale": rationale,
        },
    )

    # Prometheus
    try:
        from app.core.metrics import (
            consistency_blocker_total,
            consistency_outline_revise_attempts,
        )

        for blocker in blockers:
            consistency_blocker_total.inc(
                category=str(getattr(blocker, "category", "unknown") or "unknown")
            )
        consistency_outline_revise_attempts.observe(float(attempts))
    except Exception:
        logger.debug("consistency metric failed", exc_info=True)

    return {
        "consistency_revise_attempts": attempts,
        "outline": revised_outline,
    }


def route_after_revise(state: dict[str, Any]) -> str:
    """决定 revise 之后是回到 consistency_check 还是降级。

    flag 关闭：永远 ``consistency_check``（pass-through 模式下不应被调用）。
    flag 开启：达到 yaml ``max_outline_revise`` 后按 ``downgrade_to`` 路由。
    """
    novel_id = state.get("novel_id")
    if not is_enabled(_FLAG, novel_id=novel_id):
        return "consistency_check"

    attempts = int(state.get("consistency_revise_attempts") or 0)
    gate = get_gate("consistency", "hard_constraint", novel_id=novel_id)
    max_attempts = max(0, int(gate.max_outline_revise or 0))

    if attempts < max_attempts:
        return "consistency_check"

    target = (gate.downgrade_to or "save_blocked").strip().lower()
    if target == "warn":
        emit_agent_event(
            agent_name="consistency_check",
            event_type="downgrade",
            novel_id=int(novel_id) if novel_id is not None else 0,
            chapter_num=state.get("current_chapter"),
            verdict="warn",
            payload={"from_mode": "strict", "to_mode": "warn", "category": "hard_constraint"},
        )
        return "beats"

    emit_agent_event(
        agent_name="consistency_check",
        event_type="save_blocked",
        novel_id=int(novel_id) if novel_id is not None else 0,
        chapter_num=state.get("current_chapter"),
        verdict="fail",
        payload={
            "final_blockers": [
                getattr(b, "category", str(b))
                for b in (getattr(state.get("consistency_report"), "blockers", None) or [])
                if b is not None
            ],
            "revise_attempts_total": attempts,
            "downgrade_reason": "max_revise_exceeded",
        },
    )
    return "save_blocked"
