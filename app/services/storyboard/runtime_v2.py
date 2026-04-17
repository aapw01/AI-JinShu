"""Storyboard V2 domain services: preflight, run orchestration, and state aggregation."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.novel import ChapterVersion, StoryCharacterProfile
from app.models.storyboard import (
    StoryboardAuditLog,
    StoryboardCharacterCard,
    StoryboardCharacterPrompt,
    StoryboardExport,
    StoryboardGateReport,
    StoryboardProject,
    StoryboardRun,
    StoryboardRunLane,
    StoryboardSourceSnapshot,
    StoryboardVersion,
)
from app.services.generation.character_profiles import normalize_ethnicity, normalize_skin_tone
from app.services.rewrite.service import get_default_version_id
from app.services.scheduler.scheduler_service import (
    cancel_task as cancel_creation_task,
    pause_task as pause_creation_task,
    resume_task as resume_creation_task,
    submit_task as submit_creation_task,
)
from app.services.storyboard.events import append_event
from app.services.storyboard.service import normalize_lanes


ACTIVE_LANE_STATES = {"queued", "submitted", "dispatching", "running", "retrying", "paused"}
TERMINAL_LANE_STATES = {"completed", "failed", "cancelled"}


def _utc_now() -> datetime:
    """返回当前 UTC 时间，统一任务与数据库时间基准。"""
    return datetime.now(timezone.utc)


def _lane_label(lane: str) -> str:
    """执行 lane label 相关辅助逻辑。"""
    return "竖屏版" if lane == "vertical_feed" else "横屏版"


def _serialize_chapter(row: ChapterVersion) -> dict[str, Any]:
    """执行 serialize chapter 相关辅助逻辑。"""
    return {
        "chapter_num": int(row.chapter_num),
        "title": row.title or f"第{row.chapter_num}章",
        "summary": row.summary or "",
        "content": row.content or "",
    }


def _serialize_profile(row: StoryCharacterProfile) -> dict[str, Any]:
    """执行 serialize profile 相关辅助逻辑。"""
    return {
        "character_key": row.character_key or "",
        "display_name": row.display_name or "",
        "gender_presentation": row.gender_presentation,
        "age_band": row.age_band,
        "skin_tone": row.skin_tone,
        "ethnicity": row.ethnicity,
        "body_type": row.body_type,
        "face_features": row.face_features,
        "hair_style": row.hair_style,
        "hair_color": row.hair_color,
        "eye_color": row.eye_color,
        "wardrobe_base_style": row.wardrobe_base_style,
        "signature_items_json": row.signature_items_json or [],
        "visual_do_not_change_json": row.visual_do_not_change_json or [],
        "confidence": float(row.confidence or 0.0),
        "updated_chapter_num": row.updated_chapter_num,
    }


def _snapshot_hash(chapters: list[dict[str, Any]], profiles: list[dict[str, Any]]) -> str:
    """执行 snapshot hash 相关辅助逻辑。"""
    payload = {"chapters": chapters, "profiles": profiles}
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _audit(
    db: Session,
    *,
    project_id: int,
    actor_user_uuid: str,
    action: str,
    run_id: int | None = None,
    version_id: int | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """执行 audit 相关辅助逻辑。"""
    db.add(
        StoryboardAuditLog(
            storyboard_project_id=project_id,
            storyboard_run_id=run_id,
            storyboard_version_id=version_id,
            action=action,
            actor_user_uuid=actor_user_uuid,
            detail_json=detail or {},
        )
    )
    db.flush()


def get_project_or_404(db: Session, project_id: int) -> StoryboardProject | None:
    """返回project或404。"""
    return db.execute(select(StoryboardProject).where(StoryboardProject.id == project_id)).scalar_one_or_none()


def get_run_by_public_id(db: Session, *, project_id: int, run_public_id: str) -> StoryboardRun | None:
    """获取 run by public id。"""
    return db.execute(
        select(StoryboardRun).where(
            StoryboardRun.storyboard_project_id == project_id,
            StoryboardRun.public_id == run_public_id,
        )
    ).scalar_one_or_none()


def list_run_lanes(db: Session, *, run_id: int) -> list[StoryboardRunLane]:
    """列出runlanes。"""
    return db.execute(
        select(StoryboardRunLane)
        .where(StoryboardRunLane.storyboard_run_id == run_id)
        .order_by(StoryboardRunLane.id.asc())
    ).scalars().all()


def ensure_project_source_version(db: Session, *, project: StoryboardProject) -> int:
    """确保projectsource版本存在并可用。"""
    source_version_id = int(project.source_novel_version_id or 0)
    if source_version_id <= 0:
        source_version_id = int(get_default_version_id(db, project.novel_id))
        project.source_novel_version_id = source_version_id
        db.flush()
    return source_version_id


def build_source_snapshot(
    db: Session,
    *,
    project: StoryboardProject,
    force_refresh: bool = False,
) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]], str]:
    """构建sourcesnapshot。"""
    source_version_id = ensure_project_source_version(db, project=project)
    if not force_refresh:
        latest = db.execute(
            select(StoryboardSourceSnapshot)
            .where(
                StoryboardSourceSnapshot.storyboard_project_id == project.id,
                StoryboardSourceSnapshot.novel_version_id == source_version_id,
            )
            .order_by(StoryboardSourceSnapshot.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if latest:
            return (
                source_version_id,
                list(latest.chapters_json or []),
                list(latest.character_profiles_json or []),
                str(latest.snapshot_hash or ""),
            )

    chapters = db.execute(
        select(ChapterVersion)
        .where(ChapterVersion.novel_version_id == source_version_id)
        .order_by(ChapterVersion.chapter_num.asc())
    ).scalars().all()
    chapter_rows = [_serialize_chapter(row) for row in chapters]

    profiles = db.execute(
        select(StoryCharacterProfile)
        .where(
            StoryCharacterProfile.novel_id == project.novel_id,
            StoryCharacterProfile.novel_version_id == source_version_id,
        )
        .order_by(StoryCharacterProfile.display_name.asc())
    ).scalars().all()
    profile_rows = [_serialize_profile(row) for row in profiles]
    digest = _snapshot_hash(chapter_rows, profile_rows)

    db.add(
        StoryboardSourceSnapshot(
            storyboard_project_id=project.id,
            novel_id=project.novel_id,
            novel_version_id=source_version_id,
            snapshot_hash=digest,
            chapters_json=chapter_rows,
            character_profiles_json=profile_rows,
            metadata_json={"source": "preflight"},
        )
    )
    db.flush()
    return source_version_id, chapter_rows, profile_rows, digest


def run_preflight(
    db: Session,
    *,
    project: StoryboardProject,
    actor_user_uuid: str,
    force_refresh_snapshot: bool = False,
) -> dict[str, Any]:
    """执行preflight。"""
    source_version_id, chapters, profiles, digest = build_source_snapshot(
        db,
        project=project,
        force_refresh=force_refresh_snapshot,
    )
    failed_rows: list[dict[str, Any]] = []
    for row in profiles:
        miss: list[str] = []
        if not normalize_skin_tone(str(row.get("skin_tone") or "")):
            miss.append("skin_tone")
        if not normalize_ethnicity(str(row.get("ethnicity") or "")):
            miss.append("ethnicity")
        if miss:
            failed_rows.append(
                {
                    "character_key": str(row.get("character_key") or ""),
                    "display_name": str(row.get("display_name") or ""),
                    "missing_fields": miss,
                }
            )
    gate_status = "passed"
    if not chapters or not profiles or failed_rows:
        gate_status = "blocked"
    project.status = "preflight_passed" if gate_status == "passed" else "preflight_blocked"
    report = {
        "source_novel_version_id": source_version_id,
        "profiles_count": len(profiles),
        "chapters_count": len(chapters),
        "missing_identity_fields_count": len(failed_rows),
        "failed_identity_characters": failed_rows[:50],
        "snapshot_hash": digest,
    }
    db.add(
        StoryboardGateReport(
            storyboard_project_id=project.id,
            storyboard_run_id=None,
            storyboard_version_id=None,
            gate_type="preflight",
            gate_status=gate_status,
            missing_count=len(failed_rows),
            report_json=report,
            created_by_user_uuid=actor_user_uuid,
        )
    )
    append_event(
        db,
        storyboard_project_id=project.id,
        storyboard_run_id=None,
        topic="storyboard.preflight",
        event_key=f"project:{project.id}:preflight:{gate_status}:{int(_utc_now().timestamp())}",
        payload={"gate_status": gate_status, **report},
    )
    _audit(
        db,
        project_id=project.id,
        actor_user_uuid=actor_user_uuid,
        action="preflight",
        detail={"gate_status": gate_status, "missing_identity_fields_count": len(failed_rows)},
    )
    db.flush()
    return {"ok": gate_status == "passed", "gate_status": gate_status, **report}


def _next_version_no(db: Session, *, project_id: int) -> int:
    """执行 next version no 相关辅助逻辑。"""
    val = db.execute(
        select(func.max(StoryboardVersion.version_no)).where(StoryboardVersion.storyboard_project_id == project_id)
    ).scalar_one_or_none()
    return int(val or 0) + 1


def _create_run_lane_version(
    db: Session,
    *,
    project: StoryboardProject,
    lane: str,
    source_novel_version_id: int,
) -> StoryboardVersion:
    """创建runlane版本。"""
    version = StoryboardVersion(
        storyboard_project_id=project.id,
        source_novel_version_id=source_novel_version_id,
        version_no=_next_version_no(db, project_id=project.id),
        lane=lane,
        status="generating",
        is_default=0,
        is_final=0,
        quality_report_json={},
    )
    db.add(version)
    db.flush()
    return version


def create_run_and_dispatch(
    db: Session,
    *,
    project: StoryboardProject,
    actor_user_uuid: str,
    trace_id: str | None = None,
    idempotency_key: str | None = None,
) -> tuple[StoryboardRun, list[StoryboardRunLane]]:
    """创建runanddispatch。"""
    if project.status != "preflight_passed":
        raise ValueError("preflight_required")
    if idempotency_key:
        existing = db.execute(
            select(StoryboardRun)
            .where(
                StoryboardRun.storyboard_project_id == project.id,
                StoryboardRun.idempotency_key == idempotency_key,
            )
            .order_by(StoryboardRun.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if existing:
            return existing, list_run_lanes(db, run_id=existing.id)

    active = db.execute(
        select(StoryboardRun)
        .where(
            StoryboardRun.storyboard_project_id == project.id,
            StoryboardRun.status.in_(list(ACTIVE_LANE_STATES)),
        )
        .limit(1)
    ).scalar_one_or_none()
    if active:
        raise ValueError("run_already_active")

    source_version_id = ensure_project_source_version(db, project=project)
    run = StoryboardRun(
        storyboard_project_id=project.id,
        requested_by_user_uuid=actor_user_uuid,
        status="queued",
        run_state="queued",
        current_phase="queued",
        progress=0.0,
        message="分镜运行已创建，等待调度",
        trace_id=trace_id,
        idempotency_key=idempotency_key,
        started_at=_utc_now(),
    )
    db.add(run)
    db.flush()

    lanes = normalize_lanes(project.output_lanes if isinstance(project.output_lanes, list) else None)
    run_lanes: list[StoryboardRunLane] = []
    for lane in lanes:
        version = _create_run_lane_version(
            db,
            project=project,
            lane=lane,
            source_novel_version_id=source_version_id,
        )
        lane_row = StoryboardRunLane(
            storyboard_run_id=run.id,
            storyboard_project_id=project.id,
            lane=lane,
            storyboard_version_id=version.id,
            status="queued",
            run_state="queued",
            current_phase="queued",
            progress=0.0,
            message=f"{_lane_label(lane)}等待调度",
            gate_report_json={},
            started_at=_utc_now(),
        )
        db.add(lane_row)
        db.flush()
        run_lanes.append(lane_row)

        creation = submit_creation_task(
            db,
            user_uuid=actor_user_uuid,
            task_type="storyboard_lane",
            resource_type="storyboard_project",
            resource_id=project.id,
            payload={
                "project_id": project.id,
                "run_id": run.id,
                "run_public_id": run.public_id,
                "run_lane_id": lane_row.id,
                "lane": lane,
                "version_id": version.id,
                "novel_version_id": source_version_id,
            },
        )
        lane_row.creation_task_public_id = creation.public_id
        lane_row.status = "submitted"
        lane_row.run_state = "submitted"
        lane_row.current_phase = "submitted"
        lane_row.message = f"{_lane_label(lane)}任务已提交"

    run.status = "running"
    run.run_state = "running"
    run.current_phase = "lane_dispatch"
    run.message = "双 Lane 任务已提交"
    run.progress = 1.0
    project.status = "generating"
    project.active_lane = run_lanes[0].lane if run_lanes else project.active_lane
    append_event(
        db,
        storyboard_project_id=project.id,
        storyboard_run_id=run.id,
        topic="storyboard.run",
        event_key=f"run:{run.public_id}:created",
        payload={"run_id": run.public_id, "lane_count": len(run_lanes)},
    )
    _audit(
        db,
        project_id=project.id,
        run_id=run.id,
        actor_user_uuid=actor_user_uuid,
        action="run_start",
        detail={"run_id": run.public_id, "lanes": [row.lane for row in run_lanes]},
    )
    db.flush()
    return run, run_lanes


def update_run_lane_state(
    db: Session,
    *,
    run_lane_id: int,
    status: str | None = None,
    run_state: str | None = None,
    current_phase: str | None = None,
    progress: float | None = None,
    message: str | None = None,
    error: str | None = None,
    error_code: str | None = None,
    error_category: str | None = None,
    gate_report_json: dict[str, Any] | None = None,
) -> StoryboardRunLane | None:
    """更新runlane状态。"""
    lane = db.execute(select(StoryboardRunLane).where(StoryboardRunLane.id == run_lane_id)).scalar_one_or_none()
    if not lane:
        return None
    if status is not None:
        lane.status = status
    if run_state is not None:
        lane.run_state = run_state
    if current_phase is not None:
        lane.current_phase = current_phase
    if progress is not None:
        lane.progress = float(max(0.0, min(100.0, progress)))
    if message is not None:
        lane.message = message
    if error is not None:
        lane.error = error
    if error_code is not None:
        lane.error_code = error_code
    if error_category is not None:
        lane.error_category = error_category
    if gate_report_json is not None:
        lane.gate_report_json = gate_report_json
    if lane.status in TERMINAL_LANE_STATES and lane.finished_at is None:
        lane.finished_at = _utc_now()
    lane.updated_at = _utc_now()
    db.flush()
    return lane


def refresh_run_status(db: Session, *, run_id: int) -> StoryboardRun | None:
    """执行 refresh run status 相关辅助逻辑。"""
    run = db.execute(select(StoryboardRun).where(StoryboardRun.id == run_id)).scalar_one_or_none()
    if not run:
        return None
    lanes = list_run_lanes(db, run_id=run_id)
    if not lanes:
        return run

    avg_progress = sum(float(row.progress or 0.0) for row in lanes) / len(lanes)
    run.progress = round(avg_progress, 2)

    statuses = {row.status for row in lanes}
    if statuses.issubset({"completed"}):
        run.status = "completed"
        run.run_state = "completed"
        run.current_phase = "completed"
        run.message = "双 Lane 分镜生成完成"
        run.finished_at = run.finished_at or _utc_now()
        project = get_project_or_404(db, run.storyboard_project_id)
        if project:
            project.status = "reviewing"
            # Use vertical lane as the default review lane when available.
            preferred = next((row for row in lanes if row.lane == "vertical_feed"), lanes[0])
            versions = db.execute(
                select(StoryboardVersion).where(StoryboardVersion.storyboard_project_id == project.id)
            ).scalars().all()
            for item in versions:
                item.is_default = 1 if item.id == preferred.storyboard_version_id else 0
            project.active_lane = preferred.lane
    elif statuses.intersection({"failed"}):
        run.status = "failed"
        run.run_state = "failed"
        run.current_phase = "failed"
        run.message = "至少一个 Lane 生成失败"
        run.finished_at = run.finished_at or _utc_now()
        project = get_project_or_404(db, run.storyboard_project_id)
        if project:
            project.status = "failed"
    elif statuses.issubset({"cancelled"}):
        run.status = "cancelled"
        run.run_state = "cancelled"
        run.current_phase = "cancelled"
        run.message = "分镜运行已取消"
        run.finished_at = run.finished_at or _utc_now()
        project = get_project_or_404(db, run.storyboard_project_id)
        if project:
            project.status = "cancelled"
    elif statuses.intersection({"paused"}) and not statuses.intersection({"running", "submitted", "queued", "dispatching", "retrying"}):
        run.status = "paused"
        run.run_state = "paused"
        run.current_phase = "paused"
        run.message = "分镜运行已暂停"
    else:
        run.status = "running"
        run.run_state = "running"
        run.current_phase = "lane_running"
        run.message = "Lane 并行执行中"

    run.updated_at = _utc_now()
    append_event(
        db,
        storyboard_project_id=run.storyboard_project_id,
        storyboard_run_id=run.id,
        topic="storyboard.run",
        event_key=f"run:{run.public_id}:status:{int(_utc_now().timestamp())}",
        payload={"status": run.status, "run_state": run.run_state, "progress": run.progress},
    )
    db.flush()
    return run


def run_action(
    db: Session,
    *,
    run: StoryboardRun,
    action: str,
    actor_user_uuid: str,
) -> StoryboardRun:
    """执行action。"""
    if run.run_state in {"completed", "failed", "cancelled"} and action != "cancel":
        raise ValueError("run_already_terminal")
    lanes = list_run_lanes(db, run_id=run.id)
    if action == "pause":
        if run.run_state == "paused":
            return run
        for row in lanes:
            if not row.creation_task_public_id:
                continue
            try:
                pause_creation_task(db, public_id=row.creation_task_public_id, user_uuid=actor_user_uuid)
            except Exception:
                continue
            update_run_lane_state(
                db,
                run_lane_id=row.id,
                status="paused",
                run_state="paused",
                current_phase="paused",
                message=f"{_lane_label(row.lane)}已暂停",
            )
        run.status = "paused"
        run.run_state = "paused"
        run.current_phase = "paused"
        run.message = "分镜运行已暂停"
    elif action == "resume":
        resumed_count = 0
        for row in lanes:
            if not row.creation_task_public_id:
                continue
            try:
                resume_creation_task(db, public_id=row.creation_task_public_id, user_uuid=actor_user_uuid)
            except Exception:
                continue
            resumed_count += 1
            update_run_lane_state(
                db,
                run_lane_id=row.id,
                status="queued",
                run_state="queued",
                current_phase="queued",
                message=f"{_lane_label(row.lane)}已恢复，等待调度",
            )
        if resumed_count == 0:
            raise ValueError("no_lanes_resumed")
        run.status = "running"
        run.run_state = "queued"
        run.current_phase = "queued"
        run.message = "分镜运行已恢复，等待调度"
    elif action == "cancel":
        if run.run_state == "cancelled":
            return run
        for row in lanes:
            if not row.creation_task_public_id:
                continue
            try:
                cancel_creation_task(db, public_id=row.creation_task_public_id, user_uuid=actor_user_uuid)
            except Exception:
                continue
            update_run_lane_state(
                db,
                run_lane_id=row.id,
                status="cancelled",
                run_state="cancelled",
                current_phase="cancelled",
                message=f"{_lane_label(row.lane)}已取消",
            )
        run.status = "cancelled"
        run.run_state = "cancelled"
        run.current_phase = "cancelled"
        run.message = "分镜运行已取消"
        run.finished_at = run.finished_at or _utc_now()
        project = get_project_or_404(db, run.storyboard_project_id)
        if project:
            project.status = "cancelled"
    else:
        raise ValueError("unsupported_action")

    _audit(
        db,
        project_id=run.storyboard_project_id,
        run_id=run.id,
        actor_user_uuid=actor_user_uuid,
        action=f"run_{action}",
        detail={"run_id": run.public_id},
    )
    append_event(
        db,
        storyboard_project_id=run.storyboard_project_id,
        storyboard_run_id=run.id,
        topic="storyboard.run",
        event_key=f"run:{run.public_id}:action:{action}:{int(_utc_now().timestamp())}",
        payload={"action": action, "status": run.status, "run_state": run.run_state},
    )
    db.flush()
    return run


def persist_character_cards(
    db: Session,
    *,
    project_id: int,
    version_id: int,
    lane: str,
    cards: list[dict[str, Any]],
) -> int:
    """执行 persist character cards 相关辅助逻辑。"""
    db.execute(delete(StoryboardCharacterCard).where(StoryboardCharacterCard.storyboard_version_id == version_id))
    db.execute(delete(StoryboardCharacterPrompt).where(StoryboardCharacterPrompt.storyboard_version_id == version_id))
    created = 0
    for item in cards:
        row = StoryboardCharacterCard(
            storyboard_project_id=project_id,
            storyboard_version_id=version_id,
            lane=lane,
            character_key=str(item.get("character_key") or ""),
            display_name=str(item.get("display_name") or ""),
            skin_tone=str(item.get("skin_tone") or ""),
            ethnicity=str(item.get("ethnicity") or ""),
            master_prompt_text=str(item.get("master_prompt_text") or ""),
            negative_prompt_text=str(item.get("negative_prompt_text") or ""),
            style_tags_json=item.get("style_tags_json") or [],
            consistency_anchors_json=item.get("consistency_anchors_json") or [],
            quality_score=float(item.get("quality_score") or 0.0),
            metadata_json=item.get("metadata_json") or {},
        )
        db.add(row)
        # Keep legacy table updated for old screens/exports.
        db.add(
            StoryboardCharacterPrompt(
                storyboard_project_id=project_id,
                storyboard_version_id=version_id,
                lane=lane,
                character_key=row.character_key,
                display_name=row.display_name,
                skin_tone=row.skin_tone,
                ethnicity=row.ethnicity,
                master_prompt_text=row.master_prompt_text,
                negative_prompt_text=row.negative_prompt_text,
                style_tags_json=row.style_tags_json,
                consistency_anchors_json=row.consistency_anchors_json,
                quality_score=row.quality_score,
            )
        )
        created += 1
    db.flush()
    return created


def get_export_by_public_id(
    db: Session,
    *,
    project_id: int,
    export_public_id: str,
) -> StoryboardExport | None:
    """获取 export by public id。"""
    return db.execute(
        select(StoryboardExport).where(
            StoryboardExport.storyboard_project_id == project_id,
            StoryboardExport.public_id == export_public_id,
        )
    ).scalar_one_or_none()
