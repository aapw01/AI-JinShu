"""Fact extractor self-heal Celery beat (#11 §11.4).

定期扫 ``status=pending`` 的 ``fact_extraction_failures`` → 用替补 model 跑一次
fact 抽取 → 成功置 ``recovered``，失败 ``retry_count++``，超 max_retries 置
``escalated``。

Phase 0 阶段：调度框架 + 状态流转 + flag 守门，但**真正的 LLM 重试调用占位**
（用 ``extractor_self_heal_runner`` 钩子，由后续 PR 实装 fallback chain）。
"""

from __future__ import annotations

import logging
from typing import Callable

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.feature_flags import is_enabled
from app.models.novel import FactExtractionFailure
from app.services.agents.events import emit_agent_event
from app.workers.celery_app import app

logger = logging.getLogger(__name__)


_MAX_RETRIES = 3


# Hook for the eventual real implementation: takes a failure row, returns
# True on recovery (facts persisted) or False on continued failure.
ExtractorRunner = Callable[[FactExtractionFailure], bool]
_runner_impl: ExtractorRunner | None = None


def register_runner(runner: ExtractorRunner) -> None:
    """让真正的 extractor 实现注册自己；测试也通过这个钩子注入 mock。"""
    global _runner_impl
    _runner_impl = runner


def _default_runner(_row: FactExtractionFailure) -> bool:
    """Phase 0 占位：永远返回 False（除非注册了真实 runner）。"""
    return False


def _process_one(row: FactExtractionFailure) -> str:
    runner = _runner_impl or _default_runner
    try:
        recovered = runner(row)
    except Exception:
        logger.exception("extractor recovery runner crashed")
        recovered = False

    new_status: str
    if recovered:
        new_status = "recovered"
        outcome = "recovered"
    else:
        row.retry_count = int(row.retry_count or 0) + 1
        if row.retry_count >= _MAX_RETRIES:
            new_status = "escalated"
            outcome = "escalated"
        else:
            new_status = "pending"
            outcome = "failed"
    row.status = new_status

    emit_agent_event(
        agent_name="fact_extractor",
        event_type="retry",
        novel_id=int(row.novel_id),
        chapter_num=int(row.chapter_num),
        verdict="warn" if outcome != "recovered" else "pass",
        payload={
            "attempt": int(row.retry_count or 0),
            "fallback_model": "default",
            "outcome": outcome,
        },
    )
    try:
        from app.core.metrics import (
            fact_extraction_escalated_total,
            fact_extraction_failures_total,
            fact_extraction_recovered_total,
        )

        if outcome == "recovered":
            fact_extraction_recovered_total.inc()
        elif outcome == "escalated":
            fact_extraction_escalated_total.inc()
        else:
            fact_extraction_failures_total.inc(
                kind=str(row.failure_kind or "unknown")
            )
    except Exception:
        logger.debug("extractor metric failed", exc_info=True)
    return outcome


def run_recovery_once(*, max_batch: int = 50) -> dict[str, int]:
    """单次扫描入口（纯函数，便于单测）。返回 ``recovered/failed/escalated`` 计数。"""
    if not is_enabled("extractor.self_heal"):
        return {"recovered": 0, "failed": 0, "escalated": 0, "skipped_disabled": 1}

    summary = {"recovered": 0, "failed": 0, "escalated": 0}
    db = SessionLocal()
    try:
        rows = (
            db.execute(
                select(FactExtractionFailure)
                .where(FactExtractionFailure.status == "pending")
                .order_by(FactExtractionFailure.id.asc())
                .limit(max_batch)
            )
            .scalars()
            .all()
        )
        for row in rows:
            outcome = _process_one(row)
            summary[outcome] = summary.get(outcome, 0) + 1
        db.commit()
    except Exception:
        logger.exception("extractor recovery loop failed")
        db.rollback()
    finally:
        db.close()
    return summary


@app.task(bind=True, acks_late=True, reject_on_worker_lost=True)
def fact_extraction_recovery_task(self) -> dict[str, int]:
    return run_recovery_once()
