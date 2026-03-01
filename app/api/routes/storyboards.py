"""Storyboard project routes."""
from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timezone
from difflib import SequenceMatcher

import redis
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authz.deps import require_permission
from app.core.authz.resources import load_storyboard_resource
from app.core.authz.types import Permission, Principal
from app.core.config import get_settings
from app.core.database import get_db, resolve_novel
from app.core.logging_config import log_event
from app.prompts import render_prompt
from app.core.time_utils import to_utc_iso_z
from app.models.novel import Chapter, Novel
from app.models.storyboard import (
    StoryboardAssertion,
    StoryboardCharacterPrompt,
    StoryboardProject,
    StoryboardShot,
    StoryboardTask,
    StoryboardVersion,
)
from app.schemas.storyboard import (
    StoryboardActionResponse,
    StoryboardCharacterGenerateResponse,
    StoryboardCharacterPromptResponse,
    StoryboardCreateRequest,
    StoryboardDiffResponse,
    StoryboardGenerateResponse,
    StoryboardOptimizeResponse,
    StoryboardProjectResponse,
    StoryboardStylePresetsResponse,
    StoryboardStyleRecommendationRequest,
    StoryboardStyleRecommendationResponse,
    StoryboardShotResponse,
    StoryboardShotUpdateRequest,
    StoryboardTaskStatusResponse,
    StoryboardVersionResponse,
)
from app.services.storyboard.exporter import export_shots_to_csv
from app.services.storyboard.character_prompts import (
    compose_character_prompts_for_version,
    export_character_prompts_csv,
    list_character_prompts,
)
from app.services.storyboard.service import (
    RUNNING_STATES,
    apply_rewrite_suggestions_to_shots,
    create_generation_versions,
    create_project,
    create_task_record,
    format_eta,
    get_latest_task,
    get_project_or_404,
    list_projects,
    normalize_lanes,
    project_config,
    task_status_payload,
    update_task_state,
)
from app.services.storyboard.style_catalog import list_style_presets, recommend_styles
from app.services.scheduler.scheduler_service import (
    cancel_task as cancel_creation_task,
    pause_task as pause_creation_task,
    resume_task as resume_creation_task,
    submit_task as submit_creation_task,
)

router = APIRouter()
logger = logging.getLogger(__name__)


_redis_pool = None


def _get_redis() -> redis.Redis:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = redis.ConnectionPool.from_url(get_settings().redis_url)
    return redis.Redis(connection_pool=_redis_pool)


def _redis_key(task_id: str) -> str:
    return f"storyboard:task:{task_id}"


def _to_project_response(p: StoryboardProject, novel_public_id: str) -> StoryboardProjectResponse:
    lanes = p.output_lanes if isinstance(p.output_lanes, list) else ["vertical_feed", "horizontal_cinematic"]
    cfg = project_config(p)
    return StoryboardProjectResponse(
        id=p.id,
        uuid=p.uuid,
        novel_id=novel_public_id,
        status=p.status,
        target_episodes=p.target_episodes,
        target_episode_seconds=p.target_episode_seconds,
        style_profile=p.style_profile,
        professional_mode=bool(p.professional_mode),
        audience_goal=p.audience_goal,
        mode=cfg["mode"],
        genre_style_key=cfg["genre_style_key"],
        director_style_key=cfg["director_style_key"],
        style_recommendations=cfg["style_recommendations"],
        output_lanes=normalize_lanes(lanes),
        active_lane=p.active_lane,
        created_at=to_utc_iso_z(p.created_at),
        updated_at=to_utc_iso_z(p.updated_at),
    )


def _to_version_response(v: StoryboardVersion) -> StoryboardVersionResponse:
    return StoryboardVersionResponse(
        id=v.id,
        storyboard_project_id=v.storyboard_project_id,
        version_no=v.version_no,
        parent_version_id=v.parent_version_id,
        lane=v.lane,
        status=v.status,
        is_default=bool(v.is_default),
        is_final=bool(v.is_final),
        quality_report_json=v.quality_report_json if isinstance(v.quality_report_json, dict) else {},
        created_at=to_utc_iso_z(v.created_at),
        updated_at=to_utc_iso_z(v.updated_at),
    )


