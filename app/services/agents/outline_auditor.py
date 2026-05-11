"""Outline promise auditor (#7)."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.feature_flags import is_enabled
from app.core.gates import get_gate
from app.models.novel import OutlineAuditReportRow
from app.services.agents.contracts.outline import OutlineAuditReport, OutlineContract
from app.services.agents.llm_agent import run_llm_agent

logger = logging.getLogger(__name__)


def audit_chapter_outline(
    db: Session,
    *,
    novel_id: int,
    novel_version_id: int | None,
    chapter_num: int,
    contract: OutlineContract,
    chapter_text: str,
) -> OutlineAuditReport | None:
    """flag-controlled：调用 LLM 比对 outline_contract 与正文。"""
    if not is_enabled("quality.outline_promise_audit", novel_id=novel_id):
        return None

    parsed = run_llm_agent(
        agent_name="outline_auditor",
        event_type="audit",
        template="outline_promise_audit",
        template_kwargs={
            "chapter_num": chapter_num,
            "chapter_objective": contract.chapter_objective,
            "required_new_information": contract.required_new_information or [],
            "payoff": contract.payoff or "",
            "opening_scene": contract.opening_scene or "",
            "forbidden_repeats": contract.forbidden_repeats or [],
            "chapter_text": chapter_text or "",
        },
        schema=OutlineAuditReport,
        novel_id=novel_id,
        chapter_num=chapter_num,
        novel_version_id=novel_version_id,
    )
    if parsed is None:
        return None

    try:
        from app.core.metrics import (
            outline_audit_partial_rate,
            outline_audit_unfulfilled_total,
        )

        partial_count = sum(1 for p in parsed.promises if p.fulfilled == "partial")
        unfulfilled = sum(1 for p in parsed.promises if p.fulfilled == "no")
        if unfulfilled > 0:
            outline_audit_unfulfilled_total.inc(kind="no")
        if partial_count > 0:
            outline_audit_unfulfilled_total.inc(kind="partial")
        if parsed.promises:
            outline_audit_partial_rate.set(partial_count / max(1, len(parsed.promises)))
    except Exception:
        pass

    must_fix_gate = get_gate("outline_audit", "must_fix_threshold", novel_id=novel_id)
    if (
        must_fix_gate.mode != "off"
        and parsed.must_fix_count >= int(must_fix_gate.threshold or 1)
    ):
        logger.info(
            "outline_auditor: must_fix=%s ch=%s exceeded threshold",
            parsed.must_fix_count,
            chapter_num,
        )

    if novel_version_id is not None:
        existing = db.execute(
            select(OutlineAuditReportRow)
            .where(OutlineAuditReportRow.novel_version_id == novel_version_id)
            .where(OutlineAuditReportRow.chapter_num == chapter_num)
        ).scalar_one_or_none()
        partial_count = sum(1 for p in parsed.promises if p.fulfilled == "partial")
        partial_rate = partial_count / max(1, len(parsed.promises))
        row = existing or OutlineAuditReportRow(
            novel_version_id=novel_version_id,
            chapter_num=chapter_num,
        )
        row.must_fix_count = int(parsed.must_fix_count)
        row.partial_rate = float(partial_rate)
        row.payload = parsed.model_dump()
        if existing is None:
            db.add(row)
        try:
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("outline_auditor: persist failed ch=%s", chapter_num)
            return None

    return parsed


__all__ = ["audit_chapter_outline"]
