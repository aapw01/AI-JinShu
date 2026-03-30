"""Celery task for chapter rewrite workflow."""
from __future__ import annotations

import json
import logging

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.llm_contract import invoke_chapter_body_structured
from app.core.llm_contract import get_last_prompt_meta
from app.core.llm_usage import begin_usage_session, end_usage_session, snapshot_usage
from app.core.logging_config import bind_log_context, log_event
from app.core.strategy import resolve_ai_profile
from app.models.novel import ChapterVersion, Novel, NovelVersion, RewriteRequest
from app.prompts import render_prompt
from app.services.generation.agents import FinalizerAgent
from app.services.generation.common import (
    normalize_chapter_content,
    resolve_chapter_title,
)
from app.services.generation.contracts import OutputContractError
from app.services.generation.length_control import (
    build_chapter_length_prompt_kwargs,
    maybe_compact_chapter_length,
)
from app.services.rewrite.service import group_annotations_by_chapter
from app.services.scheduler.scheduler_service import (
    dispatch_user_queue_for_user,
    heartbeat_task as heartbeat_creation_task,
    finalize_task as finalize_creation_task,
    get_task_by_id as get_creation_task_by_id,
    mark_task_running as mark_creation_task_running,
    update_task_progress as update_creation_task_progress,
)
from app.core.constants import CREATION_WORKER_HEARTBEAT_SECONDS
from app.models.creation_task import CreationTask
from app.services.task_runtime.checkpoint_repo import (
    get_last_completed_unit,
    mark_unit_completed,
    update_resume_cursor,
)
from app.services.task_runtime.cursor_service import resume_from_last_completed
from app.services.task_runtime.lease_service import background_heartbeat
from app.workers.celery_app import app

logger = logging.getLogger(__name__)


def _error_meta_from_exc(exc: Exception) -> tuple[str, str, bool]:
    if isinstance(exc, OutputContractError):
        if exc.code == "MODEL_OUTPUT_POLICY_VIOLATION":
            return exc.code, "policy", bool(exc.retryable)
        if exc.code in {"MODEL_OUTPUT_PARSE_FAILED", "MODEL_OUTPUT_SCHEMA_INVALID", "MODEL_OUTPUT_CONTRACT_EXHAUSTED"}:
            return exc.code, "transient", bool(exc.retryable)
        return exc.code, "transient", bool(exc.retryable)
    return "REWRITE_TASK_FAILED", "transient", True


def _get_creation_task_state(task_db_id: int) -> str | None:
    db = SessionLocal()
    try:
        row = get_creation_task_by_id(db, task_id=task_db_id)
        return row.status if row else None
    finally:
        db.close()


def _check_worker_superseded(task_db_id: int, current_celery_id: str) -> None:
    db = SessionLocal()
    try:
        row = get_creation_task_by_id(db, task_id=task_db_id)
        if not row:
            return
        if row.worker_task_id and row.worker_task_id != current_celery_id:
            raise RuntimeError(
                f"worker superseded: creation_task.worker_task_id={row.worker_task_id}, "
                f"current={current_celery_id}"
            )
    finally:
        db.close()


def _activate_creation_task(task_db_id: int, *, current_celery_id: str) -> None:
    db = SessionLocal()
    try:
        row = get_creation_task_by_id(db, task_id=task_db_id)
        if not row:
            raise RuntimeError("creation_task_not_found")
        if row.worker_task_id and row.worker_task_id != current_celery_id:
            raise RuntimeError(
                f"worker superseded: creation_task.worker_task_id={row.worker_task_id}, "
                f"current={current_celery_id}"
            )
        state = str(row.status or "")
        if state == "cancelled":
            raise RuntimeError("rewrite_cancelled")
        if state == "paused":
            raise RuntimeError("rewrite_paused")
        if state not in {"dispatching", "running"}:
            raise RuntimeError(f"rewrite_invalid_start:{state or 'unknown'}")
        mark_creation_task_running(db, task_id=task_db_id, worker_task_id=current_celery_id)
        db.commit()
    finally:
        db.close()