def _to_shot_response(s: StoryboardShot) -> StoryboardShotResponse:
    return StoryboardShotResponse(
        id=s.id,
        storyboard_version_id=s.storyboard_version_id,
        episode_no=s.episode_no,
        scene_no=s.scene_no,
        shot_no=s.shot_no,
        location=s.location,
        time_of_day=s.time_of_day,
        shot_size=s.shot_size,
        camera_angle=s.camera_angle,
        camera_move=s.camera_move,
        duration_sec=s.duration_sec,
        characters_json=s.characters_json or [],
        action=s.action,
        dialogue=s.dialogue,
        emotion_beat=s.emotion_beat,
        transition=s.transition,
        sound_hint=s.sound_hint,
        production_note=s.production_note,
        blocking=s.blocking,
        motivation=s.motivation,
        performance_note=s.performance_note,
        continuity_anchor=s.continuity_anchor,
        created_at=to_utc_iso_z(s.created_at),
        updated_at=to_utc_iso_z(s.updated_at),
    )


def _to_character_prompt_response(s: StoryboardCharacterPrompt) -> StoryboardCharacterPromptResponse:
    return StoryboardCharacterPromptResponse(
        id=s.id,
        storyboard_project_id=s.storyboard_project_id,
        storyboard_version_id=s.storyboard_version_id,
        lane=s.lane,
        character_key=s.character_key,
        display_name=s.display_name,
        skin_tone=s.skin_tone,
        ethnicity=s.ethnicity,
        master_prompt_text=s.master_prompt_text,
        negative_prompt_text=s.negative_prompt_text,
        style_tags_json=[str(x) for x in (s.style_tags_json or []) if str(x).strip()],
        consistency_anchors_json=[str(x) for x in (s.consistency_anchors_json or []) if str(x).strip()],
        quality_score=s.quality_score,
        created_at=to_utc_iso_z(s.created_at),
        updated_at=to_utc_iso_z(s.updated_at),
    )


def _active_task_or_404(db: Session, project_id: int, task_id: str | None) -> StoryboardTask:
    if task_id:
        row = db.execute(
            select(StoryboardTask)
            .where(StoryboardTask.storyboard_project_id == project_id, StoryboardTask.task_id == task_id)
            .limit(1)
        ).scalar_one_or_none()
        if row:
            return row
        raise HTTPException(404, "Storyboard task not found")
    row = get_latest_task(db, project_id)
    if row:
        return row
    raise HTTPException(404, "Storyboard task not found")


@router.get("/style-presets", response_model=StoryboardStylePresetsResponse)
def get_storyboard_style_presets(
    _: Principal = Depends(require_permission(Permission.STORYBOARD_CREATE)),
):
    return StoryboardStylePresetsResponse(**list_style_presets())


@router.post("/style-recommendations", response_model=StoryboardStyleRecommendationResponse)
def get_storyboard_style_recommendations(
    req: StoryboardStyleRecommendationRequest,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.STORYBOARD_CREATE)),
):
    novel = resolve_novel(db, req.novel_id)
    if not novel:
        raise HTTPException(404, "Novel not found")
    rows = db.execute(
        select(Chapter).where(Chapter.novel_id == novel.id).order_by(Chapter.chapter_num.asc()).limit(12)
    ).scalars().all()
    chapter_text = " ".join([((c.summary or c.content or "")[:180]) for c in rows])
    return StoryboardStyleRecommendationResponse(
        novel_id=novel.uuid or str(novel.id),
        recommendations=recommend_styles(novel, chapter_text),
    )


@router.get("", response_model=list[StoryboardProjectResponse])
def get_storyboard_projects(
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(Permission.STORYBOARD_READ)),
):
    rows = list_projects(db, principal.role, principal.user_uuid)
    novel_ids = {int(r.novel_id) for r in rows}
    novel_map: dict[int, str] = {}
    if novel_ids:
        novels = db.execute(select(Novel).where(Novel.id.in_(novel_ids))).scalars().all()
        novel_map = {int(n.id): (n.uuid or str(n.id)) for n in novels}
    return [_to_project_response(r, novel_map.get(int(r.novel_id), str(r.novel_id))) for r in rows]


