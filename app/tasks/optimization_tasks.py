"""Celery 任务壳：9 条优化点的占位实现 (Phase 0)。

约束：
- 全部 task 默认 ``flag-off`` ⇒ 直接 ``{"skipped_disabled": True}`` 返回，**不**
  接触 LLM、不写表。
- ``flag-on`` 路径目前仅写一条 ``agent_events.no-op``（让链路可观测）。真正的
  业务逻辑（LLM prompt / 抽取 / 比对）由后续 prompt-tuning PR 填入。

每个 task 暴露一个 ``run_*_once(novel_id, chapter_num=...)`` 纯函数版本，
方便单测与手工调用；Celery 包装在最后。
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.feature_flags import is_enabled
from app.services.agents.events import emit_agent_event
from app.workers.celery_app import app

logger = logging.getLogger(__name__)


def _stub(
    *,
    flag: str,
    agent_name: str,
    event_type: str,
    novel_id: int,
    chapter_num: int | None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not is_enabled(flag, novel_id=novel_id):
        return {"skipped_disabled": True, "flag": flag}
    emit_agent_event(
        agent_name=agent_name,
        event_type=event_type,
        novel_id=int(novel_id),
        chapter_num=chapter_num,
        verdict="skipped",
        payload={"stub": True, **(payload or {})},
    )
    return {"executed": True, "flag": flag, "stub": True}


# --- #2 alias registry build -----------------------------------------------
def run_alias_build_once(
    novel_id: int,
    *,
    chapter_num: int | None = None,
    novel_version_id: int | None = None,
    aliases: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not is_enabled("consistency.alias_registry_v1", novel_id=novel_id):
        return {"skipped_disabled": True, "flag": "consistency.alias_registry_v1"}
    if novel_version_id is None or not aliases:
        return {"skipped_no_input": True, "flag": "consistency.alias_registry_v1"}
    from app.core.database import SessionLocal
    from app.services.memory.alias_registry import bulk_register

    db = SessionLocal()
    try:
        written = bulk_register(db, novel_version_id=novel_version_id, items=aliases)
        return {"executed": True, "written": written}
    finally:
        db.close()


# --- #3a volume brief distill ----------------------------------------------
def run_volume_brief_distill_once(
    novel_id: int,
    *,
    volume_no: int | None = None,
    chapter_summaries: list[dict[str, Any]] | None = None,
    novel_version_id: int | None = None,
) -> dict[str, Any]:
    if not is_enabled("memory.volume_brief_distill", novel_id=novel_id):
        return {"skipped_disabled": True, "flag": "memory.volume_brief_distill"}
    if volume_no is None or not chapter_summaries:
        return {"skipped_no_input": True, "flag": "memory.volume_brief_distill"}
    from app.core.database import SessionLocal
    from app.services.memory.volume_brief import distill_volume_brief

    db = SessionLocal()
    try:
        out = distill_volume_brief(
            db,
            novel_id=novel_id,
            novel_version_id=novel_version_id,
            volume_no=volume_no,
            chapter_summaries=chapter_summaries,
        )
        return {"executed": True, "char_count": out.char_count if out else None}
    finally:
        db.close()


# --- #3b hybrid search index sync ------------------------------------------
def run_hybrid_search_sync_once(novel_id: int) -> dict[str, Any]:
    return _stub(
        flag="memory.hybrid_search",
        agent_name="hybrid_search",
        event_type="sync",
        novel_id=novel_id,
        chapter_num=None,
    )


# --- #3c rerank warmup -----------------------------------------------------
def run_rerank_warmup_once(novel_id: int) -> dict[str, Any]:
    return _stub(
        flag="memory.cross_encoder_rerank",
        agent_name="cross_encoder",
        event_type="warmup",
        novel_id=novel_id,
        chapter_num=None,
    )


# --- #4 spacetime extract --------------------------------------------------
def run_spacetime_extract_once(
    novel_id: int,
    *,
    chapter_num: int,
    chapter_text: str | None = None,
    novel_version_id: int | None = None,
    prev_anchor: str | None = None,
) -> dict[str, Any]:
    if not is_enabled("consistency.spacetime_v1", novel_id=novel_id):
        return {"skipped_disabled": True, "flag": "consistency.spacetime_v1"}
    if not chapter_text:
        return {"skipped_no_text": True, "flag": "consistency.spacetime_v1"}
    from app.core.database import SessionLocal
    from app.services.agents.spacetime_extractor import extract_and_persist

    db = SessionLocal()
    try:
        out = extract_and_persist(
            db,
            novel_id=novel_id,
            novel_version_id=novel_version_id,
            chapter_num=chapter_num,
            chapter_text=chapter_text,
            prev_anchor=prev_anchor,
        )
        return {"executed": True, "extracted": out is not None}
    finally:
        db.close()


# --- #5 voice drift audit --------------------------------------------------
def run_voice_drift_once(
    novel_id: int,
    *,
    chapter_num: int,
    character_key: str | None = None,
    chapter_text: str | None = None,
    novel_version_id: int | None = None,
) -> dict[str, Any]:
    if not is_enabled("quality.voice_drift_audit", novel_id=novel_id):
        return {"skipped_disabled": True, "flag": "quality.voice_drift_audit"}
    if not character_key or not chapter_text:
        return {"skipped_no_input": True, "flag": "quality.voice_drift_audit"}
    from app.core.database import SessionLocal
    from app.services.agents.voice_drift import audit_chapter_voice

    db = SessionLocal()
    try:
        report = audit_chapter_voice(
            db,
            novel_id=novel_id,
            novel_version_id=novel_version_id,
            chapter_num=chapter_num,
            character_key=character_key,
            chapter_text=chapter_text,
        )
        return {
            "executed": True,
            "drift_score": report.drift_score if report else None,
            "triggered": bool(report.triggered) if report else False,
        }
    finally:
        db.close()


# --- #6 foreshadow lifecycle audit ----------------------------------------
def run_foreshadow_lifecycle_once(
    novel_id: int,
    *,
    chapter_num: int,
    novel_version_id: int | None = None,
) -> dict[str, Any]:
    if not is_enabled("consistency.foreshadow_lifecycle_v1", novel_id=novel_id):
        return {"skipped_disabled": True, "flag": "consistency.foreshadow_lifecycle_v1"}
    from app.core.database import SessionLocal
    from app.services.memory.foreshadow_lifecycle import advance_foreshadows

    db = SessionLocal()
    try:
        transitions = advance_foreshadows(
            db,
            novel_id=novel_id,
            novel_version_id=novel_version_id,
            current_chapter=chapter_num,
        )
        return {"executed": True, "transitions": len(transitions)}
    finally:
        db.close()


# --- #7 outline promise audit ---------------------------------------------
def run_outline_audit_once(
    novel_id: int,
    *,
    chapter_num: int,
    contract: dict[str, Any] | None = None,
    chapter_text: str | None = None,
    novel_version_id: int | None = None,
) -> dict[str, Any]:
    if not is_enabled("quality.outline_promise_audit", novel_id=novel_id):
        return {"skipped_disabled": True, "flag": "quality.outline_promise_audit"}
    if not contract or not chapter_text:
        return {"skipped_no_input": True, "flag": "quality.outline_promise_audit"}
    from app.core.database import SessionLocal
    from app.services.agents.contracts.outline import OutlineContract
    from app.services.agents.outline_auditor import audit_chapter_outline

    db = SessionLocal()
    try:
        outline_contract = OutlineContract.model_validate({"chapter_num": chapter_num, **contract})
        out = audit_chapter_outline(
            db,
            novel_id=novel_id,
            novel_version_id=novel_version_id,
            chapter_num=chapter_num,
            contract=outline_contract,
            chapter_text=chapter_text,
        )
        return {"executed": True, "must_fix_count": out.must_fix_count if out else None}
    finally:
        db.close()


# --- #8 precision rewrite -------------------------------------------------
def run_precision_rewrite_once(novel_id: int, *, chapter_num: int) -> dict[str, Any]:
    return _stub(
        flag="repair.precision_rewrite",
        agent_name="patch_writer",
        event_type="rewrite",
        novel_id=novel_id,
        chapter_num=chapter_num,
    )


# --- #10 context embedding score ------------------------------------------
def run_context_embedding_score_once(novel_id: int, *, chapter_num: int) -> dict[str, Any]:
    return _stub(
        flag="memory.context_embedding_score",
        agent_name="context_selector",
        event_type="score",
        novel_id=novel_id,
        chapter_num=chapter_num,
    )


# --- #12 reader lens audit ------------------------------------------------
def run_reader_lens_once(
    novel_id: int,
    *,
    chapter_num: int,
    chapter_text: str | None = None,
    prior_summary: str | None = None,
    novel_version_id: int | None = None,
    model_label: str = "primary",
) -> dict[str, Any]:
    if not is_enabled("quality.reader_lens_audit", novel_id=novel_id):
        return {"skipped_disabled": True, "flag": "quality.reader_lens_audit"}
    if not chapter_text:
        return {"skipped_no_text": True, "flag": "quality.reader_lens_audit"}
    from app.core.database import SessionLocal
    from app.services.agents.reader_lens import evaluate_chapter

    db = SessionLocal()
    try:
        out = evaluate_chapter(
            db,
            novel_id=novel_id,
            novel_version_id=novel_version_id,
            chapter_num=chapter_num,
            chapter_text=chapter_text,
            prior_summary=prior_summary,
            model_label=model_label,
        )
        return {
            "executed": True,
            "fluency": out.first_read_fluency if out else None,
            "missing": list(out.missing_setups) if out else None,
        }
    finally:
        db.close()


# --- Celery wrappers -------------------------------------------------------
@app.task(bind=True, acks_late=True, reject_on_worker_lost=True)
def alias_build_task(self, novel_id: int, chapter_num: int | None = None) -> dict[str, Any]:
    return run_alias_build_once(novel_id, chapter_num=chapter_num)


@app.task(bind=True, acks_late=True, reject_on_worker_lost=True)
def volume_brief_distill_task(self, novel_id: int, volume_no: int | None = None) -> dict[str, Any]:
    return run_volume_brief_distill_once(novel_id, volume_no=volume_no)


@app.task(bind=True, acks_late=True, reject_on_worker_lost=True)
def hybrid_search_sync_task(self, novel_id: int) -> dict[str, Any]:
    return run_hybrid_search_sync_once(novel_id)


@app.task(bind=True, acks_late=True, reject_on_worker_lost=True)
def rerank_warmup_task(self, novel_id: int) -> dict[str, Any]:
    return run_rerank_warmup_once(novel_id)


@app.task(bind=True, acks_late=True, reject_on_worker_lost=True)
def spacetime_extract_task(self, novel_id: int, chapter_num: int) -> dict[str, Any]:
    return run_spacetime_extract_once(novel_id, chapter_num=chapter_num)


@app.task(bind=True, acks_late=True, reject_on_worker_lost=True)
def voice_drift_task(self, novel_id: int, chapter_num: int) -> dict[str, Any]:
    return run_voice_drift_once(novel_id, chapter_num=chapter_num)


@app.task(bind=True, acks_late=True, reject_on_worker_lost=True)
def foreshadow_lifecycle_task(self, novel_id: int, chapter_num: int) -> dict[str, Any]:
    return run_foreshadow_lifecycle_once(novel_id, chapter_num=chapter_num)


@app.task(bind=True, acks_late=True, reject_on_worker_lost=True)
def outline_audit_task(self, novel_id: int, chapter_num: int) -> dict[str, Any]:
    return run_outline_audit_once(novel_id, chapter_num=chapter_num)


@app.task(bind=True, acks_late=True, reject_on_worker_lost=True)
def precision_rewrite_task(self, novel_id: int, chapter_num: int) -> dict[str, Any]:
    return run_precision_rewrite_once(novel_id, chapter_num=chapter_num)


@app.task(bind=True, acks_late=True, reject_on_worker_lost=True)
def context_embedding_score_task(self, novel_id: int, chapter_num: int) -> dict[str, Any]:
    return run_context_embedding_score_once(novel_id, chapter_num=chapter_num)


@app.task(bind=True, acks_late=True, reject_on_worker_lost=True)
def reader_lens_task(self, novel_id: int, chapter_num: int) -> dict[str, Any]:
    return run_reader_lens_once(novel_id, chapter_num=chapter_num)