def _update_creation_progress(task_db_id: int, *, progress: float, message: str, phase: str = "rewrite") -> None:
    db = SessionLocal()
    try:
        usage = snapshot_usage()
        update_creation_task_progress(
            db,
            task_id=task_db_id,
            progress=progress,
            message=message,
            phase=phase,
            token_usage_input=int(usage.get("input_tokens") or 0),
            token_usage_output=int(usage.get("output_tokens") or 0),
            estimated_cost=float(usage.get("estimated_cost") or 0.0),
        )
        db.commit()
    finally:
        db.close()


def _heartbeat_creation(task_db_id: int) -> None:
    db = SessionLocal()
    try:
        heartbeat_creation_task(db, task_id=task_db_id)
        db.commit()
    finally:
        db.close()


def _mark_rewrite_checkpoint(task_db_id: int, *, chapter_num: int) -> None:
    db = SessionLocal()
    try:
        mark_unit_completed(
            db,
            creation_task_id=task_db_id,
            unit_type="chapter",
            unit_no=int(chapter_num),
            payload={"phase": "rewrite", "chapter_num": int(chapter_num)},
        )
        last_completed = get_last_completed_unit(db, creation_task_id=task_db_id, unit_type="chapter")
        update_resume_cursor(
            db,
            creation_task_id=task_db_id,
            unit_type="chapter",
            last_completed_unit_no=last_completed,
            next_unit_no=int(last_completed or 0) + 1,
        )
        db.commit()
    finally:
        db.close()


def _resolve_rewrite_resume(task_db_id: int, *, rewrite_from: int, rewrite_to: int) -> int:
    db = SessionLocal()
    try:
        last_completed = get_last_completed_unit(
            db,
            creation_task_id=task_db_id,
            unit_type="chapter",
            unit_from=int(rewrite_from),
            unit_to=int(rewrite_to),
        )
        resume_from = resume_from_last_completed(
            range_start=int(rewrite_from),
            range_end=int(rewrite_to),
            last_completed=last_completed,
        )
        update_resume_cursor(
            db,
            creation_task_id=task_db_id,
            unit_type="chapter",
            last_completed_unit_no=last_completed,
            next_unit_no=int(resume_from),
        )
        db.commit()
        return int(resume_from)
    finally:
        db.close()


def _finalize_creation(
    task_db_id: int,
    *,
    final_status: str,
    progress: float,
    message: str,
    phase: str,
    error_code: str | None = None,
    error_category: str | None = None,
    error_detail: str | None = None,
    result_json: dict | None = None,
) -> None:
    db = SessionLocal()
    user_uuid: str | None = None
    try:
        row = finalize_creation_task(
            db,
            task_id=task_db_id,
            final_status=final_status,
            progress=progress,
            message=message,
            phase=phase,
            error_code=error_code,
            error_category=error_category,
            error_detail=error_detail,
            result_json=result_json,
        )
        user_uuid = row.user_uuid if row else None
        db.commit()
    finally:
        db.close()
    if user_uuid:
        dispatch_user_queue_for_user(user_uuid=user_uuid)


def _rewrite_prompt(
    novel_title: str,
    chapter_num: int,
    base_title: str | None,
    base_content: str,
    previous_context: str,
    annotations: list[dict],
    target_language: str,
) -> tuple[str, str]:
    template = "rewrite_chapter_from_annotations"
    ann_rows: list[dict[str, object]] = []
    ann_lines: list[str] = []
    for ann in annotations:
        snippet = (ann.get("selected_text") or "").strip()
        if len(snippet) > 80:
            snippet = snippet[:80] + "..."
        ann_rows.append(
            {
                "chapter_num": int(ann.get("chapter_num") or chapter_num),
                "start_offset": ann.get("start_offset"),
                "end_offset": ann.get("end_offset"),
                "selected_text": str(ann.get("selected_text") or ""),
                "issue_type": str(ann.get("issue_type") or "other"),
                "priority": str(ann.get("priority") or "should"),
                "instruction": str(ann.get("instruction") or ""),
            }
        )
        ann_lines.append(
            f"[优先级:{ann.get('priority','should')}] [类型:{ann.get('issue_type','other')}] "
            f"片段:{snippet or '未选中片段'} 指令:{ann.get('instruction','')}"
        )
    context_block = previous_context or "无"
    annotations_json = json.dumps(ann_rows, ensure_ascii=False, indent=2)
    ann_text = "\n".join(ann_lines) if ann_lines else "无"

    prompt_text = render_prompt(
        template,
        novel_title=novel_title,
        chapter_num=chapter_num,
        base_title=(base_title or ""),
        target_language=target_language,
        context_block=context_block,
        annotations_json=annotations_json,
        ann_text=ann_text,
        base_content=base_content,
        **build_chapter_length_prompt_kwargs(),
    )
    return prompt_text, template