@router.post("", response_model=StoryboardProjectResponse)
def create_storyboard_project(
    req: StoryboardCreateRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(Permission.STORYBOARD_CREATE)),
):
    novel = resolve_novel(db, req.novel_id)
    if not novel:
        raise HTTPException(404, "Novel not found")
    if novel.status != "completed":
        raise HTTPException(409, "仅已完成小说可创建导演分镜项目")
    if not req.professional_mode:
        raise HTTPException(400, "professional_mode 在 V1 必须为 true")
    if not req.copyright_assertion:
        raise HTTPException(400, "请先确认改编权声明")

    project = create_project(
        db,
        novel=novel,
        owner_user_uuid=principal.user_uuid or "",
        target_episodes=req.target_episodes,
        target_episode_seconds=req.target_episode_seconds,
        style_profile=req.style_profile,
        mode=req.mode,
        genre_style_key=req.genre_style_key,
        director_style_key=req.director_style_key,
        auto_style_recommendation=req.auto_style_recommendation,
        output_lanes=req.output_lanes,
        audience_goal=req.audience_goal,
        copyright_assertion=req.copyright_assertion,
    )
    db.commit()
    db.refresh(project)
    log_event(
        logger,
        "storyboard.project.created",
        novel_id=novel.id,
        storyboard_project_id=project.id,
        user_id=principal.user_uuid,
        output_lanes=project.output_lanes,
    )
    return _to_project_response(project, novel.uuid or str(novel.id))


@router.post("/{project_id}/generate", response_model=StoryboardGenerateResponse)
def generate_storyboard(
    project_id: int,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(Permission.STORYBOARD_GENERATE, resource_loader=load_storyboard_resource)),
):
    project = get_project_or_404(db, project_id)
    if not project:
        raise HTTPException(404, "Storyboard project not found")

    active = db.execute(
        select(StoryboardTask)
        .where(
            StoryboardTask.storyboard_project_id == project.id,
            StoryboardTask.status.in_(RUNNING_STATES),
        )
        .limit(1)
    ).scalar_one_or_none()
    if active:
        raise HTTPException(409, "已有进行中的分镜生成任务")

    lanes = normalize_lanes(project.output_lanes if isinstance(project.output_lanes, list) else None)
    versions = create_generation_versions(db, project.id, lanes)
    task = create_task_record(
        db,
        project_id=project.id,
        task_id=f"pending-{project.id}-{int(datetime.now(timezone.utc).timestamp())}",
        trace_id=getattr(request.state, "trace_id", None),
    )
    creation_task = submit_creation_task(
        db,
        user_uuid=principal.user_uuid or "",
        task_type="storyboard",
        resource_type="storyboard_project",
        resource_id=int(project.id),
        payload={
            "project_id": int(project.id),
            "version_ids": [int(v.id) for v in versions],
            "task_db_id": int(task.id),
        },
    )
    task.task_id = creation_task.public_id
    task.status = "submitted"
    task.run_state = "submitted"
    task.current_phase = "queued"
    project.status = "generating"
    db.commit()

    log_event(
        logger,
        "storyboard.generate.submit",
        task_id=task.task_id,
        storyboard_project_id=project.id,
        novel_id=project.novel_id,
        run_state="submitted",
    )

    return StoryboardGenerateResponse(task_id=task.task_id, storyboard_project_id=project.id, created_version_ids=[v.id for v in versions])


@router.get("/{project_id}/status", response_model=StoryboardTaskStatusResponse)
def get_storyboard_status(
    project_id: int,
    task_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.STORYBOARD_READ, resource_loader=load_storyboard_resource)),
):
    row = _active_task_or_404(db, project_id, task_id)
    raw = _get_redis().get(_redis_key(row.task_id))
    if raw:
        try:
            payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
            if isinstance(payload, dict):
                payload.setdefault("storyboard_project_id", project_id)
                payload["eta_label"] = format_eta(payload.get("eta_seconds"))
                gate = row.gate_report_json if isinstance(row.gate_report_json, dict) else {}
                payload.setdefault("style_consistency_score", gate.get("style_consistency_score"))
                payload.setdefault("hook_score_episode", gate.get("hook_score_episode"))
                payload.setdefault("quality_gate_reasons", gate.get("quality_gate_reasons"))
                payload.setdefault("character_prompt_phase", gate.get("character_prompt_phase"))
                payload.setdefault("character_profiles_count", gate.get("character_profiles_count"))
                payload.setdefault("missing_identity_fields_count", gate.get("missing_identity_fields_count"))
                payload.setdefault("failed_identity_characters", gate.get("failed_identity_characters"))
                return StoryboardTaskStatusResponse(**payload)
        except Exception:
            pass
    return StoryboardTaskStatusResponse(**task_status_payload(row))


