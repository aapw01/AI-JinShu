"""Celery tasks for storyboard generation."""
from __future__ import annotations

import json
import logging
import time

import redis
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.llm_usage import begin_usage_session, end_usage_session, snapshot_usage
from app.core.logging_config import log_event
from app.models.novel import Novel
from app.models.storyboard import StoryboardProject, StoryboardTask, StoryboardVersion
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
from app.services.storyboard.service import (
    build_quality_report,
    generate_lane_shots,
    load_novel_chapters,
    persist_episode_shots,
    project_config,
    set_default_version,
    update_task_state,
)
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
    hb_interval = max(5, int(get_settings().creation_worker_heartbeat_seconds or 30))
    hb_ctx = background_heartbeat(creation_task_id, heartbeat_fn=_heartbeat_creation, interval_seconds=hb_interval)
    hb_ctx.__enter__()
    db = SessionLocal()
    try:
        project = db.execute(select(StoryboardProject).where(StoryboardProject.id == project_id)).scalar_one_or_none()
        task = _reload_task(db, task_db_id) if task_db_id is not None else None
        if not project:
            raise RuntimeError("storyboard task context not found")
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
        db.rollback()
        task = _reload_task(db, task_db_id) if task_db_id is not None else None
        project = db.execute(select(StoryboardProject).where(StoryboardProject.id == project_id)).scalar_one_or_none()
        is_paused = str(exc) == "storyboard_paused"
        is_cancelled = str(exc) == "storyboard_cancelled"
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