def _collect_previous_context(db, target_version_id: int, chapter_num: int) -> str:
    rows = db.execute(
        select(ChapterVersion)
        .where(
            ChapterVersion.novel_version_id == target_version_id,
            ChapterVersion.chapter_num < chapter_num,
        )
        .order_by(ChapterVersion.chapter_num.desc())
        .limit(2)
    ).scalars().all()
    rows = list(reversed(rows))
    if not rows:
        return ""
    parts: list[str] = []
    for row in rows:
        title = row.title or f"第{row.chapter_num}章"
        content = (row.content or "")[:500]
        parts.append(f"- {title}: {content}")
    return "\n".join(parts)


@app.task(bind=True, acks_late=True, reject_on_worker_lost=True)
def submit_rewrite_task(
    self,
    novel_id: int,
    rewrite_request_id: int,
    base_version_id: int,
    target_version_id: int,
    rewrite_from: int,
    rewrite_to: int,
    creation_task_id: int | None = None,
):
    # Load prior token totals so resume continues from the correct baseline.
    _prior_in, _prior_out = 0, 0
    if creation_task_id is not None:
        _db_tmp = SessionLocal()
        try:
            _ct_row = _db_tmp.execute(
                select(CreationTask).where(CreationTask.id == creation_task_id)
            ).scalar_one_or_none()
            if _ct_row and isinstance(_ct_row.result_json, dict):
                _prior_in = int(_ct_row.result_json.get("token_usage_input") or 0)
                _prior_out = int(_ct_row.result_json.get("token_usage_output") or 0)
        finally:
            _db_tmp.close()
    begin_usage_session(f"rewrite:{self.request.id}", base_input=_prior_in, base_output=_prior_out)
    hb_ctx = background_heartbeat(creation_task_id, heartbeat_fn=_heartbeat_creation, interval_seconds=CREATION_WORKER_HEARTBEAT_SECONDS)
    hb_ctx.__enter__()
    _worker_superseded = False
    db = SessionLocal()
    try:
        if creation_task_id is not None:
            _activate_creation_task(creation_task_id, current_celery_id=self.request.id)
            rewrite_from = _resolve_rewrite_resume(
                creation_task_id,
                rewrite_from=int(rewrite_from),
                rewrite_to=int(rewrite_to),
            )
            if int(rewrite_from) > int(rewrite_to):
                _finalize_creation(
                    creation_task_id,
                    final_status="completed",
                    progress=100.0,
                    message="重写完成（已无待处理章节）",
                    phase="completed",
                )
                return

        novel = db.execute(select(Novel).where(Novel.id == novel_id)).scalar_one_or_none()
        req = db.execute(select(RewriteRequest).where(RewriteRequest.id == rewrite_request_id)).scalar_one_or_none()
        target_version = db.execute(select(NovelVersion).where(NovelVersion.id == target_version_id)).scalar_one_or_none()
        if not novel or not req or not target_version:
            raise RuntimeError("rewrite target not found")

        req.status = "running"
        req.error = None
        target_version.status = "generating"
        db.commit()
        with bind_log_context(task_id=self.request.id, novel_id=novel_id):
            log_event(
                logger,
                "rewrite.task.started",
                task_id=self.request.id,
                novel_id=novel_id,
                chapter_num=rewrite_from,
                run_state="running",
                base_version_id=base_version_id,
                target_version_id=target_version_id,
            )

        strategy = novel.strategy or "web-novel"
        writer_profile = resolve_ai_profile(strategy, "rewrite", novel_config=dict(novel.config or {}))
        finalizer_profile = resolve_ai_profile(strategy, "finalizer", novel_config=dict(novel.config or {}))
        provider, model = writer_profile["provider"], writer_profile["model"]
        writer_inference = writer_profile["inference"]
        finalizer_provider, finalizer_model = finalizer_profile["provider"], finalizer_profile["model"]
        finalizer_inference = finalizer_profile["inference"]
        rewrite_finalizer = FinalizerAgent()
        annotations_by_chapter = group_annotations_by_chapter(db, rewrite_request_id)

        total = max(1, rewrite_to - rewrite_from + 1)
        for chapter_num in range(rewrite_from, rewrite_to + 1):
            if creation_task_id is not None:
                try:
                    _heartbeat_creation(creation_task_id)
                except Exception:
                    pass
                _check_worker_superseded(creation_task_id, self.request.id)
                c_state = _get_creation_task_state(creation_task_id)
                if c_state == "cancelled":
                    raise RuntimeError("rewrite_cancelled")
                if c_state == "paused":
                    raise RuntimeError("rewrite_paused")
            log_event(
                logger,
                "rewrite.chapter.start",
                task_id=self.request.id,
                novel_id=novel_id,
                chapter_num=chapter_num,
                base_version_id=base_version_id,
                target_version_id=target_version_id,
            )
            base_chapter = db.execute(
                select(ChapterVersion)
                .where(
                    ChapterVersion.novel_version_id == base_version_id,
                    ChapterVersion.chapter_num == chapter_num,
                )
            ).scalar_one_or_none()
            if not base_chapter:
                raise RuntimeError(f"base chapter missing: {chapter_num}")

            ann_rows = annotations_by_chapter.get(chapter_num, [])
            annotations = [
                {
                    "selected_text": x.selected_text,
                    "issue_type": x.issue_type,
                    "instruction": x.instruction,
                    "priority": x.priority,
                }
                for x in ann_rows
            ]
            prev_context = _collect_previous_context(db, target_version_id, chapter_num)
            prompt, prompt_template = _rewrite_prompt(
                novel_title=novel.title,
                chapter_num=chapter_num,
                base_title=base_chapter.title,
                base_content=base_chapter.content or "",
                previous_context=prev_context,
                annotations=annotations,
                target_language=novel.target_language or "zh",
            )
            try:
                rewritten = invoke_chapter_body_structured(
                    prompt=prompt,
                    stage="rewrite",
                    chapter_num=chapter_num,
                    provider=provider,
                    model=model,
                    inference=writer_inference,
                    prompt_template=prompt_template,
                    prompt_version="v2",
                )
            except OutputContractError as e:
                log_event(
                    logger,
                    "rewrite.chapter.contract_error",
                    level=logging.ERROR,
                    task_id=self.request.id,
                    novel_id=novel_id,
                    chapter_num=chapter_num,
                    error_code=e.code,
                    error_category="policy" if e.code == "MODEL_OUTPUT_POLICY_VIOLATION" else "transient",
                    error_class=type(e).__name__,
                )
                raise
            except Exception as e:
                log_event(
                    logger,
                    "rewrite.chapter.llm_error",
                    level=logging.ERROR,
                    task_id=self.request.id,
                    novel_id=novel_id,
                    chapter_num=chapter_num,
                    error_class=type(e).__name__,
                    error_category="transient",
                )
                raise RuntimeError(f"重写第{chapter_num}章时LLM调用失败: {type(e).__name__}: {e}") from e

            if not rewritten:
                rewritten = (base_chapter.content or "").strip()
            rewritten = normalize_chapter_content(rewritten)
            rewritten, length_diagnostics = maybe_compact_chapter_length(
                content=rewritten,
                compact_fn=lambda draft, feedback: rewrite_finalizer.run(
                    draft=draft,
                    feedback=feedback,
                    language=novel.target_language or "zh",
                    provider=finalizer_provider,
                    model=finalizer_model,
                    inference=finalizer_inference,
                ),
                normalize_fn=normalize_chapter_content,
            )
            if bool(length_diagnostics.get("length_compaction_attempted")):
                log_event(
                    logger,
                    "rewrite.chapter.length_compaction",
                    task_id=self.request.id,
                    novel_id=novel_id,
                    chapter_num=chapter_num,
                    before=length_diagnostics.get("word_count_before_compaction"),
                    after=length_diagnostics.get("word_count_after_compaction"),
                    applied=length_diagnostics.get("length_compaction_applied"),
                    reason=length_diagnostics.get("length_compaction_reason"),
                )
            chapter_title = resolve_chapter_title(
                chapter_num=chapter_num,
                title=base_chapter.title,
                summary=base_chapter.summary,
                content=rewritten,
            )

            target_chapter = db.execute(
                select(ChapterVersion)
                .where(
                    ChapterVersion.novel_version_id == target_version_id,
                    ChapterVersion.chapter_num == chapter_num,
                )
            ).scalar_one_or_none()
            if target_chapter:
                target_chapter.title = chapter_title
                target_chapter.content = rewritten
                target_chapter.summary = base_chapter.summary
                target_chapter.status = "completed"
                target_chapter.metadata_ = {
                    **(target_chapter.metadata_ or {}),
                    "rewrite_request_id": rewrite_request_id,
                    "based_on_version": base_version_id,
                    "word_count_before_compaction": int(length_diagnostics.get("word_count_before_compaction") or 0),
                    "word_count_after_compaction": int(length_diagnostics.get("word_count_after_compaction") or 0),
                    "length_compaction_attempted": bool(length_diagnostics.get("length_compaction_attempted")),
                    "length_compaction_applied": bool(length_diagnostics.get("length_compaction_applied")),
                    "length_compaction_reason": str(length_diagnostics.get("length_compaction_reason") or ""),
                }
            else:
                db.add(
                    ChapterVersion(
                        novel_version_id=target_version_id,
                        chapter_num=chapter_num,
                        title=chapter_title,
                        content=rewritten,
                        summary=base_chapter.summary,
                        status="completed",
                        source_chapter_version_id=base_chapter.id,
                        metadata_={
                            "rewrite_request_id": rewrite_request_id,
                            "based_on_version": base_version_id,
                            "word_count_before_compaction": int(length_diagnostics.get("word_count_before_compaction") or 0),
                            "word_count_after_compaction": int(length_diagnostics.get("word_count_after_compaction") or 0),
                            "length_compaction_attempted": bool(length_diagnostics.get("length_compaction_attempted")),
                            "length_compaction_applied": bool(length_diagnostics.get("length_compaction_applied")),
                            "length_compaction_reason": str(length_diagnostics.get("length_compaction_reason") or ""),
                        },
                    )
                )

            finished = chapter_num - rewrite_from + 1
            req.current_chapter = chapter_num
            req.progress = round((finished / total) * 100, 2)
            req.message = f"重写中：第{chapter_num}章"
            db.commit()
            if creation_task_id is not None:
                _mark_rewrite_checkpoint(creation_task_id, chapter_num=chapter_num)
                _update_creation_progress(
                    creation_task_id,
                    progress=req.progress,
                    message=req.message or "重写中",
                    phase="rewrite",
                )
            log_event(
                logger,
                "rewrite.chapter.end",
                task_id=self.request.id,
                novel_id=novel_id,
                chapter_num=chapter_num,
                progress=req.progress,
            )

        req.status = "completed"
        req.progress = 100.0
        req.message = "重写完成"
        target_version.status = "completed"

        all_versions = db.execute(select(NovelVersion).where(NovelVersion.novel_id == novel_id)).scalars().all()
        for version in all_versions:
            version.is_default = 1 if version.id == target_version_id else 0
        db.commit()
        log_event(
            logger,
            "rewrite.completed",
            task_id=self.request.id,
            novel_id=novel_id,
            chapter_num=rewrite_to,
            run_state="completed",
        )
        if creation_task_id is not None:
            usage = snapshot_usage()
            prompt_meta = get_last_prompt_meta() or {}
            _finalize_creation(
                creation_task_id,
                final_status="completed",
                progress=100.0,
                message="重写完成",
                phase="completed",
                result_json={
                    "token_usage_input": int(usage.get("input_tokens") or 0),
                    "token_usage_output": int(usage.get("output_tokens") or 0),
                    "estimated_cost": float(usage.get("estimated_cost") or 0.0),
                    "usage_calls": int(usage.get("calls") or 0),
                    "usage_stages": usage.get("stages") or {},
                    "prompt_version": str(prompt_meta.get("prompt_version") or "v2"),
                    "prompt_hash": prompt_meta.get("prompt_hash"),
                    "prompt_template": prompt_meta.get("prompt_template"),
                },
            )
    except Exception as exc:
        err_text = str(exc)
        _worker_superseded = "worker superseded" in err_text
        if _worker_superseded:
            logger.warning("Rewrite worker superseded, exiting gracefully: %s", err_text)
            db.rollback()
        else:
            db.rollback()
            is_paused = err_text == "rewrite_paused"
            is_cancelled = err_text == "rewrite_cancelled"
            error_code, error_category, retryable = _error_meta_from_exc(exc)
            log_event(
                logger,
                "rewrite.failed" if not (is_paused or is_cancelled) else "rewrite.stopped",
                level=logging.ERROR if not (is_paused or is_cancelled) else logging.WARNING,
                task_id=self.request.id,
                novel_id=novel_id,
                run_state="failed" if not (is_paused or is_cancelled) else ("paused" if is_paused else "cancelled"),
                error_class=type(exc).__name__,
                error_code=None if (is_paused or is_cancelled) else error_code,
                error_category=None if (is_paused or is_cancelled) else error_category,
                retryable=retryable if not (is_paused or is_cancelled) else None,
            )
            req = db.execute(select(RewriteRequest).where(RewriteRequest.id == rewrite_request_id)).scalar_one_or_none()
            if req:
                req.status = "paused" if is_paused else ("cancelled" if is_cancelled else "failed")
                req.error = None if (is_paused or is_cancelled) else str(exc)
                req.message = "重写任务已暂停" if is_paused else ("重写任务已取消" if is_cancelled else "重写失败")
            target_version = db.execute(select(NovelVersion).where(NovelVersion.id == target_version_id)).scalar_one_or_none()
            if target_version and not is_paused:
                target_version.status = "failed"
            db.commit()
            if creation_task_id is not None:
                try:
                    last = get_last_completed_unit(db, creation_task_id=creation_task_id, unit_type="chapter")
                    if last is not None:
                        update_resume_cursor(
                            db,
                            creation_task_id=creation_task_id,
                            unit_type="chapter",
                            last_completed_unit_no=last,
                            next_unit_no=int(last) + 1,
                        )
                        db.commit()
                except Exception:
                    logger.warning("Failed to update rewrite resume_cursor on failure", exc_info=True)
                try:
                    usage = snapshot_usage()
                    prompt_meta = get_last_prompt_meta() or {}
                    _finalize_creation(
                        creation_task_id,
                        final_status="paused" if is_paused else ("cancelled" if is_cancelled else "failed"),
                        progress=float(req.progress if req else 0.0),
                        message=str(req.message if req else "任务结束"),
                        phase="paused" if is_paused else ("cancelled" if is_cancelled else "failed"),
                        error_code=None if (is_paused or is_cancelled) else error_code,
                        error_category=None if (is_paused or is_cancelled) else error_category,
                        error_detail=None if (is_paused or is_cancelled) else str(exc),
                        result_json={
                            "token_usage_input": int(usage.get("input_tokens") or 0),
                            "token_usage_output": int(usage.get("output_tokens") or 0),
                            "estimated_cost": float(usage.get("estimated_cost") or 0.0),
                            "usage_calls": int(usage.get("calls") or 0),
                            "usage_stages": usage.get("stages") or {},
                            "prompt_version": str(prompt_meta.get("prompt_version") or "v2"),
                            "prompt_hash": prompt_meta.get("prompt_hash"),
                            "prompt_template": prompt_meta.get("prompt_template"),
                        },
                    )
                except Exception:
                    logger.exception("Failed to finalize creation task %s", creation_task_id)
            if not (is_paused or is_cancelled):
                raise
    finally:
        hb_ctx.__exit__(None, None, None)
        db.close()
        end_usage_session()