@router.post("/{project_id}/pause", response_model=StoryboardActionResponse)
def pause_storyboard_task(
    project_id: int,
    task_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(Permission.STORYBOARD_GENERATE, resource_loader=load_storyboard_resource)),
):
    row = _active_task_or_404(db, project_id, task_id)
    if row.run_state not in {"running", "retrying", "submitted"}:
        raise HTTPException(409, f"当前状态 {row.run_state} 不支持暂停")
    try:
        pause_creation_task(db, public_id=row.task_id, user_uuid=principal.user_uuid or "")
    except ValueError:
        db.rollback()
        raise HTTPException(409, "当前任务不可暂停")
    update_task_state(db, row, status="paused", run_state="paused", phase="paused", message="分镜任务已暂停")
    db.commit()
    return StoryboardActionResponse(ok=True, storyboard_project_id=project_id, task_id=row.task_id, run_state=row.run_state)


@router.post("/{project_id}/resume", response_model=StoryboardActionResponse)
def resume_storyboard_task(
    project_id: int,
    task_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(Permission.STORYBOARD_GENERATE, resource_loader=load_storyboard_resource)),
):
    row = _active_task_or_404(db, project_id, task_id)
    if row.run_state != "paused":
        raise HTTPException(409, f"当前状态 {row.run_state} 不支持恢复")
    try:
        resume_creation_task(db, public_id=row.task_id, user_uuid=principal.user_uuid or "")
    except ValueError:
        db.rollback()
        raise HTTPException(409, "当前任务不可恢复")
    update_task_state(db, row, status="running", run_state="running", phase="resume", message="分镜任务已恢复")
    db.commit()
    return StoryboardActionResponse(ok=True, storyboard_project_id=project_id, task_id=row.task_id, run_state=row.run_state)


@router.post("/{project_id}/cancel", response_model=StoryboardActionResponse)
def cancel_storyboard_task(
    project_id: int,
    task_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(Permission.STORYBOARD_GENERATE, resource_loader=load_storyboard_resource)),
):
    row = _active_task_or_404(db, project_id, task_id)
    if row.run_state in {"completed", "failed", "cancelled"}:
        raise HTTPException(409, f"当前状态 {row.run_state} 不支持取消")
    try:
        cancel_creation_task(db, public_id=row.task_id, user_uuid=principal.user_uuid or "")
    except ValueError:
        db.rollback()
        raise HTTPException(404, "Task not found")
    update_task_state(db, row, status="cancelled", run_state="cancelled", phase="cancelled", message="分镜任务已取消")
    project = get_project_or_404(db, project_id)
    if project:
        project.status = "failed"
    db.commit()
    return StoryboardActionResponse(ok=True, storyboard_project_id=project_id, task_id=row.task_id, run_state=row.run_state)


@router.post("/{project_id}/retry", response_model=StoryboardActionResponse)
def retry_storyboard_task(
    project_id: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(Permission.STORYBOARD_GENERATE, resource_loader=load_storyboard_resource)),
):
    project = get_project_or_404(db, project_id)
    if not project:
        raise HTTPException(404, "Storyboard project not found")
    latest = get_latest_task(db, project_id)
    if latest and latest.status in RUNNING_STATES:
        raise HTTPException(409, "已有进行中的分镜任务")

    lanes = normalize_lanes(project.output_lanes if isinstance(project.output_lanes, list) else None)
    versions = create_generation_versions(db, project.id, lanes)
    task = create_task_record(
        db,
        project_id=project.id,
        task_id=f"pending-retry-{project.id}-{int(datetime.now(timezone.utc).timestamp())}",
    )
    creation_task = submit_creation_task(
        db,
        user_uuid=principal.user_uuid or "",
        task_type="storyboard",
        resource_type="storyboard_project",
        resource_id=int(project.id),
        payload={
            "project_id": int(project.id),
            "version_ids": [int(v.id) for v in versions],
            "task_db_id": int(task.id),
        },
    )
    update_task_state(db, task, status="submitted", run_state="submitted", phase="queued", message="重试任务已提交")
    task.task_id = creation_task.public_id
    project.status = "generating"
    db.commit()

    return StoryboardActionResponse(ok=True, storyboard_project_id=project.id, task_id=task.task_id, run_state=task.run_state)


