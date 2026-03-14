"""Celery tasks for storyboard generation."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

import redis
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.llm_usage import begin_usage_session, end_usage_session, snapshot_usage
from app.core.logging_config import log_event
from app.models.novel import Novel
from app.models.storyboard import (
    StoryboardExport,
    StoryboardGateReport,
    StoryboardProject,
    StoryboardRun,
    StoryboardRunLane,
    StoryboardShot,
    StoryboardSourceSnapshot,
    StoryboardTask,
    StoryboardVersion,
)
from app.prompts import render_prompt
from app.services.scheduler.scheduler_service import (
    finalize_task as finalize_creation_task,
    get_task_by_id as get_creation_task_by_id,
    heartbeat_task as heartbeat_creation_task,
    mark_task_running as mark_creation_task_running,
    update_task_progress as update_creation_task_progress,
)
from app.services.task_runtime.checkpoint_repo import (
    get_completed_units,
    mark_unit_completed,
    update_resume_cursor,
)
from app.services.task_runtime.lease_service import background_heartbeat
from app.services.storyboard.character_prompts import compose_character_prompts_for_version
from app.services.storyboard.export_v2 import render_export_blob, save_export_blob
from app.services.storyboard.runtime_v2 import (
    persist_character_cards,
    refresh_run_status,
    update_run_lane_state,
)
from app.services.storyboard.service import (
    build_quality_report,
    generate_lane_shots,
    load_novel_chapters,
    persist_episode_shots,
    project_config,
    set_default_version,
    update_task_state,
)
from app.services.storyboard.adapter import AdaptedChapter
from app.workers.celery_app import app

logger = logging.getLogger(__name__)


_redis_pool = None


def _get_redis() -> redis.Redis:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = redis.ConnectionPool.from_url(get_settings().redis_url)
    return redis.Redis(connection_pool=_redis_pool)


def _redis_key(task_id: str) -> str:
    return f"storyboard:task:{task_id}"


def _publish(task: StoryboardTask) -> None:
    payload = {
        "storyboard_project_id": task.storyboard_project_id,
        "task_id": task.task_id,
        "status": task.status,
        "run_state": task.run_state,
        "current_phase": task.current_phase,
        "current_lane": task.current_lane,
        "progress": float(task.progress or 0.0),
        "current_episode": task.current_episode,
        "eta_seconds": task.eta_seconds,
        "message": task.message,
        "error": task.error,
        "error_code": task.error_code,
        "error_category": task.error_category,
        "retryable": bool(task.retryable),
    }
    _get_redis().setex(_redis_key(task.task_id), 3600 * 6, json.dumps(payload, ensure_ascii=False))


def _reload_task(db, task_db_id: int) -> StoryboardTask | None:
    return db.execute(select(StoryboardTask).where(StoryboardTask.id == task_db_id)).scalar_one_or_none()


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


def _mark_creation_running(task_db_id: int) -> None:
    db = SessionLocal()
    try:
        mark_creation_task_running(db, task_id=task_db_id)
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


def _update_creation_progress(task_db_id: int, *, progress: float, phase: str, message: str) -> None:
    db = SessionLocal()
    try:
        usage = snapshot_usage()
        update_creation_task_progress(
            db,
            task_id=task_db_id,
            progress=progress,
            phase=phase,
            message=message,
            token_usage_input=int(usage.get("input_tokens") or 0),
            token_usage_output=int(usage.get("output_tokens") or 0),
            estimated_cost=float(usage.get("estimated_cost") or 0.0),
        )
        db.commit()
    finally:
        db.close()


def _finalize_creation(
    task_db_id: int,
    *,
    final_status: str,
    progress: float,
    phase: str,
    message: str,
    error_code: str | None = None,
    error_category: str | None = None,
    error_detail: str | None = None,
    result_json: dict | None = None,
) -> None:
    db = SessionLocal()
    try:
        finalize_creation_task(
            db,
            task_id=task_db_id,
            final_status=final_status,
            progress=progress,
            phase=phase,
            message=message,
            error_code=error_code,
            error_category=error_category,
            error_detail=error_detail,
            result_json=result_json,
        )
        db.commit()
    finally:
        db.close()


@app.task(bind=True, acks_late=True, reject_on_worker_lost=True)
def run_storyboard_pipeline(
    self,
    *,
    project_id: int,
    novel_version_id: int | None = None,
    task_db_id: int | None = None,
    version_ids: list[int],
    creation_task_id: int | None = None,
):
    begin_usage_session(f"storyboard:{self.request.id}")
    from app.core.constants import CREATION_WORKER_HEARTBEAT_SECONDS
    hb_ctx = background_heartbeat(creation_task_id, heartbeat_fn=_heartbeat_creation, interval_seconds=CREATION_WORKER_HEARTBEAT_SECONDS)
    hb_ctx.__enter__()
    _worker_superseded = False
    db = SessionLocal()
    try:
        project = db.execute(select(StoryboardProject).where(StoryboardProject.id == project_id)).scalar_one_or_none()
        task = _reload_task(db, task_db_id) if task_db_id is not None else None
        if not project:
            logger.warning("Storyboard project %s no longer exists (likely deleted), marking task as permanently failed", project_id)
            if creation_task_id is not None:
                _finalize_creation(
                    creation_task_id,
                    final_status="failed",
                    progress=0.0,
                    phase="failed",
                    message="分镜项目已不存在（可能已被删除）",
                    error_code="STORYBOARD_PROJECT_NOT_FOUND",
                    error_category="permanent",
                )
            return
        if creation_task_id is not None:
            _check_worker_superseded(creation_task_id, self.request.id)
            c_status = _get_creation_task_state(creation_task_id)
            if c_status not in {"dispatching", "running"}:
                logger.info("Skip storyboard execution because creation_task status=%s", c_status)
                return
            _mark_creation_running(creation_task_id)
        novel = db.execute(select(Novel).where(Novel.id == project.novel_id)).scalar_one_or_none()
        if not novel:
            raise RuntimeError("novel not found for storyboard")

        versions = db.execute(
            select(StoryboardVersion)
            .where(StoryboardVersion.id.in_(version_ids))
            .order_by(StoryboardVersion.id.asc())
        ).scalars().all()
        if not versions:
            raise RuntimeError("storyboard versions not found")

        effective_novel_version_id = int(novel_version_id) if novel_version_id is not None else None
        if effective_novel_version_id is None and versions:
            candidate = versions[0].source_novel_version_id
            effective_novel_version_id = int(candidate) if candidate is not None else None
        chapters = load_novel_chapters(db, novel.id, effective_novel_version_id)
        if not chapters:
            raise RuntimeError("novel has no chapters to adapt")

        if task:
            update_task_state(db, task, status="running", run_state="running", phase="extract_novel_structure", progress=2.0, message="正在提取小说结构")
        if creation_task_id is not None:
            _update_creation_progress(
                creation_task_id,
                progress=2.0,
                phase="extract_novel_structure",
                message="正在提取小说结构",
            )
        project.status = "generating"
        db.commit()
        if task:
            _publish(task)
        log_event(
            logger,
            "storyboard.phase.start",
            task_id=(task.task_id if task else self.request.id),
            novel_id=novel.id,
            storyboard_project_id=project.id,
            phase="extract_novel_structure",
        )

        total_lanes = max(1, len(versions))
        lane_reports: dict[str, dict] = {}
        cfg = project_config(project)
        for idx, version in enumerate(versions, start=1):
            if task_db_id is not None:
                task = _reload_task(db, task_db_id)
                if not task:
                    raise RuntimeError("task missing while running")
                if creation_task_id is not None:
                    _heartbeat_creation(creation_task_id)
                while task.run_state == "paused":
                    update_task_state(db, task, status="paused", run_state="paused", phase="paused", message="分镜生成已暂停")
                    db.commit()
                    _publish(task)
                    if creation_task_id is not None:
                        _heartbeat_creation(creation_task_id)
                    time.sleep(1.2)
                    task = _reload_task(db, task_db_id)
                    if not task:
                        raise RuntimeError("task missing while paused")
                if task.run_state == "cancelled":
                    update_task_state(db, task, status="cancelled", run_state="cancelled", phase="cancelled", message="任务已取消")
                    for v in versions:
                        v.status = "failed"
                    project.status = "failed"
                    db.commit()
                    _publish(task)
                    if creation_task_id is not None:
                        try:
                            _finalize_creation(creation_task_id, final_status="cancelled", phase="cancelled", message="任务已取消", progress=0.0)
                        except Exception:
                            logger.exception("Failed to finalize creation task %s on cancel", creation_task_id)
                    return
            elif creation_task_id is not None:
                _heartbeat_creation(creation_task_id)
                _check_worker_superseded(creation_task_id, self.request.id)
                c_state = _get_creation_task_state(creation_task_id)
                if c_state == "cancelled":
                    for v in versions:
                        v.status = "failed"
                    project.status = "failed"
                    db.commit()
                    try:
                        _finalize_creation(creation_task_id, final_status="cancelled", phase="cancelled", message="任务已取消", progress=0.0)
                    except Exception:
                        logger.exception("Failed to finalize creation task %s on cancel", creation_task_id)
                    return
                if c_state == "paused":
                    raise RuntimeError("storyboard_paused")
            lane = version.lane
            lane_progress_base = round((idx - 1) * 100 / total_lanes, 2)
            if task:
                update_task_state(
                    db,
                    task,
                    status="running",
                    run_state="running",
                    phase="shot_expand",
                    lane=lane,
                    progress=lane_progress_base,
                    message=f"正在生成 {('竖屏版' if lane == 'vertical_feed' else '横屏版')} 分镜",
                )
            if creation_task_id is not None:
                _update_creation_progress(
                    creation_task_id,
                    progress=lane_progress_base,
                    phase="shot_expand",
                    message=f"正在生成 {('竖屏版' if lane == 'vertical_feed' else '横屏版')} 分镜",
                )
            db.commit()
            if task:
                _publish(task)
            log_event(
                logger,
                "storyboard.phase.start",
                task_id=(task.task_id if task else self.request.id),
                novel_id=novel.id,
                storyboard_project_id=project.id,
                current_lane=lane,
                phase="shot_expand",
            )
            completed_chapters: set[int] = set()
            if creation_task_id is not None:
                completed_chapters = get_completed_units(
                    db,
                    creation_task_id=creation_task_id,
                    unit_type="chapter",
                    partition=lane,
                )
            total_chapters = max(1, len(chapters))
            shot_count = 0
            for chapter_idx, chapter in enumerate(chapters, start=1):
                chapter_no = int(chapter.chapter_num)
                if chapter_no in completed_chapters:
                    continue
                if creation_task_id is not None:
                    _heartbeat_creation(creation_task_id)
                    _check_worker_superseded(creation_task_id, self.request.id)
                    c_state = _get_creation_task_state(creation_task_id)
                    if c_state == "cancelled":
                        raise RuntimeError("storyboard_cancelled")
                    if c_state == "paused":
                        raise RuntimeError("storyboard_paused")
                single_shots, _, _ = generate_lane_shots(
                    lane=lane,
                    novel=novel,
                    chapters=[chapter],
                    target_episodes=1,
                    target_episode_seconds=project.target_episode_seconds,
                    style_profile=project.style_profile,
                    mode=cfg["mode"],
                    genre_style_key=cfg["genre_style_key"],
                    director_style_key=cfg["director_style_key"],
                )
                for s in single_shots:
                    s.episode_no = chapter_no
                shot_count += persist_episode_shots(db, version.id, chapter_no, single_shots)
                if creation_task_id is not None:
                    mark_unit_completed(
                        db,
                        creation_task_id=creation_task_id,
                        unit_type="chapter",
                        unit_no=chapter_no,
                        partition=lane,
                        payload={"lane": lane, "chapter_num": chapter_no, "phase": "shot_expand"},
                    )
                    update_resume_cursor(
                        db,
                        creation_task_id=creation_task_id,
                        unit_type="chapter",
                        partition=lane,
                        last_completed_unit_no=chapter_no,
                        next_unit_no=chapter_no + 1,
                    )
                    blended_progress = lane_progress_base + (chapter_idx / total_chapters) * (100 / total_lanes)
                    _update_creation_progress(
                        creation_task_id,
                        progress=round(min(99.0, blended_progress), 2),
                        phase="shot_expand",
                        message=f"{lane} 已完成第{chapter_no}章分镜",
                    )
                db.commit()
            if creation_task_id is not None:
                _heartbeat_creation(creation_task_id)
            _, contract, quality = generate_lane_shots(
                lane=lane,
                novel=novel,
                chapters=chapters,
                target_episodes=project.target_episodes,
                target_episode_seconds=project.target_episode_seconds,
                style_profile=project.style_profile,
                mode=cfg["mode"],
                genre_style_key=cfg["genre_style_key"],
                director_style_key=cfg["director_style_key"],
            )
            quality_report = build_quality_report(lane=lane, quality=quality, prompt_contract_json=contract)
            version.quality_report_json = quality_report
            if quality.style_consistency_score < 0.75:
                version.status = "failed"
                if task:
                    update_task_state(
                        db,
                        task,
                        status="failed",
                        run_state="failed",
                        phase="style_consistency_gate",
                        lane=lane,
                        progress=round((idx * 100) / total_lanes, 2),
                        message=f"{lane} 风格一致性未达标",
                        error="style consistency gate failed",
                        error_code="FAILED_STYLE_GATE",
                        error_category="policy",
                        retryable=1,
                        gate_report=quality_report,
                    )
                project.status = "failed"
                db.commit()
                if task:
                    _publish(task)
                log_event(
                    logger,
                    "storyboard.phase.error",
                    level=logging.WARNING,
                    task_id=(task.task_id if task else self.request.id),
                    novel_id=novel.id,
                    storyboard_project_id=project.id,
                    current_lane=lane,
                    phase="style_consistency_gate",
                    error_code="FAILED_STYLE_GATE",
                    error_category="policy",
                )
                if creation_task_id is not None:
                    try:
                        _finalize_creation(
                            creation_task_id, final_status="failed", phase="style_consistency_gate",
                            message=f"{lane} 风格一致性未达标", error_code="FAILED_STYLE_GATE",
                            error_category="policy", progress=round((idx * 100) / total_lanes, 2),
                        )
                    except Exception:
                        logger.exception("Failed to finalize creation task %s on style gate", creation_task_id)
                return

            if creation_task_id is not None:
                _heartbeat_creation(creation_task_id)
            if task:
                update_task_state(
                    db,
                    task,
                    status="running",
                    run_state="running",
                    phase="character_prompt_compose",
                    lane=lane,
                    progress=round((idx - 1) * 100 / total_lanes + 8.0, 2),
                    message=f"{lane} 正在生成人物主形象提示词",
                )
            db.commit()
            if task:
                _publish(task)
            character_report = compose_character_prompts_for_version(
                db=db,
                project=project,
                version=version,
                novel=novel,
                force_regenerate=True,
            )
            quality_report["character_prompt_phase"] = "character_prompt_compose"
            quality_report["character_profiles_count"] = int(character_report.get("profiles_count") or 0)
            quality_report["missing_identity_fields_count"] = int(character_report.get("missing_identity_fields_count") or 0)
            quality_report["failed_identity_characters"] = character_report.get("failed_identity_characters") or []
            version.quality_report_json = quality_report
            if int(character_report.get("missing_identity_fields_count") or 0) > 0:
                version.status = "failed"
                failed_rows = character_report.get("failed_identity_characters") or []
                if task:
                    update_task_state(
                        db,
                        task,
                        status="failed",
                        run_state="failed",
                        phase="character_identity_gate",
                        lane=lane,
                        progress=round((idx * 100) / total_lanes, 2),
                        message="角色身份字段缺失，门禁未通过",
                        error=render_prompt(
                            "storyboard_character_identity_gate_fail",
                            failed_rows_json=json.dumps(failed_rows[:20], ensure_ascii=False),
                        ).strip(),
                        error_code="FAILED_IDENTITY_REQUIRED",
                        error_category="policy",
                        retryable=1,
                        gate_report=quality_report,
                    )
                project.status = "failed"
                db.commit()
                if task:
                    _publish(task)
                log_event(
                    logger,
                    "storyboard.phase.error",
                    level=logging.WARNING,
                    task_id=(task.task_id if task else self.request.id),
                    novel_id=novel.id,
                    storyboard_project_id=project.id,
                    current_lane=lane,
                    phase="character_identity_gate",
                    error_code="FAILED_IDENTITY_REQUIRED",
                    error_category="policy",
                )
                if creation_task_id is not None:
                    try:
                        _finalize_creation(
                            creation_task_id, final_status="failed", phase="character_identity_gate",
                            message="角色身份字段缺失，门禁未通过", error_code="FAILED_IDENTITY_REQUIRED",
                            error_category="policy", progress=round((idx * 100) / total_lanes, 2),
                        )
                    except Exception:
                        logger.exception("Failed to finalize creation task %s on identity gate", creation_task_id)
                return

            version.status = "completed"
            lane_reports[lane] = quality_report
            progress = round((idx * 100) / total_lanes, 2)
            if task:
                update_task_state(
                    db,
                    task,
                    status="running",
                    run_state="running",
                    phase="quality_gate",
                    lane=lane,
                    progress=progress,
                    message=f"{lane} 已生成 {shot_count} 个镜头",
                    gate_report=quality_report,
                )
            if creation_task_id is not None:
                _update_creation_progress(
                    creation_task_id,
                    progress=progress,
                    phase="quality_gate",
                    message=f"{lane} 已生成 {shot_count} 个镜头",
                )
            db.commit()
            if task:
                _publish(task)
            log_event(
                logger,
                "storyboard.phase.end",
                task_id=(task.task_id if task else self.request.id),
                novel_id=novel.id,
                storyboard_project_id=project.id,
                current_lane=lane,
                phase="quality_gate",
                shot_count=shot_count,
                style_consistency_score=quality.style_consistency_score,
            )

        default_version = versions[0]
        set_default_version(db, project.id, default_version.id)
        project.status = "ready"
        project.active_lane = default_version.lane
        if task_db_id is not None:
            task = _reload_task(db, task_db_id)
            if not task:
                raise RuntimeError("task missing before finish")
            update_task_state(
                db,
                task,
                status="completed",
                run_state="completed",
                phase="completed",
                lane=default_version.lane,
                progress=100.0,
                message="导演分镜草案已生成",
                gate_report={
                    "style_consistency_score": min(
                        [r.get("style_consistency_score", 1.0) for r in lane_reports.values()] or [1.0]
                    ),
                    "hook_score_episode": lane_reports.get(default_version.lane, {}).get("hook_score_episode", {}),
                    "quality_gate_reasons": lane_reports.get(default_version.lane, {}).get("quality_gate_reasons", []),
                    "character_prompt_phase": lane_reports.get(default_version.lane, {}).get("character_prompt_phase"),
                    "character_profiles_count": lane_reports.get(default_version.lane, {}).get("character_profiles_count"),
                    "missing_identity_fields_count": lane_reports.get(default_version.lane, {}).get("missing_identity_fields_count"),
                    "failed_identity_characters": lane_reports.get(default_version.lane, {}).get("failed_identity_characters", []),
                },
                retryable=0,
            )
        db.commit()
        if task:
            _publish(task)
        if creation_task_id is not None:
            try:
                usage = snapshot_usage()
                _finalize_creation(
                    creation_task_id,
                    final_status="completed",
                    progress=100.0,
                    phase="completed",
                    message="导演分镜草案已生成",
                    result_json={
                        "token_usage_input": int(usage.get("input_tokens") or 0),
                        "token_usage_output": int(usage.get("output_tokens") or 0),
                        "estimated_cost": float(usage.get("estimated_cost") or 0.0),
                        "usage_calls": int(usage.get("calls") or 0),
                        "usage_stages": usage.get("stages") or {},
                    },
                )
            except Exception:
                logger.exception("Failed to finalize creation task %s", creation_task_id)
        log_event(
            logger,
            "storyboard.task.completed",
            task_id=(task.task_id if task else self.request.id),
            novel_id=novel.id,
            storyboard_project_id=project.id,
        )
    except Exception as exc:
        err_text = str(exc)
        _worker_superseded = "worker superseded" in err_text
        if _worker_superseded:
            logger.warning("Storyboard worker superseded, exiting gracefully: %s", err_text)
            db.rollback()
        else:
            db.rollback()
            task = _reload_task(db, task_db_id) if task_db_id is not None else None
            project = db.execute(select(StoryboardProject).where(StoryboardProject.id == project_id)).scalar_one_or_none()
            is_paused = err_text == "storyboard_paused"
            is_cancelled = err_text == "storyboard_cancelled"
            if task:
                update_task_state(
                    db,
                    task,
                    status="paused" if is_paused else ("cancelled" if is_cancelled else "failed"),
                    run_state="paused" if is_paused else ("cancelled" if is_cancelled else "failed"),
                    phase="paused" if is_paused else ("cancelled" if is_cancelled else "failed"),
                    message="分镜生成已暂停" if is_paused else ("分镜生成已取消" if is_cancelled else "导演分镜生成失败"),
                    error=None if (is_paused or is_cancelled) else str(exc),
                    error_code=None if (is_paused or is_cancelled) else "STORYBOARD_PIPELINE_FAILED",
                    error_category=None if (is_paused or is_cancelled) else "transient",
                    retryable=0 if is_cancelled else 1,
                )
            if project and not is_paused:
                project.status = "failed"
            db.commit()
            if task:
                _publish(task)
            if creation_task_id is not None:
                try:
                    usage = snapshot_usage()
                    _finalize_creation(
                        creation_task_id,
                        final_status="paused" if is_paused else ("cancelled" if is_cancelled else "failed"),
                        progress=0.0,
                        phase="paused" if is_paused else ("cancelled" if is_cancelled else "failed"),
                        message="分镜生成已暂停" if is_paused else ("分镜生成已取消" if is_cancelled else "导演分镜生成失败"),
                        error_code=None if (is_paused or is_cancelled) else "STORYBOARD_PIPELINE_FAILED",
                        error_category=None if (is_paused or is_cancelled) else "transient",
                        error_detail=None if (is_paused or is_cancelled) else str(exc),
                        result_json={
                            "token_usage_input": int(usage.get("input_tokens") or 0),
                            "token_usage_output": int(usage.get("output_tokens") or 0),
                            "estimated_cost": float(usage.get("estimated_cost") or 0.0),
                            "usage_calls": int(usage.get("calls") or 0),
                            "usage_stages": usage.get("stages") or {},
                        },
                    )
                except Exception:
                    logger.exception("Failed to finalize creation task %s", creation_task_id)
            log_event(
                logger,
                "storyboard.task.stopped" if (is_paused or is_cancelled) else "storyboard.task.failed",
                level=logging.WARNING if (is_paused or is_cancelled) else logging.ERROR,
                task_id=getattr(task, "task_id", None),
                storyboard_project_id=project_id,
                error_class=type(exc).__name__,
                error_code=None if (is_paused or is_cancelled) else "STORYBOARD_PIPELINE_FAILED",
                error_category=None if (is_paused or is_cancelled) else "transient",
            )
            if not (is_paused or is_cancelled):
                raise
    finally:
        hb_ctx.__exit__(None, None, None)
        db.close()
        end_usage_session()


def _ensure_lane_creation_state(creation_task_id: int | None, *, current_celery_id: str | None = None) -> None:
    if creation_task_id is None:
        return
    if current_celery_id:
        _check_worker_superseded(creation_task_id, current_celery_id)
    c_state = _get_creation_task_state(creation_task_id)
    if c_state == "cancelled":
        raise RuntimeError("storyboard_cancelled")
    if c_state == "paused":
        raise RuntimeError("storyboard_paused")


def _chapters_from_snapshot(snapshot: StoryboardSourceSnapshot) -> list[AdaptedChapter]:
    out: list[AdaptedChapter] = []
    for row in list(snapshot.chapters_json or []):
        out.append(
            AdaptedChapter(
                chapter_num=int(row.get("chapter_num") or 0),
                title=str(row.get("title") or ""),
                summary=str(row.get("summary") or ""),
                content=str(row.get("content") or ""),
            )
        )
    return [row for row in out if row.chapter_num > 0]


def _build_character_cards(
    *,
    profiles: list[dict],
    shots,
    lane: str,
    style_profile: str | None,
    genre: str | None,
) -> tuple[list[dict], list[dict]]:
    from app.services.generation.character_profiles import normalize_ethnicity, normalize_skin_tone

    cards: list[dict] = []
    failed: list[dict] = []
    for row in profiles:
        skin = normalize_skin_tone(str(row.get("skin_tone") or ""))
        eth = normalize_ethnicity(str(row.get("ethnicity") or ""))
        if not skin or not eth:
            miss: list[str] = []
            if not skin:
                miss.append("skin_tone")
            if not eth:
                miss.append("ethnicity")
            failed.append(
                {
                    "character_key": row.get("character_key") or "",
                    "display_name": row.get("display_name") or "",
                    "missing_fields": miss,
                }
            )
            continue
        display_name = str(row.get("display_name") or row.get("character_key") or "角色")
        shot_refs: list[str] = []
        for shot in shots[:200]:
            chars = [str(x) for x in (shot.characters_json or [])]
            if display_name in chars:
                shot_refs.append(f"E{shot.episode_no}S{shot.scene_no}#{shot.shot_no}:{(shot.action or '')[:26]}")
                if len(shot_refs) >= 3:
                    break
        cards.append(
            {
                "character_key": str(row.get("character_key") or display_name),
                "display_name": display_name,
                "skin_tone": skin,
                "ethnicity": eth,
                "master_prompt_text": (
                    f"{display_name}，{lane}分镜风格，题材={genre or '通用'}，视觉风格={style_profile or '平台默认'}。"
                    f"肤色={skin}，族裔={eth}。关键镜头锚点：{'；'.join(shot_refs) if shot_refs else '暂无'}。"
                ),
                "negative_prompt_text": "避免脸型漂移、发色漂移、时代错置、服装突变",
                "style_tags_json": [style_profile or "", lane, genre or ""],
                "consistency_anchors_json": row.get("visual_do_not_change_json") or [],
                "quality_score": float(row.get("confidence") or 0.0),
                "metadata_json": {"source": "storyboard_v2"},
            }
        )
    return cards, failed


def _refresh_run_after_lane(db, run_id: int) -> None:
    refreshed = refresh_run_status(db, run_id=run_id)
    if refreshed:
        log_event(
            logger,
            "storyboard.run.refresh",
            storyboard_project_id=refreshed.storyboard_project_id,
            run_id=refreshed.public_id,
            run_state=refreshed.run_state,
            status=refreshed.status,
            progress=refreshed.progress,
        )


@app.task(bind=True, acks_late=True, reject_on_worker_lost=True)
def run_storyboard_lane(
    self,
    *,
    project_id: int,
    run_id: int,
    run_lane_id: int,
    version_id: int,
    lane: str,
    novel_version_id: int,
    creation_task_id: int | None = None,
):
    begin_usage_session(f"storyboard-lane:{self.request.id}")
    from app.core.constants import CREATION_WORKER_HEARTBEAT_SECONDS as _HB_SEC
    hb_ctx = background_heartbeat(
        creation_task_id,
        heartbeat_fn=_heartbeat_creation,
        interval_seconds=_HB_SEC,
    )
    hb_ctx.__enter__()
    db = SessionLocal()
    try:
        if creation_task_id is not None:
            _mark_creation_running(creation_task_id)
        project = db.execute(select(StoryboardProject).where(StoryboardProject.id == project_id)).scalar_one_or_none()
        run = db.execute(select(StoryboardRun).where(StoryboardRun.id == run_id)).scalar_one_or_none()
        run_lane = db.execute(select(StoryboardRunLane).where(StoryboardRunLane.id == run_lane_id)).scalar_one_or_none()
        version = db.execute(select(StoryboardVersion).where(StoryboardVersion.id == version_id)).scalar_one_or_none()
        if not project or not run or not run_lane or not version:
            raise RuntimeError("storyboard_lane_context_not_found")
        novel = db.execute(select(Novel).where(Novel.id == int(project.novel_id))).scalar_one_or_none()
        if not novel:
            raise RuntimeError("storyboard_lane_novel_not_found")

        update_run_lane_state(
            db,
            run_lane_id=run_lane.id,
            status="running",
            run_state="running",
            current_phase="shot_expand",
            progress=3.0,
            message=f"{lane} 正在生成分镜",
        )
        db.commit()

        _ensure_lane_creation_state(creation_task_id, current_celery_id=self.request.id)
        snapshot = db.execute(
            select(StoryboardSourceSnapshot)
            .where(
                StoryboardSourceSnapshot.storyboard_project_id == project.id,
                StoryboardSourceSnapshot.novel_version_id == novel_version_id,
            )
            .order_by(StoryboardSourceSnapshot.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if not snapshot:
            raise RuntimeError("storyboard_source_snapshot_not_found")
        chapters = _chapters_from_snapshot(snapshot)
        if not chapters:
            raise RuntimeError("storyboard_source_snapshot_chapters_empty")

        lane_shots, contract, quality = generate_lane_shots(
            lane=lane,
            novel=novel,
            chapters=chapters,
            target_episodes=int(project.target_episodes or 40),
            target_episode_seconds=int(project.target_episode_seconds or 90),
            style_profile=project.style_profile,
            mode=(project.config_json or {}).get("mode") or "quick",
            genre_style_key=(project.config_json or {}).get("genre_style_key"),
            director_style_key=(project.config_json or {}).get("director_style_key"),
        )
        # Deterministic per-version shot persistence for reproducibility.
        from app.services.storyboard.service import persist_shots

        shot_count = persist_shots(db, version.id, lane_shots)
        quality_report = build_quality_report(lane=lane, quality=quality, prompt_contract_json=contract)
        update_run_lane_state(
            db,
            run_lane_id=run_lane.id,
            status="running",
            run_state="running",
            current_phase="quality_gate",
            progress=65.0,
            message=f"{lane} 质量门禁校验中",
            gate_report_json=quality_report,
        )
        if quality.style_consistency_score < 0.75:
            db.add(
                StoryboardGateReport(
                    storyboard_project_id=project.id,
                    storyboard_run_id=run.id,
                    storyboard_version_id=version.id,
                    gate_type="quality",
                    gate_status="failed",
                    missing_count=0,
                    report_json=quality_report,
                    created_by_user_uuid=run.requested_by_user_uuid,
                )
            )
            version.status = "failed"
            update_run_lane_state(
                db,
                run_lane_id=run_lane.id,
                status="failed",
                run_state="failed",
                current_phase="quality_gate",
                progress=100.0,
                message=f"{lane} 风格门禁失败",
                error="style consistency gate failed",
                error_code="GATE_STYLE_FAILED",
                error_category="policy",
            )
            db.commit()
            _refresh_run_after_lane(db, run.id)
            if creation_task_id is not None:
                _finalize_creation(
                    creation_task_id,
                    final_status="failed",
                    progress=100.0,
                    phase="quality_gate",
                    message=f"{lane} 风格门禁失败",
                    error_code="GATE_STYLE_FAILED",
                    error_category="policy",
                )
            return

        _ensure_lane_creation_state(creation_task_id, current_celery_id=self.request.id)
        shot_rows = db.execute(
            select(StoryboardShot)
            .where(StoryboardShot.storyboard_version_id == version.id)
            .order_by(StoryboardShot.episode_no.asc(), StoryboardShot.scene_no.asc(), StoryboardShot.shot_no.asc())
        ).scalars().all()

        cards, failed_rows = _build_character_cards(
            profiles=list(snapshot.character_profiles_json or []),
            shots=shot_rows,
            lane=lane,
            style_profile=project.style_profile,
            genre=novel.genre,
        )
        quality_report["missing_identity_fields_count"] = len(failed_rows)
        quality_report["failed_identity_characters"] = failed_rows[:20]
        if failed_rows:
            db.add(
                StoryboardGateReport(
                    storyboard_project_id=project.id,
                    storyboard_run_id=run.id,
                    storyboard_version_id=version.id,
                    gate_type="identity",
                    gate_status="blocked",
                    missing_count=len(failed_rows),
                    report_json=quality_report,
                    created_by_user_uuid=run.requested_by_user_uuid,
                )
            )
            version.status = "failed"
            version.quality_report_json = quality_report
            update_run_lane_state(
                db,
                run_lane_id=run_lane.id,
                status="failed",
                run_state="failed",
                current_phase="character_identity_gate",
                progress=100.0,
                message="角色身份字段门禁未通过",
                error="identity fields missing",
                error_code="GATE_IDENTITY_REQUIRED",
                error_category="policy",
                gate_report_json=quality_report,
            )
            db.commit()
            _refresh_run_after_lane(db, run.id)
            if creation_task_id is not None:
                _finalize_creation(
                    creation_task_id,
                    final_status="failed",
                    progress=100.0,
                    phase="character_identity_gate",
                    message="角色身份字段门禁未通过",
                    error_code="GATE_IDENTITY_REQUIRED",
                    error_category="policy",
                )
            return

        persist_character_cards(
            db,
            project_id=project.id,
            version_id=version.id,
            lane=lane,
            cards=cards,
        )
        version.status = "completed"
        version.quality_report_json = {
            **quality_report,
            "character_cards_count": len(cards),
            "shot_count": int(shot_count),
        }
        update_run_lane_state(
            db,
            run_lane_id=run_lane.id,
            status="completed",
            run_state="completed",
            current_phase="completed",
            progress=100.0,
            message=f"{lane} 完成",
            gate_report_json=version.quality_report_json,
        )
        db.add(
            StoryboardGateReport(
                storyboard_project_id=project.id,
                storyboard_run_id=run.id,
                storyboard_version_id=version.id,
                gate_type="quality",
                gate_status="passed",
                missing_count=0,
                report_json=version.quality_report_json,
                created_by_user_uuid=run.requested_by_user_uuid,
            )
        )
        db.commit()
        _refresh_run_after_lane(db, run.id)
        if creation_task_id is not None:
            usage = snapshot_usage()
            _finalize_creation(
                creation_task_id,
                final_status="completed",
                progress=100.0,
                phase="completed",
                message=f"{lane} 已完成",
                result_json={
                    "shot_count": int(shot_count),
                    "character_cards_count": len(cards),
                    "token_usage_input": int(usage.get("input_tokens") or 0),
                    "token_usage_output": int(usage.get("output_tokens") or 0),
                    "estimated_cost": float(usage.get("estimated_cost") or 0.0),
                },
            )
    except Exception as exc:
        err = str(exc)
        run_lane = db.execute(select(StoryboardRunLane).where(StoryboardRunLane.id == run_lane_id)).scalar_one_or_none()
        if run_lane:
            if err == "storyboard_paused":
                update_run_lane_state(
                    db,
                    run_lane_id=run_lane.id,
                    status="paused",
                    run_state="paused",
                    current_phase="paused",
                    message="Lane 已暂停",
                )
            elif err == "storyboard_cancelled":
                update_run_lane_state(
                    db,
                    run_lane_id=run_lane.id,
                    status="cancelled",
                    run_state="cancelled",
                    current_phase="cancelled",
                    message="Lane 已取消",
                )
            else:
                update_run_lane_state(
                    db,
                    run_lane_id=run_lane.id,
                    status="failed",
                    run_state="failed",
                    current_phase="failed",
                    progress=100.0,
                    message="Lane 执行失败",
                    error=err,
                    error_code="GEN_LANE_FAILED",
                    error_category="transient",
                )
        db.commit()
        _refresh_run_after_lane(db, run_id)
        if creation_task_id is not None:
            _finalize_creation(
                creation_task_id,
                final_status="paused" if err == "storyboard_paused" else ("cancelled" if err == "storyboard_cancelled" else "failed"),
                progress=100.0 if err not in {"storyboard_paused", "storyboard_cancelled"} else 0.0,
                phase="paused" if err == "storyboard_paused" else ("cancelled" if err == "storyboard_cancelled" else "failed"),
                message="Lane 已暂停" if err == "storyboard_paused" else ("Lane 已取消" if err == "storyboard_cancelled" else "Lane 执行失败"),
                error_code=None if err in {"storyboard_paused", "storyboard_cancelled"} else "GEN_LANE_FAILED",
                error_category=None if err in {"storyboard_paused", "storyboard_cancelled"} else "transient",
                error_detail=None if err in {"storyboard_paused", "storyboard_cancelled"} else err,
            )
        if err not in {"storyboard_paused", "storyboard_cancelled"}:
            raise
    finally:
        hb_ctx.__exit__(None, None, None)
        db.close()
        end_usage_session()


@app.task(bind=True, acks_late=True, reject_on_worker_lost=True)
def run_storyboard_export(self, *, export_db_id: int):
    db = SessionLocal()
    try:
        row = db.execute(select(StoryboardExport).where(StoryboardExport.id == export_db_id)).scalar_one_or_none()
        if not row:
            return
        if row.status in {"completed", "running"}:
            return
        row.status = "running"
        row.updated_at = datetime.now(timezone.utc)
        db.flush()
        version = db.execute(select(StoryboardVersion).where(StoryboardVersion.id == row.storyboard_version_id)).scalar_one_or_none()
        if not version:
            row.status = "failed"
            row.error_code = "EXPORT_VERSION_NOT_FOUND"
            row.error = "version not found"
            row.finished_at = datetime.now(timezone.utc)
            db.commit()
            return
        if int(version.is_final or 0) != 1:
            row.status = "failed"
            row.error_code = "EXPORT_REQUIRES_FINAL"
            row.error = "only finalized version can be exported"
            row.finished_at = datetime.now(timezone.utc)
            db.commit()
            return
        payload, content_type, ext = render_export_blob(db, version_id=version.id, export_format=row.format)
        storage_path, size = save_export_blob(export_public_id=row.public_id, extension=ext, content=payload)
        row.status = "completed"
        row.content_type = content_type
        row.file_name = f"storyboard-p{row.storyboard_project_id}-v{version.version_no}-{version.lane}.{ext}"
        row.storage_path = storage_path
        row.size_bytes = size
        row.finished_at = datetime.now(timezone.utc)
        row.updated_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:
        db.rollback()
        row = db.execute(select(StoryboardExport).where(StoryboardExport.id == export_db_id)).scalar_one_or_none()
        if row:
            row.status = "failed"
            row.error_code = "EXPORT_TASK_FAILED"
            row.error = str(exc)
            row.finished_at = datetime.now(timezone.utc)
            row.updated_at = datetime.now(timezone.utc)
            db.commit()
        raise
    finally:
        db.close()