@router.get("/{project_id}/versions", response_model=list[StoryboardVersionResponse])
def list_storyboard_versions(
    project_id: int,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.STORYBOARD_READ, resource_loader=load_storyboard_resource)),
):
    rows = db.execute(
        select(StoryboardVersion)
        .where(StoryboardVersion.storyboard_project_id == project_id)
        .order_by(StoryboardVersion.version_no.desc(), StoryboardVersion.id.desc())
    ).scalars().all()
    return [_to_version_response(v) for v in rows]


@router.post("/{project_id}/versions/{version_id}/activate", response_model=StoryboardActionResponse)
def activate_storyboard_version(
    project_id: int,
    version_id: int,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.STORYBOARD_UPDATE, resource_loader=load_storyboard_resource)),
):
    version = db.execute(
        select(StoryboardVersion).where(
            StoryboardVersion.id == version_id,
            StoryboardVersion.storyboard_project_id == project_id,
        )
    ).scalar_one_or_none()
    if not version:
        raise HTTPException(404, "Version not found")
    versions = db.execute(
        select(StoryboardVersion).where(StoryboardVersion.storyboard_project_id == project_id)
    ).scalars().all()
    for v in versions:
        v.is_default = 1 if v.id == version_id else 0
    project = get_project_or_404(db, project_id)
    if project:
        project.active_lane = version.lane
    db.commit()
    return StoryboardActionResponse(ok=True, storyboard_project_id=project_id)


@router.post("/{project_id}/versions/{version_id}/finalize", response_model=StoryboardActionResponse)
def finalize_storyboard_version(
    project_id: int,
    version_id: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(Permission.STORYBOARD_FINALIZE, resource_loader=load_storyboard_resource)),
):
    version = db.execute(
        select(StoryboardVersion).where(
            StoryboardVersion.id == version_id,
            StoryboardVersion.storyboard_project_id == project_id,
        )
    ).scalar_one_or_none()
    if not version:
        raise HTTPException(404, "Version not found")
    if version.status != "completed":
        raise HTTPException(409, "仅 completed 版本可定稿")
    prompts_count = db.execute(
        select(StoryboardCharacterPrompt.id)
        .where(StoryboardCharacterPrompt.storyboard_version_id == version_id)
        .limit(1)
    ).scalar_one_or_none()
    if prompts_count is None:
        raise HTTPException(409, "角色主形象提示词尚未生成，暂不可定稿")
    report = version.quality_report_json if isinstance(version.quality_report_json, dict) else {}
    missing_identity_fields_count = int(report.get("missing_identity_fields_count") or 0)
    if missing_identity_fields_count > 0:
        raise HTTPException(409, "角色身份字段门禁未通过，暂不可定稿")

    versions = db.execute(
        select(StoryboardVersion).where(StoryboardVersion.storyboard_project_id == project_id)
    ).scalars().all()
    for v in versions:
        v.is_final = 1 if v.id == version_id else 0
        if v.id == version_id:
            v.is_default = 1

    project = get_project_or_404(db, project_id)
    if project:
        project.status = "finalized"
        project.active_lane = version.lane

    db.add(
        StoryboardAssertion(
            storyboard_project_id=project_id,
            user_uuid=principal.user_uuid or "",
            assertion_type="manual_finalize_gate",
            assertion_text="用户确认导演分镜定稿。",
        )
    )
    db.commit()

    log_event(
        logger,
        "storyboard.version.finalized",
        storyboard_project_id=project_id,
        version_id=version_id,
        user_id=principal.user_uuid,
    )
    return StoryboardActionResponse(ok=True, storyboard_project_id=project_id)


@router.get("/{project_id}/shots", response_model=list[StoryboardShotResponse])
def list_storyboard_shots(
    project_id: int,
    version_id: int | None = Query(default=None),
    episode_no: int | None = Query(default=None),
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.STORYBOARD_READ, resource_loader=load_storyboard_resource)),
):
    if version_id is None:
        version = db.execute(
            select(StoryboardVersion)
            .where(StoryboardVersion.storyboard_project_id == project_id, StoryboardVersion.is_default == 1)
            .order_by(StoryboardVersion.id.desc())
        ).scalar_one_or_none()
        if not version:
            raise HTTPException(404, "No default storyboard version")
    else:
        version = db.execute(
            select(StoryboardVersion)
            .where(StoryboardVersion.id == version_id, StoryboardVersion.storyboard_project_id == project_id)
        ).scalar_one_or_none()
        if not version:
            raise HTTPException(404, "Version not found")

    stmt = select(StoryboardShot).where(StoryboardShot.storyboard_version_id == version.id)
    if episode_no is not None:
        stmt = stmt.where(StoryboardShot.episode_no == episode_no)
    rows = db.execute(
        stmt.order_by(StoryboardShot.episode_no.asc(), StoryboardShot.scene_no.asc(), StoryboardShot.shot_no.asc())
    ).scalars().all()
    return [_to_shot_response(s) for s in rows]


@router.get("/{project_id}/characters", response_model=list[StoryboardCharacterPromptResponse])
def list_storyboard_character_prompts(
    project_id: int,
    version_id: int | None = None,
    lane: str | None = None,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.STORYBOARD_READ, resource_loader=load_storyboard_resource)),
):
    project = get_project_or_404(db, project_id)
    if not project:
        raise HTTPException(404, "Storyboard project not found")
    version: StoryboardVersion | None = None
    if version_id is None:
        version = db.execute(
            select(StoryboardVersion)
            .where(StoryboardVersion.storyboard_project_id == project_id, StoryboardVersion.is_default == 1)
            .limit(1)
        ).scalar_one_or_none()
        if not version:
            raise HTTPException(404, "No default storyboard version")
        version_id = version.id
    rows = list_character_prompts(db, project_id=project_id, version_id=version_id, lane=lane)
    return [_to_character_prompt_response(r) for r in rows]


@router.post("/{project_id}/characters/generate", response_model=StoryboardCharacterGenerateResponse)
def regenerate_storyboard_character_prompts(
    project_id: int,
    version_id: int | None = Query(default=None),
    lane: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.STORYBOARD_UPDATE, resource_loader=load_storyboard_resource)),
):
    project = get_project_or_404(db, project_id)
    if not project:
        raise HTTPException(404, "Storyboard project not found")
    if version_id is None:
        version = db.execute(
            select(StoryboardVersion)
            .where(StoryboardVersion.storyboard_project_id == project_id, StoryboardVersion.is_default == 1)
            .limit(1)
        ).scalar_one_or_none()
        if not version:
            raise HTTPException(404, "No default storyboard version")
    else:
        version = db.execute(
            select(StoryboardVersion).where(
                StoryboardVersion.id == version_id,
                StoryboardVersion.storyboard_project_id == project_id,
            )
        ).scalar_one_or_none()
        if not version:
            raise HTTPException(404, "Version not found")
    if lane and version.lane != lane:
        raise HTTPException(400, "lane 与 version 不匹配")
    novel = db.execute(select(Novel).where(Novel.id == project.novel_id)).scalar_one_or_none()
    if not novel:
        raise HTTPException(404, "Novel not found")
    report = compose_character_prompts_for_version(
        db=db,
        project=project,
        version=version,
        novel=novel,
        force_regenerate=True,
    )
    gate = {
        "character_prompt_phase": "character_prompt_compose",
        "character_profiles_count": int(report.get("profiles_count") or 0),
        "missing_identity_fields_count": int(report.get("missing_identity_fields_count") or 0),
        "failed_identity_characters": report.get("failed_identity_characters") or [],
    }
    version.quality_report_json = {**(version.quality_report_json or {}), **gate}
    latest = get_latest_task(db, project_id)
    if latest:
        latest.gate_report_json = {**(latest.gate_report_json or {}), **gate}
    db.commit()
    return StoryboardCharacterGenerateResponse(
        ok=True,
        storyboard_project_id=project_id,
        storyboard_version_id=version.id,
        lane=version.lane,
        generated_count=int(report.get("generated_count") or 0),
        profiles_count=int(report.get("profiles_count") or 0),
        missing_identity_fields_count=int(report.get("missing_identity_fields_count") or 0),
        failed_identity_characters=report.get("failed_identity_characters") or [],
    )


@router.put("/{project_id}/shots/{shot_id}", response_model=StoryboardShotResponse)
def update_storyboard_shot(
    project_id: int,
    shot_id: int,
    req: StoryboardShotUpdateRequest,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.STORYBOARD_UPDATE, resource_loader=load_storyboard_resource)),
):
    shot = db.execute(
        select(StoryboardShot)
        .join(StoryboardVersion, StoryboardVersion.id == StoryboardShot.storyboard_version_id)
        .where(StoryboardShot.id == shot_id, StoryboardVersion.storyboard_project_id == project_id)
    ).scalar_one_or_none()
    if not shot:
        raise HTTPException(404, "Shot not found")

    version = db.execute(select(StoryboardVersion).where(StoryboardVersion.id == shot.storyboard_version_id)).scalar_one_or_none()
    if version and bool(version.is_final):
        raise HTTPException(409, "定稿版本不可编辑")

    for key, value in req.model_dump(exclude_unset=True).items():
        setattr(shot, key, value)
    db.commit()
    db.refresh(shot)
    return _to_shot_response(shot)


@router.post("/{project_id}/versions/{version_id}/optimize", response_model=StoryboardOptimizeResponse)
def optimize_storyboard_version(
    project_id: int,
    version_id: int,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.STORYBOARD_UPDATE, resource_loader=load_storyboard_resource)),
):
    version = db.execute(
        select(StoryboardVersion).where(
            StoryboardVersion.id == version_id,
            StoryboardVersion.storyboard_project_id == project_id,
        )
    ).scalar_one_or_none()
    if not version:
        raise HTTPException(404, "Version not found")
    if bool(version.is_final):
        raise HTTPException(409, "定稿版本不可优化")

    shots = db.execute(
        select(StoryboardShot)
        .where(StoryboardShot.storyboard_version_id == version_id)
        .order_by(StoryboardShot.episode_no.asc(), StoryboardShot.scene_no.asc(), StoryboardShot.shot_no.asc())
    ).scalars().all()
    if not shots:
        raise HTTPException(404, "No shots found")

    report = version.quality_report_json if isinstance(version.quality_report_json, dict) else {}
    suggestions = report.get("rewrite_suggestions") or [
        render_prompt("storyboard_rewrite_suggestion_hook").strip(),
        render_prompt("storyboard_rewrite_suggestion_fields").strip(),
    ]
    optimized_count = apply_rewrite_suggestions_to_shots(shots, suggestions)
    report["optimized_shots"] = optimized_count
    report["optimization_applied_at"] = to_utc_iso_z(datetime.now(timezone.utc))
    version.quality_report_json = report
    db.commit()
    return StoryboardOptimizeResponse(
        ok=True,
        storyboard_project_id=project_id,
        version_id=version_id,
        optimized_shots=optimized_count,
        quality_report_json=report,
    )


@router.get("/{project_id}/versions/{version_id}/diff", response_model=StoryboardDiffResponse)
def storyboard_diff(
    project_id: int,
    version_id: int,
    compare_to: int,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.STORYBOARD_READ, resource_loader=load_storyboard_resource)),
):
    left = db.execute(
        select(StoryboardShot)
        .where(StoryboardShot.storyboard_version_id == compare_to)
        .order_by(StoryboardShot.episode_no.asc(), StoryboardShot.scene_no.asc(), StoryboardShot.shot_no.asc())
    ).scalars().all()
    right = db.execute(
        select(StoryboardShot)
        .where(StoryboardShot.storyboard_version_id == version_id)
        .order_by(StoryboardShot.episode_no.asc(), StoryboardShot.scene_no.asc(), StoryboardShot.shot_no.asc())
    ).scalars().all()
    if not left or not right:
        raise HTTPException(404, "version not found or empty")

    left_map = {(s.episode_no, s.scene_no, s.shot_no): s for s in left}
    right_map = {(s.episode_no, s.scene_no, s.shot_no): s for s in right}
    keys = sorted(set(left_map.keys()) | set(right_map.keys()))
    per_episode: dict[int, dict[str, int]] = {}
    changed = 0
    for key in keys:
        left_shot = left_map.get(key)
        right_shot = right_map.get(key)
        ep = key[0]
        bucket = per_episode.setdefault(ep, {"added": 0, "removed": 0, "changed": 0})
        if left_shot and not right_shot:
            bucket["removed"] += 1
            changed += 1
            continue
        if right_shot and not left_shot:
            bucket["added"] += 1
            changed += 1
            continue
        ltxt = f"{left_shot.action or ''}\n{left_shot.dialogue or ''}\n{left_shot.motivation or ''}"
        rtxt = f"{right_shot.action or ''}\n{right_shot.dialogue or ''}\n{right_shot.motivation or ''}"
        sim = SequenceMatcher(None, ltxt, rtxt).ratio()
        if sim < 0.985:
            bucket["changed"] += 1
            changed += 1

    episodes = [{"episode_no": ep, **stat} for ep, stat in sorted(per_episode.items())]
    return StoryboardDiffResponse(
        storyboard_project_id=project_id,
        version_id=version_id,
        compare_to=compare_to,
        summary={
            "total_shots": len(keys),
            "changed_shots": changed,
            "change_ratio": round(changed / max(1, len(keys)), 4),
        },
        episodes=episodes,
    )


@router.get("/{project_id}/export/csv")
def export_storyboard_csv(
    project_id: int,
    version_id: int,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.STORYBOARD_EXPORT, resource_loader=load_storyboard_resource)),
):
    version = db.execute(
        select(StoryboardVersion).where(
            StoryboardVersion.id == version_id,
            StoryboardVersion.storyboard_project_id == project_id,
        )
    ).scalar_one_or_none()
    if not version:
        raise HTTPException(404, "Version not found")
    if not bool(version.is_final):
        raise HTTPException(409, "仅定稿版本允许导出")
    report = version.quality_report_json if isinstance(version.quality_report_json, dict) else {}
    if int(report.get("missing_identity_fields_count") or 0) > 0:
        raise HTTPException(409, "角色身份字段门禁未通过，暂不可导出")

    shots = db.execute(
        select(StoryboardShot)
        .where(StoryboardShot.storyboard_version_id == version_id)
        .order_by(StoryboardShot.episode_no.asc(), StoryboardShot.scene_no.asc(), StoryboardShot.shot_no.asc())
    ).scalars().all()
    if not shots:
        raise HTTPException(404, "No shots found")

    content = export_shots_to_csv(shots)
    filename = f"storyboard-p{project_id}-v{version.version_no}-{version.lane}.csv"
    buffer = io.BytesIO(content.encode("utf-8-sig"))
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    log_event(
        logger,
        "storyboard.export.csv",
        storyboard_project_id=project_id,
        version_id=version_id,
        shot_count=len(shots),
    )
    return StreamingResponse(buffer, media_type="text/csv", headers=headers)


@router.get("/{project_id}/characters/export")
def export_storyboard_characters(
    project_id: int,
    version_id: int,
    lane: str | None = Query(default=None),
    format: str = Query(default="csv"),
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.STORYBOARD_EXPORT, resource_loader=load_storyboard_resource)),
):
    version = db.execute(
        select(StoryboardVersion).where(
            StoryboardVersion.id == version_id,
            StoryboardVersion.storyboard_project_id == project_id,
        )
    ).scalar_one_or_none()
    if not version:
        raise HTTPException(404, "Version not found")
    if not bool(version.is_final):
        raise HTTPException(409, "仅定稿版本允许导出")
    if int((version.quality_report_json or {}).get("missing_identity_fields_count") or 0) > 0:
        raise HTTPException(409, "角色身份字段门禁未通过，暂不可导出")
    lane_filter = lane or version.lane
    rows = list_character_prompts(db, project_id=project_id, version_id=version_id, lane=lane_filter)
    if not rows:
        raise HTTPException(404, "No character prompts found")

    fmt = str(format or "csv").strip().lower()
    if fmt == "json":
        payload = [_to_character_prompt_response(r).model_dump() for r in rows]
        return payload
    if fmt != "csv":
        raise HTTPException(400, "format must be csv or json")

    content = export_character_prompts_csv(rows)
    filename = f"character-prompts-p{project_id}-v{version.version_no}-{lane_filter}.csv"
    buffer = io.BytesIO(content.encode("utf-8-sig"))
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    log_event(
        logger,
        "storyboard.character_prompt.export",
        storyboard_project_id=project_id,
        version_id=version_id,
        lane=lane_filter,
        character_count=len(rows),
    )
    return StreamingResponse(buffer, media_type="text/csv", headers=headers)
