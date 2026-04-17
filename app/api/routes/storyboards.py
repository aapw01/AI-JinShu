"""Storyboard project routes."""
from __future__ import annotations

import io
import json
import logging
import asyncio
from datetime import datetime, timezone
from difflib import SequenceMatcher

import redis
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.api_errors import http_error
from app.core.authz.deps import require_permission
from app.core.authz.resources import load_storyboard_resource
from app.core.authz.types import Permission, Principal
from app.core.config import get_settings
from app.core.database import get_db, resolve_novel
from app.core.logging_config import log_event
from app.prompts import render_prompt
from app.core.time_utils import to_utc_iso_z
from app.models.novel import ChapterVersion, Novel, NovelVersion
from app.models.storyboard import (
    StoryboardAssertion,
    StoryboardCharacterCard,
    StoryboardCharacterPrompt,
    StoryboardExport,
    StoryboardRun,
    StoryboardRunLane,
    StoryboardProject,
    StoryboardShot,
    StoryboardTask,
    StoryboardVersion,
)
from app.schemas.storyboard import (
    StoryboardActionResponse,
    StoryboardCharacterCardResponse,
    StoryboardCharacterCardUpdateRequest,
    StoryboardCharacterGenerateResponse,
    StoryboardCharacterPromptResponse,
    StoryboardCreateRequest,
    StoryboardDiffResponse,
    StoryboardExportCreateRequest,
    StoryboardExportCreateResponse,
    StoryboardExportStatusResponse,
    StoryboardGenerateResponse,
    StoryboardGenerateRequest,
    StoryboardOptimizeResponse,
    StoryboardPreflightRequest,
    StoryboardPreflightResponse,
    StoryboardProjectResponse,
    StoryboardRunActionRequest,
    StoryboardRunActionResponse,
    StoryboardRunLaneResponse,
    StoryboardRunResponse,
    StoryboardStylePresetsResponse,
    StoryboardStyleRecommendationRequest,
    StoryboardStyleRecommendationResponse,
    StoryboardShotResponse,
    StoryboardShotUpdateRequest,
    StoryboardTaskStatusResponse,
    StoryboardVersionResponse,
)
from app.services.storyboard.export_v2 import build_export_download_url, open_export_blob, verify_download_signature
from app.services.storyboard.exporter import export_shots_to_csv
from app.services.storyboard.character_prompts import (
    compose_character_prompts_for_version,
    export_character_prompts_csv,
    list_character_prompts,
)
from app.services.storyboard.runtime_v2 import (
    create_run_and_dispatch,
    get_export_by_public_id,
    get_project_or_404 as get_project_or_404_v2,
    get_run_by_public_id,
    list_run_lanes,
    persist_character_cards,
    refresh_run_status,
    run_action as run_action_v2,
    run_preflight,
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
from app.tasks.storyboard import run_storyboard_export
from app.services.rewrite.service import get_default_version_id
from app.services.storyboard.style_catalog import list_style_presets, recommend_styles
from app.services.scheduler.scheduler_service import (
    cancel_task as cancel_creation_task,
    dispatch_user_queue_for_user,
    pause_task as pause_creation_task,
    resume_task as resume_creation_task,
    submit_task as submit_creation_task,
)

router = APIRouter()
logger = logging.getLogger(__name__)


_redis_pool = None


def _get_redis() -> redis.Redis:
    """返回当前任务模块复用的 Redis 客户端。"""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = redis.ConnectionPool.from_url(get_settings().redis_url)
    return redis.Redis(connection_pool=_redis_pool)


def _redis_key(task_id: str) -> str:
    """构造当前业务使用的 Redis 键。"""
    return f"storyboard:task:{task_id}"


def _update_storyboard_redis(task_id: str, status: str, message: str, row: "StoryboardTask | None" = None) -> None:
    """更新分镜Redis。"""
    try:
        import json as _json
        data: dict = {"status": status, "run_state": status, "message": message}
        if row:
            data["progress"] = float(row.progress or 0)
            data["storyboard_project_id"] = row.storyboard_project_id
        _get_redis().setex(_redis_key(task_id), 21600, _json.dumps(data, ensure_ascii=False))
    except Exception:
        pass


def _to_project_response(p: StoryboardProject, novel_public_id: str, novel_title: str | None = None) -> StoryboardProjectResponse:
    """执行 to project response 相关辅助逻辑。"""
    lanes = p.output_lanes if isinstance(p.output_lanes, list) else ["vertical_feed", "horizontal_cinematic"]
    cfg = project_config(p)
    return StoryboardProjectResponse(
        id=p.id,
        uuid=p.uuid,
        novel_id=novel_public_id,
        novel_title=novel_title,
        source_novel_version_id=(int(p.source_novel_version_id) if p.source_novel_version_id is not None else None),
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
    """执行 to version response 相关辅助逻辑。"""
    return StoryboardVersionResponse(
        id=v.id,
        storyboard_project_id=v.storyboard_project_id,
        source_novel_version_id=(int(v.source_novel_version_id) if v.source_novel_version_id is not None else None),
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
    """执行 to shot response 相关辅助逻辑。"""
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
    """执行 to character prompt response 相关辅助逻辑。"""
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


def _to_character_card_response(s: StoryboardCharacterCard) -> StoryboardCharacterCardResponse:
    """执行 to character card response 相关辅助逻辑。"""
    return StoryboardCharacterCardResponse(
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
        metadata_json=s.metadata_json if isinstance(s.metadata_json, dict) else {},
        created_at=to_utc_iso_z(s.created_at),
        updated_at=to_utc_iso_z(s.updated_at),
    )


def _to_run_lane_response(row: StoryboardRunLane) -> StoryboardRunLaneResponse:
    """执行 to run lane response 相关辅助逻辑。"""
    return StoryboardRunLaneResponse(
        id=row.id,
        lane=row.lane,
        storyboard_version_id=row.storyboard_version_id,
        creation_task_public_id=row.creation_task_public_id,
        status=row.status,
        run_state=row.run_state,
        current_phase=row.current_phase,
        progress=float(row.progress or 0.0),
        message=row.message,
        error=row.error,
        error_code=row.error_code,
        error_category=row.error_category,
        gate_report_json=row.gate_report_json if isinstance(row.gate_report_json, dict) else {},
        updated_at=to_utc_iso_z(row.updated_at),
    )


def _to_run_response(run: StoryboardRun, lanes: list[StoryboardRunLane]) -> StoryboardRunResponse:
    """执行 to run response 相关辅助逻辑。"""
    return StoryboardRunResponse(
        id=run.id,
        public_id=run.public_id,
        storyboard_project_id=run.storyboard_project_id,
        status=run.status,
        run_state=run.run_state,
        current_phase=run.current_phase,
        progress=float(run.progress or 0.0),
        message=run.message,
        error=run.error,
        error_code=run.error_code,
        error_category=run.error_category,
        lanes=[_to_run_lane_response(row) for row in lanes],
        created_at=to_utc_iso_z(run.created_at),
        updated_at=to_utc_iso_z(run.updated_at),
        finished_at=to_utc_iso_z(run.finished_at),
    )


def _run_to_legacy_task_payload(run: StoryboardRun, lanes: list[StoryboardRunLane]) -> dict:
    """执行tolegacy任务载荷。"""
    lane = next(
        (
            row
            for row in lanes
            if row.run_state in {"running", "retrying", "submitted", "queued", "dispatching"}
        ),
        lanes[0] if lanes else None,
    )
    gate = lane.gate_report_json if lane and isinstance(lane.gate_report_json, dict) else {}
    return {
        "storyboard_project_id": int(run.storyboard_project_id),
        "task_id": lane.creation_task_public_id if lane else None,
        "status": str(run.status),
        "run_state": str(run.run_state),
        "current_phase": (lane.current_phase if lane else run.current_phase),
        "current_lane": (lane.lane if lane else None),
        "progress": float((lane.progress if lane else run.progress) or 0.0),
        "current_episode": None,
        "eta_seconds": None,
        "eta_label": None,
        "message": (lane.message if lane else run.message),
        "error": (lane.error if lane else run.error),
        "error_code": (lane.error_code if lane else run.error_code),
        "error_category": (lane.error_category if lane else run.error_category),
        "retryable": None,
        "style_consistency_score": gate.get("style_consistency_score"),
        "hook_score_episode": gate.get("hook_score_episode"),
        "quality_gate_reasons": gate.get("quality_gate_reasons"),
        "character_prompt_phase": gate.get("character_prompt_phase"),
        "character_profiles_count": gate.get("character_profiles_count"),
        "missing_identity_fields_count": gate.get("missing_identity_fields_count"),
        "failed_identity_characters": gate.get("failed_identity_characters"),
    }


def _resolve_version_or_404(db: Session, *, project_id: int, version_id: int) -> StoryboardVersion:
    """根据项目和版本 ID 找到目标分镜版本；不存在时返回 404。"""
    version = db.execute(
        select(StoryboardVersion).where(
            StoryboardVersion.id == version_id,
            StoryboardVersion.storyboard_project_id == project_id,
        )
    ).scalar_one_or_none()
    if not version:
        raise http_error(404, "storyboard_version_not_found", "Version not found")
    return version


def _to_export_status_response(row: StoryboardExport, *, include_download: bool = True) -> StoryboardExportStatusResponse:
    """执行 to export status response 相关辅助逻辑。"""
    download_url: str | None = None
    if include_download and row.status == "completed" and row.storage_path:
        download_url = build_export_download_url(
            project_id=int(row.storyboard_project_id),
            export_id=str(row.public_id),
        )
    return StoryboardExportStatusResponse(
        id=str(row.public_id),
        storyboard_project_id=int(row.storyboard_project_id),
        storyboard_version_id=int(row.storyboard_version_id),
        format=str(row.format),
        status=str(row.status),
        file_name=row.file_name,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        error=row.error,
        error_code=row.error_code,
        download_url=download_url,
        created_at=to_utc_iso_z(row.created_at),
        updated_at=to_utc_iso_z(row.updated_at),
    )


def _sse_event(event_type: str, payload: dict) -> str:
    """执行 sse event 相关辅助逻辑。"""
    return f"data: {json.dumps({'type': event_type, 'payload': payload}, ensure_ascii=False)}\n\n"


def _active_task_or_404(db: Session, project_id: int, task_id: str | None) -> StoryboardTask:
    """执行 active task or 404 相关辅助逻辑。"""
    if task_id:
        row = db.execute(
            select(StoryboardTask)
            .where(StoryboardTask.storyboard_project_id == project_id, StoryboardTask.task_id == task_id)
            .limit(1)
        ).scalar_one_or_none()
        if row:
            return row
        raise http_error(404, "storyboard_task_not_found", "Storyboard task not found")
    row = get_latest_task(db, project_id)
    if row:
        return row
    raise http_error(404, "storyboard_task_not_found", "Storyboard task not found")


@router.get("/style-presets", response_model=StoryboardStylePresetsResponse)
def get_storyboard_style_presets(
    _: Principal = Depends(require_permission(Permission.STORYBOARD_CREATE)),
):
    """返回分镜stylepresets。"""
    return StoryboardStylePresetsResponse(**list_style_presets())


@router.post("/style-recommendations", response_model=StoryboardStyleRecommendationResponse)
def get_storyboard_style_recommendations(
    req: StoryboardStyleRecommendationRequest,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.STORYBOARD_CREATE)),
):
    """返回分镜stylerecommendations。"""
    novel = resolve_novel(db, req.novel_id)
    if not novel:
        raise http_error(404, "novel_not_found", "Novel not found")
    rows = db.execute(
        select(ChapterVersion)
        .where(ChapterVersion.novel_version_id == get_default_version_id(db, novel.id))
        .order_by(ChapterVersion.chapter_num.asc())
        .limit(12)
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
    """返回分镜projects。"""
    rows = list_projects(db, principal.role, principal.user_uuid)
    novel_ids = {int(r.novel_id) for r in rows}
    novel_map: dict[int, tuple[str, str]] = {}
    if novel_ids:
        novels = db.execute(select(Novel).where(Novel.id.in_(novel_ids))).scalars().all()
        novel_map = {int(n.id): (n.uuid or str(n.id), n.title or "") for n in novels}
    return [
        _to_project_response(
            r,
            novel_map.get(int(r.novel_id), (str(r.novel_id), ""))[0],
            novel_map.get(int(r.novel_id), (str(r.novel_id), ""))[1],
        )
        for r in rows
    ]


@router.post("", response_model=StoryboardProjectResponse)
def create_storyboard_project(
    req: StoryboardCreateRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(Permission.STORYBOARD_CREATE)),
):
    """创建分镜project。"""
    novel = resolve_novel(db, req.novel_id)
    if not novel:
        raise http_error(404, "novel_not_found", "Novel not found")
    if novel.status != "completed":
        raise http_error(409, "storyboard_create_novel_not_completed", "仅已完成小说可创建导演分镜项目")
    if not req.professional_mode:
        raise http_error(400, "professional_mode_required", "professional_mode 在 V1 必须为 true")
    if not req.copyright_assertion:
        raise http_error(400, "copyright_assertion_required", "请先确认改编权声明")

    source_version_id = int(req.source_novel_version_id or 0)
    if source_version_id > 0:
        version = db.execute(
            select(NovelVersion).where(
                NovelVersion.id == source_version_id,
                NovelVersion.novel_id == novel.id,
            )
        ).scalar_one_or_none()
        if not version:
            raise http_error(400, "invalid_novel_version", "小说版本无效")
    else:
        source_version_id = int(get_default_version_id(db, novel.id))

    project = create_project(
        db,
        novel=novel,
        source_novel_version_id=source_version_id,
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


@router.post("/{project_id}/preflight", response_model=StoryboardPreflightResponse)
def storyboard_preflight(
    project_id: int,
    req: StoryboardPreflightRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(Permission.STORYBOARD_GENERATE, resource_loader=load_storyboard_resource)),
):
    """执行 storyboard preflight 相关辅助逻辑。"""
    project = get_project_or_404_v2(db, project_id)
    if not project:
        raise http_error(404, "storyboard_project_not_found", "Storyboard project not found")
    report = run_preflight(
        db,
        project=project,
        actor_user_uuid=principal.user_uuid or "",
        force_refresh_snapshot=bool(req.force_refresh_snapshot),
    )
    db.commit()
    return StoryboardPreflightResponse(
        ok=bool(report.get("ok")),
        storyboard_project_id=project.id,
        gate_status=str(report.get("gate_status") or "blocked"),
        source_novel_version_id=int(report.get("source_novel_version_id") or 0),
        profiles_count=int(report.get("profiles_count") or 0),
        chapters_count=int(report.get("chapters_count") or 0),
        missing_identity_fields_count=int(report.get("missing_identity_fields_count") or 0),
        failed_identity_characters=report.get("failed_identity_characters") or [],
        snapshot_hash=str(report.get("snapshot_hash") or ""),
    )


@router.post("/{project_id}/runs", response_model=StoryboardRunResponse)
def start_storyboard_run(
    project_id: int,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(Permission.STORYBOARD_GENERATE, resource_loader=load_storyboard_resource)),
):
    """执行 start storyboard run 相关辅助逻辑。"""
    project = get_project_or_404_v2(db, project_id)
    if not project:
        raise http_error(404, "storyboard_project_not_found", "Storyboard project not found")
    try:
        run, lanes = create_run_and_dispatch(
            db,
            project=project,
            actor_user_uuid=principal.user_uuid or "",
            trace_id=getattr(request.state, "trace_id", None),
            idempotency_key=(idempotency_key or "").strip() or None,
        )
    except ValueError as exc:
        code = str(exc)
        if code == "preflight_required":
            raise http_error(409, "storyboard_preflight_required", "请先完成 preflight 门禁检查")
        if code == "run_already_active":
            raise http_error(409, "storyboard_run_active", "已有进行中的分镜运行")
        raise http_error(400, "storyboard_run_invalid", "无法启动分镜运行")
    db.commit()
    dispatch_user_queue_for_user(user_uuid=principal.user_uuid or "")
    return _to_run_response(run, lanes)


@router.get("/{project_id}/runs/{run_id}", response_model=StoryboardRunResponse)
def get_storyboard_run_status(
    project_id: int,
    run_id: str,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.STORYBOARD_READ, resource_loader=load_storyboard_resource)),
):
    """返回分镜run状态。"""
    run = get_run_by_public_id(db, project_id=project_id, run_public_id=run_id)
    if not run:
        raise http_error(404, "storyboard_run_not_found", "Storyboard run not found")
    refreshed = refresh_run_status(db, run_id=run.id) or run
    lanes = list_run_lanes(db, run_id=refreshed.id)
    db.commit()
    return _to_run_response(refreshed, lanes)


@router.get("/{project_id}/runs/{run_id}/events")
def stream_storyboard_run_events(
    project_id: int,
    run_id: str,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.STORYBOARD_READ, resource_loader=load_storyboard_resource)),
):
    """流式返回 storyboard run events。"""
    run = get_run_by_public_id(db, project_id=project_id, run_public_id=run_id)
    if not run:
        raise http_error(404, "storyboard_run_not_found", "Storyboard run not found")

    async def event_stream():
        """持续推送分镜 run 状态变化，直到运行结束或记录消失。"""
        last = None
        while True:
            db.expire_all()
            row = get_run_by_public_id(db, project_id=project_id, run_public_id=run_id)
            if not row:
                yield _sse_event("error", {"code": "storyboard_run_not_found", "message": "Storyboard run not found"})
                break
            lanes = list_run_lanes(db, run_id=row.id)
            payload = _to_run_response(row, lanes).model_dump(mode="json")
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            if encoded != last:
                last = encoded
                yield _sse_event("run_status", payload)
            if str(row.run_state or "") in {"completed", "failed", "cancelled"}:
                break
            await asyncio.sleep(0.8)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.get("/{project_id}/runs", response_model=list[StoryboardRunResponse])
def list_storyboard_runs(
    project_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.STORYBOARD_READ, resource_loader=load_storyboard_resource)),
):
    """列出分镜runs。"""
    rows = db.execute(
        select(StoryboardRun)
        .where(StoryboardRun.storyboard_project_id == project_id)
        .order_by(StoryboardRun.id.desc())
        .limit(limit)
    ).scalars().all()
    out: list[StoryboardRunResponse] = []
    for row in rows:
        lanes = list_run_lanes(db, run_id=row.id)
        out.append(_to_run_response(row, lanes))
    return out


@router.post("/{project_id}/runs/{run_id}/actions", response_model=StoryboardRunActionResponse)
def action_storyboard_run(
    project_id: int,
    run_id: str,
    req: StoryboardRunActionRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(Permission.STORYBOARD_GENERATE, resource_loader=load_storyboard_resource)),
):
    """执行 action storyboard run 相关辅助逻辑。"""
    run = get_run_by_public_id(db, project_id=project_id, run_public_id=run_id)
    if not run:
        raise http_error(404, "storyboard_run_not_found", "Storyboard run not found")
    action = str(req.action or "").strip().lower()
    if action == "retry":
        project = get_project_or_404_v2(db, project_id)
        if not project:
            raise http_error(404, "storyboard_project_not_found", "Storyboard project not found")
        if run.status not in {"failed", "cancelled", "completed"}:
            raise http_error(409, "storyboard_run_not_retryable", "仅终态 run 可重试")
        new_run, _ = create_run_and_dispatch(
            db,
            project=project,
            actor_user_uuid=principal.user_uuid or "",
            trace_id=getattr(request.state, "trace_id", None),
            idempotency_key=(idempotency_key or "").strip() or None,
        )
        db.commit()
        dispatch_user_queue_for_user(user_uuid=principal.user_uuid or "")
        return StoryboardRunActionResponse(
            ok=True,
            storyboard_project_id=project_id,
            run_id=new_run.public_id,
            action=action,
            run_state=new_run.run_state,
            status=new_run.status,
        )
    try:
        run = run_action_v2(db, run=run, action=action, actor_user_uuid=principal.user_uuid or "")
    except ValueError:
        raise http_error(400, "storyboard_run_action_invalid", "不支持的 run action")
    db.commit()
    return StoryboardRunActionResponse(
        ok=True,
        storyboard_project_id=project_id,
        run_id=run.public_id,
        action=action,
        run_state=run.run_state,
        status=run.status,
    )


@router.get("/{project_id}/versions/{version_id}/shots", response_model=list[StoryboardShotResponse])
def list_storyboard_version_shots(
    project_id: int,
    version_id: int,
    episode_no: int | None = Query(default=None),
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.STORYBOARD_READ, resource_loader=load_storyboard_resource)),
):
    """列出分镜版本shots。"""
    _resolve_version_or_404(db, project_id=project_id, version_id=version_id)
    stmt = select(StoryboardShot).where(StoryboardShot.storyboard_version_id == version_id)
    if episode_no is not None:
        stmt = stmt.where(StoryboardShot.episode_no == episode_no)
    rows = db.execute(
        stmt.order_by(StoryboardShot.episode_no.asc(), StoryboardShot.scene_no.asc(), StoryboardShot.shot_no.asc())
    ).scalars().all()
    return [_to_shot_response(row) for row in rows]


@router.post("/{project_id}/generate", response_model=StoryboardGenerateResponse)
def generate_storyboard(
    project_id: int,
    req: StoryboardGenerateRequest,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(Permission.STORYBOARD_GENERATE, resource_loader=load_storyboard_resource)),
):
    """执行 generate storyboard 相关辅助逻辑。"""
    project = get_project_or_404(db, project_id)
    if not project:
        raise http_error(404, "storyboard_project_not_found", "Storyboard project not found")

    active = db.execute(
        select(StoryboardTask)
        .where(
            StoryboardTask.storyboard_project_id == project.id,
            StoryboardTask.status.in_(RUNNING_STATES),
        )
        .limit(1)
    ).scalar_one_or_none()
    if active:
        raise http_error(409, "storyboard_task_running", "已有进行中的分镜生成任务")

    source_version = db.execute(
        select(NovelVersion).where(
            NovelVersion.id == int(req.novel_version_id),
            NovelVersion.novel_id == project.novel_id,
        )
    ).scalar_one_or_none()
    if not source_version:
        raise http_error(400, "invalid_novel_version", "小说版本无效")
    lanes = normalize_lanes(project.output_lanes if isinstance(project.output_lanes, list) else None)
    versions = create_generation_versions(
        db,
        project.id,
        lanes,
        source_novel_version_id=int(source_version.id),
    )
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
            "novel_version_id": int(source_version.id),
            "task_db_id": int(task.id),
        },
    )
    task.task_id = creation_task.public_id
    task.status = "submitted"
    task.run_state = "submitted"
    task.current_phase = "queued"
    project.status = "generating"
    db.commit()
    dispatch_user_queue_for_user(user_uuid=principal.user_uuid or "")

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
    """返回分镜状态。"""
    row = None
    try:
        row = _active_task_or_404(db, project_id, task_id)
    except HTTPException:
        row = None
    if row is None:
        latest_run = db.execute(
            select(StoryboardRun)
            .where(StoryboardRun.storyboard_project_id == project_id)
            .order_by(StoryboardRun.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if not latest_run:
            raise http_error(404, "storyboard_task_not_found", "Storyboard task not found")
        refreshed = refresh_run_status(db, run_id=latest_run.id) or latest_run
        lanes = list_run_lanes(db, run_id=refreshed.id)
        db.commit()
        return StoryboardTaskStatusResponse(**_run_to_legacy_task_payload(refreshed, lanes))
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
                if row.status in ("cancelled", "failed", "completed", "paused"):
                    payload["status"] = row.status
                    payload["run_state"] = row.run_state or row.status
                    payload["message"] = row.message or payload.get("message")
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
    """暂停分镜任务。"""
    row = _active_task_or_404(db, project_id, task_id)
    if row.run_state not in {"running", "retrying", "submitted"}:
        raise http_error(409, "storyboard_task_state_not_pausable", f"当前状态 {row.run_state} 不支持暂停")
    try:
        pause_creation_task(db, public_id=row.task_id, user_uuid=principal.user_uuid or "")
    except ValueError as exc:
        db.rollback()
        code = str(exc)
        if code == "task_not_found":
            raise http_error(404, "task_not_found", "Task not found")
        if code == "task_not_active":
            raise http_error(409, "task_not_active", "Task is not active")
        raise http_error(409, "task_not_pausable", "当前任务不可暂停")
    update_task_state(db, row, status="paused", run_state="paused", phase="paused", message="分镜任务已暂停")
    db.commit()
    dispatch_user_queue_for_user(user_uuid=principal.user_uuid or "")
    _update_storyboard_redis(row.task_id, "paused", "分镜任务已暂停", row)
    return StoryboardActionResponse(ok=True, storyboard_project_id=project_id, task_id=row.task_id, run_state=row.run_state)


@router.post("/{project_id}/resume", response_model=StoryboardActionResponse)
def resume_storyboard_task(
    project_id: int,
    task_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(Permission.STORYBOARD_GENERATE, resource_loader=load_storyboard_resource)),
):
    """恢复分镜任务。"""
    row = _active_task_or_404(db, project_id, task_id)
    if row.run_state != "paused":
        raise http_error(409, "storyboard_task_state_not_resumable", f"当前状态 {row.run_state} 不支持恢复")
    try:
        resume_creation_task(db, public_id=row.task_id, user_uuid=principal.user_uuid or "")
    except ValueError as exc:
        db.rollback()
        code = str(exc)
        if code == "task_not_found":
            raise http_error(404, "task_not_found", "Task not found")
        if code == "task_not_active":
            raise http_error(409, "task_not_active", "Task is not active")
        raise http_error(409, "task_not_resumable", "当前任务不可恢复")
    update_task_state(db, row, status="submitted", run_state="queued", phase="queued", message="分镜任务已恢复，等待调度")
    db.commit()
    dispatch_user_queue_for_user(user_uuid=principal.user_uuid or "")
    _update_storyboard_redis(row.task_id, "queued", "分镜任务已恢复，等待调度", row)
    return StoryboardActionResponse(ok=True, storyboard_project_id=project_id, task_id=row.task_id, run_state=row.run_state)


@router.post("/{project_id}/cancel", response_model=StoryboardActionResponse)
def cancel_storyboard_task(
    project_id: int,
    task_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(Permission.STORYBOARD_GENERATE, resource_loader=load_storyboard_resource)),
):
    """执行 cancel storyboard task 相关辅助逻辑。"""
    row = _active_task_or_404(db, project_id, task_id)
    if row.run_state in {"completed", "failed", "cancelled"}:
        raise http_error(409, "storyboard_task_state_not_cancellable", f"当前状态 {row.run_state} 不支持取消")
    try:
        cancel_creation_task(db, public_id=row.task_id, user_uuid=principal.user_uuid or "")
    except ValueError as exc:
        db.rollback()
        code = str(exc)
        if code == "task_not_found":
            raise http_error(404, "task_not_found", "Task not found")
        if code == "task_not_active":
            raise http_error(409, "task_not_active", "Task is not active")
        raise http_error(409, "task_not_cancellable", "当前任务不可取消")
    update_task_state(db, row, status="cancelled", run_state="cancelled", phase="cancelled", message="分镜任务已取消")
    project = get_project_or_404(db, project_id)
    if project:
        project.status = "cancelled"
    db.commit()
    dispatch_user_queue_for_user(user_uuid=principal.user_uuid or "")
    _update_storyboard_redis(row.task_id, "cancelled", "分镜任务已取消", row)
    return StoryboardActionResponse(ok=True, storyboard_project_id=project_id, task_id=row.task_id, run_state=row.run_state)


@router.post("/{project_id}/retry", response_model=StoryboardActionResponse)
def retry_storyboard_task(
    project_id: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(Permission.STORYBOARD_GENERATE, resource_loader=load_storyboard_resource)),
):
    """重试分镜任务。"""
    from app.models.creation_task import CreationTask

    project = get_project_or_404(db, project_id)
    if not project:
        raise http_error(404, "storyboard_project_not_found", "Storyboard project not found")
    latest = get_latest_task(db, project_id)
    if latest and latest.status in RUNNING_STATES:
        raise http_error(409, "storyboard_task_running", "已有进行中的分镜任务")

    existing_creation = db.execute(
        select(CreationTask)
        .where(
            CreationTask.task_type == "storyboard",
            CreationTask.resource_type == "storyboard_project",
            CreationTask.resource_id == int(project.id),
            CreationTask.user_uuid == (principal.user_uuid or ""),
            CreationTask.status.in_({"failed", "paused"}),
        )
        .order_by(CreationTask.updated_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if existing_creation:
        try:
            resumed = resume_creation_task(db, public_id=existing_creation.public_id, user_uuid=principal.user_uuid or "")
        except ValueError:
            resumed = None
        if resumed:
            if latest:
                update_task_state(db, latest, status="submitted", run_state="submitted", phase="queued", message="重试任务已提交（断点续传）")
            project.status = "generating"
            db.commit()
            dispatch_user_queue_for_user(user_uuid=principal.user_uuid or "")
            return StoryboardActionResponse(
                ok=True, storyboard_project_id=project.id,
                task_id=resumed.public_id, run_state="queued",
            )

    source_novel_version_id = get_default_version_id(db, project.novel_id)
    latest_version = db.execute(
        select(StoryboardVersion)
        .where(StoryboardVersion.storyboard_project_id == project.id)
        .order_by(StoryboardVersion.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if latest_version and latest_version.source_novel_version_id:
        source_novel_version_id = int(latest_version.source_novel_version_id)

    lanes = normalize_lanes(project.output_lanes if isinstance(project.output_lanes, list) else None)
    versions = create_generation_versions(
        db,
        project.id,
        lanes,
        source_novel_version_id=source_novel_version_id,
    )
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
            "novel_version_id": int(source_novel_version_id),
            "task_db_id": int(task.id),
        },
    )
    update_task_state(db, task, status="submitted", run_state="submitted", phase="queued", message="重试任务已提交")
    task.task_id = creation_task.public_id
    project.status = "generating"
    db.commit()
    dispatch_user_queue_for_user(user_uuid=principal.user_uuid or "")

    return StoryboardActionResponse(ok=True, storyboard_project_id=project.id, task_id=task.task_id, run_state=task.run_state)


@router.get("/{project_id}/versions", response_model=list[StoryboardVersionResponse])
def list_storyboard_versions(
    project_id: int,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.STORYBOARD_READ, resource_loader=load_storyboard_resource)),
):
    """列出分镜版本。"""
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
    """执行 activate storyboard version 相关辅助逻辑。"""
    version = db.execute(
        select(StoryboardVersion).where(
            StoryboardVersion.id == version_id,
            StoryboardVersion.storyboard_project_id == project_id,
        )
    ).scalar_one_or_none()
    if not version:
        raise http_error(404, "storyboard_version_not_found", "Version not found")
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
    """完成分镜版本的收尾处理。"""
    version = db.execute(
        select(StoryboardVersion).where(
            StoryboardVersion.id == version_id,
            StoryboardVersion.storyboard_project_id == project_id,
        )
    ).scalar_one_or_none()
    if not version:
        raise http_error(404, "storyboard_version_not_found", "Version not found")
    if version.status != "completed":
        raise http_error(409, "storyboard_finalize_requires_completed", "仅 completed 版本可定稿")
    cards_count = db.execute(
        select(StoryboardCharacterCard.id)
        .where(StoryboardCharacterCard.storyboard_version_id == version_id)
        .limit(1)
    ).scalar_one_or_none()
    if cards_count is None:
        prompts_count = db.execute(
            select(StoryboardCharacterPrompt.id)
            .where(StoryboardCharacterPrompt.storyboard_version_id == version_id)
            .limit(1)
        ).scalar_one_or_none()
        if prompts_count is None:
            raise http_error(409, "storyboard_finalize_character_prompt_missing", "角色主形象提示词尚未生成，暂不可定稿")
    report = version.quality_report_json if isinstance(version.quality_report_json, dict) else {}
    missing_identity_fields_count = int(report.get("missing_identity_fields_count") or 0)
    if missing_identity_fields_count > 0:
        raise http_error(409, "storyboard_finalize_identity_gate_failed", "角色身份字段门禁未通过，暂不可定稿")

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
    """列出分镜shots。"""
    if version_id is None:
        version = db.execute(
            select(StoryboardVersion)
            .where(StoryboardVersion.storyboard_project_id == project_id, StoryboardVersion.is_default == 1)
            .order_by(StoryboardVersion.id.desc())
        ).scalar_one_or_none()
        if not version:
            raise http_error(404, "storyboard_default_version_not_found", "No default storyboard version")
    else:
        version = db.execute(
            select(StoryboardVersion)
            .where(StoryboardVersion.id == version_id, StoryboardVersion.storyboard_project_id == project_id)
        ).scalar_one_or_none()
        if not version:
            raise http_error(404, "storyboard_version_not_found", "Version not found")

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
    """列出分镜角色提示词。"""
    project = get_project_or_404(db, project_id)
    if not project:
        raise http_error(404, "storyboard_project_not_found", "Storyboard project not found")
    version: StoryboardVersion | None = None
    if version_id is None:
        version = db.execute(
            select(StoryboardVersion)
            .where(StoryboardVersion.storyboard_project_id == project_id, StoryboardVersion.is_default == 1)
            .limit(1)
        ).scalar_one_or_none()
        if not version:
            raise http_error(404, "storyboard_default_version_not_found", "No default storyboard version")
        version_id = version.id
    rows = list_character_prompts(db, project_id=project_id, version_id=version_id, lane=lane)
    return [_to_character_prompt_response(r) for r in rows]


@router.get("/{project_id}/versions/{version_id}/character-cards", response_model=list[StoryboardCharacterCardResponse])
def list_storyboard_character_cards(
    project_id: int,
    version_id: int,
    lane: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.STORYBOARD_READ, resource_loader=load_storyboard_resource)),
):
    """列出分镜角色cards。"""
    version = _resolve_version_or_404(db, project_id=project_id, version_id=version_id)
    stmt = select(StoryboardCharacterCard).where(
        StoryboardCharacterCard.storyboard_project_id == project_id,
        StoryboardCharacterCard.storyboard_version_id == version_id,
    )
    if lane:
        stmt = stmt.where(StoryboardCharacterCard.lane == lane)
    else:
        stmt = stmt.where(StoryboardCharacterCard.lane == version.lane)
    rows = db.execute(
        stmt.order_by(StoryboardCharacterCard.display_name.asc(), StoryboardCharacterCard.character_key.asc())
    ).scalars().all()
    if not rows:
        # Backfill v2 cards from legacy prompts so old projects can edit within the new UI.
        prompt_rows = list_character_prompts(
            db,
            project_id=project_id,
            version_id=version_id,
            lane=(lane or version.lane),
        )
        if prompt_rows:
            persist_character_cards(
                db,
                project_id=project_id,
                version_id=version_id,
                lane=(lane or version.lane),
                cards=[
                    {
                        "character_key": row.character_key,
                        "display_name": row.display_name,
                        "skin_tone": row.skin_tone,
                        "ethnicity": row.ethnicity,
                        "master_prompt_text": row.master_prompt_text,
                        "negative_prompt_text": row.negative_prompt_text,
                        "style_tags_json": row.style_tags_json or [],
                        "consistency_anchors_json": row.consistency_anchors_json or [],
                        "quality_score": float(row.quality_score or 0.0),
                        "metadata_json": {"source": "legacy_prompt_backfill"},
                    }
                    for row in prompt_rows
                ],
            )
            db.commit()
            rows = db.execute(
                stmt.order_by(StoryboardCharacterCard.display_name.asc(), StoryboardCharacterCard.character_key.asc())
            ).scalars().all()
    return [_to_character_card_response(row) for row in rows]


@router.put("/{project_id}/versions/{version_id}/character-cards/{card_id}", response_model=StoryboardCharacterCardResponse)
def update_storyboard_character_card(
    project_id: int,
    version_id: int,
    card_id: int,
    req: StoryboardCharacterCardUpdateRequest,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.STORYBOARD_UPDATE, resource_loader=load_storyboard_resource)),
):
    """更新分镜角色card。"""
    version = _resolve_version_or_404(db, project_id=project_id, version_id=version_id)
    if bool(version.is_final):
        raise http_error(409, "storyboard_final_version_readonly", "定稿版本不可编辑")
    row = db.execute(
        select(StoryboardCharacterCard).where(
            StoryboardCharacterCard.id == card_id,
            StoryboardCharacterCard.storyboard_project_id == project_id,
            StoryboardCharacterCard.storyboard_version_id == version_id,
        )
    ).scalar_one_or_none()
    if not row:
        raise http_error(404, "storyboard_character_card_not_found", "Character card not found")

    payload = req.model_dump(exclude_unset=True)
    for key, value in payload.items():
        setattr(row, key, value)

    # Sync legacy table used by old pages/exports.
    legacy = db.execute(
        select(StoryboardCharacterPrompt).where(
            StoryboardCharacterPrompt.storyboard_project_id == project_id,
            StoryboardCharacterPrompt.storyboard_version_id == version_id,
            StoryboardCharacterPrompt.character_key == row.character_key,
        )
    ).scalar_one_or_none()
    if legacy:
        for key in ("skin_tone", "ethnicity", "master_prompt_text", "negative_prompt_text", "consistency_anchors_json"):
            if key in payload:
                setattr(legacy, key, payload[key])
    db.commit()
    db.refresh(row)
    return _to_character_card_response(row)


@router.post("/{project_id}/characters/generate", response_model=StoryboardCharacterGenerateResponse)
def regenerate_storyboard_character_prompts(
    project_id: int,
    version_id: int | None = Query(default=None),
    lane: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.STORYBOARD_UPDATE, resource_loader=load_storyboard_resource)),
):
    """执行 regenerate storyboard character prompts 相关辅助逻辑。"""
    project = get_project_or_404(db, project_id)
    if not project:
        raise http_error(404, "storyboard_project_not_found", "Storyboard project not found")
    if version_id is None:
        version = db.execute(
            select(StoryboardVersion)
            .where(StoryboardVersion.storyboard_project_id == project_id, StoryboardVersion.is_default == 1)
            .limit(1)
        ).scalar_one_or_none()
        if not version:
            raise http_error(404, "storyboard_default_version_not_found", "No default storyboard version")
    else:
        version = db.execute(
            select(StoryboardVersion).where(
                StoryboardVersion.id == version_id,
                StoryboardVersion.storyboard_project_id == project_id,
            )
        ).scalar_one_or_none()
        if not version:
            raise http_error(404, "storyboard_version_not_found", "Version not found")
    if lane and version.lane != lane:
        raise http_error(400, "storyboard_lane_version_mismatch", "lane 与 version 不匹配")
    novel = db.execute(select(Novel).where(Novel.id == project.novel_id)).scalar_one_or_none()
    if not novel:
        raise http_error(404, "novel_not_found", "Novel not found")
    report = compose_character_prompts_for_version(
        db=db,
        project=project,
        version=version,
        novel=novel,
        force_regenerate=True,
    )
    prompt_rows = list_character_prompts(
        db,
        project_id=project.id,
        version_id=version.id,
        lane=version.lane,
    )
    persist_character_cards(
        db,
        project_id=project.id,
        version_id=version.id,
        lane=version.lane,
        cards=[
            {
                "character_key": row.character_key,
                "display_name": row.display_name,
                "skin_tone": row.skin_tone,
                "ethnicity": row.ethnicity,
                "master_prompt_text": row.master_prompt_text,
                "negative_prompt_text": row.negative_prompt_text,
                "style_tags_json": row.style_tags_json or [],
                "consistency_anchors_json": row.consistency_anchors_json or [],
                "quality_score": float(row.quality_score or 0.0),
                "metadata_json": {"source": "manual_regenerate"},
            }
            for row in prompt_rows
        ],
    )
    gate = {
        "character_prompt_phase": "character_prompt_compose",
        "character_profiles_count": int(report.get("profiles_count") or 0),
        "missing_identity_fields_count": int(report.get("missing_identity_fields_count") or 0),
        "failed_identity_characters": report.get("failed_identity_characters") or [],
        "character_cards_count": len(prompt_rows),
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
    """更新分镜shot。"""
    shot = db.execute(
        select(StoryboardShot)
        .join(StoryboardVersion, StoryboardVersion.id == StoryboardShot.storyboard_version_id)
        .where(StoryboardShot.id == shot_id, StoryboardVersion.storyboard_project_id == project_id)
    ).scalar_one_or_none()
    if not shot:
        raise http_error(404, "storyboard_shot_not_found", "Shot not found")

    version = db.execute(select(StoryboardVersion).where(StoryboardVersion.id == shot.storyboard_version_id)).scalar_one_or_none()
    if version and bool(version.is_final):
        raise http_error(409, "storyboard_final_version_readonly", "定稿版本不可编辑")

    for key, value in req.model_dump(exclude_unset=True).items():
        setattr(shot, key, value)
    db.commit()
    db.refresh(shot)
    return _to_shot_response(shot)


@router.put("/{project_id}/versions/{version_id}/shots/{shot_id}", response_model=StoryboardShotResponse)
def update_storyboard_shot_by_version(
    project_id: int,
    version_id: int,
    shot_id: int,
    req: StoryboardShotUpdateRequest,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.STORYBOARD_UPDATE, resource_loader=load_storyboard_resource)),
):
    """更新分镜shotby版本。"""
    _resolve_version_or_404(db, project_id=project_id, version_id=version_id)
    shot = db.execute(
        select(StoryboardShot).where(
            StoryboardShot.id == shot_id,
            StoryboardShot.storyboard_version_id == version_id,
        )
    ).scalar_one_or_none()
    if not shot:
        raise http_error(404, "storyboard_shot_not_found", "Shot not found")
    version = db.execute(select(StoryboardVersion).where(StoryboardVersion.id == version_id)).scalar_one_or_none()
    if version and bool(version.is_final):
        raise http_error(409, "storyboard_final_version_readonly", "定稿版本不可编辑")
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
    """执行 optimize storyboard version 相关辅助逻辑。"""
    version = db.execute(
        select(StoryboardVersion).where(
            StoryboardVersion.id == version_id,
            StoryboardVersion.storyboard_project_id == project_id,
        )
    ).scalar_one_or_none()
    if not version:
        raise http_error(404, "storyboard_version_not_found", "Version not found")
    if bool(version.is_final):
        raise http_error(409, "storyboard_final_version_not_optimizable", "定稿版本不可优化")

    shots = db.execute(
        select(StoryboardShot)
        .where(StoryboardShot.storyboard_version_id == version_id)
        .order_by(StoryboardShot.episode_no.asc(), StoryboardShot.scene_no.asc(), StoryboardShot.shot_no.asc())
    ).scalars().all()
    if not shots:
        raise http_error(404, "storyboard_shots_not_found", "No shots found")

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
    """执行 storyboard diff 相关辅助逻辑。"""
    for vid in (version_id, compare_to):
        owner = db.execute(
            select(StoryboardVersion.storyboard_project_id).where(StoryboardVersion.id == vid)
        ).scalar_one_or_none()
        if owner != project_id:
            raise http_error(404, "storyboard_version_not_found", f"Version {vid} not found for this project")
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
        raise http_error(404, "storyboard_diff_version_not_found_or_empty", "version not found or empty")

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


@router.post("/{project_id}/versions/{version_id}/exports", response_model=StoryboardExportCreateResponse)
def create_storyboard_export(
    project_id: int,
    version_id: int,
    req: StoryboardExportCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_permission(Permission.STORYBOARD_EXPORT, resource_loader=load_storyboard_resource)),
):
    """创建分镜导出。"""
    version = _resolve_version_or_404(db, project_id=project_id, version_id=version_id)
    if not bool(version.is_final):
        raise http_error(409, "storyboard_export_requires_final", "仅定稿版本允许导出")
    report = version.quality_report_json if isinstance(version.quality_report_json, dict) else {}
    if int(report.get("missing_identity_fields_count") or 0) > 0:
        raise http_error(409, "storyboard_export_identity_gate_failed", "角色身份字段门禁未通过，暂不可导出")

    fmt = str(req.format or "").strip().lower()
    if fmt not in {"csv", "json", "pdf"}:
        raise http_error(400, "storyboard_export_format_invalid", "format must be csv/json/pdf")

    key = (idempotency_key or "").strip()
    if key:
        existing = db.execute(
            select(StoryboardExport).where(
                StoryboardExport.storyboard_project_id == project_id,
                StoryboardExport.storyboard_version_id == version_id,
                StoryboardExport.idempotency_key == key,
            )
            .order_by(StoryboardExport.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if existing:
            return StoryboardExportCreateResponse(
                ok=True,
                storyboard_project_id=project_id,
                version_id=version_id,
                export_id=str(existing.public_id),
                status=str(existing.status),
            )

    row = StoryboardExport(
        storyboard_project_id=project_id,
        storyboard_version_id=version_id,
        requested_by_user_uuid=principal.user_uuid or "",
        format=fmt,
        status="queued",
        idempotency_key=key or None,
    )
    db.add(row)
    db.flush()
    run_storyboard_export.delay(export_db_id=int(row.id))
    db.commit()
    return StoryboardExportCreateResponse(
        ok=True,
        storyboard_project_id=project_id,
        version_id=version_id,
        export_id=str(row.public_id),
        status=str(row.status),
    )


@router.get("/{project_id}/exports/{export_id}", response_model=StoryboardExportStatusResponse)
def get_storyboard_export_status(
    project_id: int,
    export_id: str,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.STORYBOARD_EXPORT, resource_loader=load_storyboard_resource)),
):
    """返回分镜导出状态。"""
    row = get_export_by_public_id(db, project_id=project_id, export_public_id=export_id)
    if not row:
        raise http_error(404, "storyboard_export_not_found", "Export not found")
    return _to_export_status_response(row)


@router.get("/{project_id}/exports/{export_id}/download")
def download_storyboard_export(
    project_id: int,
    export_id: str,
    expires: int = Query(...),
    sig: str = Query(...),
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.STORYBOARD_EXPORT, resource_loader=load_storyboard_resource)),
):
    """执行 download storyboard export 相关辅助逻辑。"""
    if not verify_download_signature(export_id, int(expires), sig):
        raise http_error(403, "storyboard_export_signature_invalid", "导出链接已失效或签名不合法")
    row = get_export_by_public_id(db, project_id=project_id, export_public_id=export_id)
    if not row:
        raise http_error(404, "storyboard_export_not_found", "Export not found")
    if row.status != "completed" or not row.storage_path:
        raise http_error(409, "storyboard_export_not_ready", "导出任务尚未完成")
    try:
        blob = open_export_blob(str(row.storage_path))
    except FileNotFoundError:
        raise http_error(404, "storyboard_export_file_not_found", "导出文件不存在")
    file_name = row.file_name or f"storyboard-export-{row.public_id}.{row.format}"
    headers = {"Content-Disposition": f'attachment; filename="{file_name}"'}
    return StreamingResponse(io.BytesIO(blob), media_type=row.content_type or "application/octet-stream", headers=headers)


@router.get("/{project_id}/export/csv")
def export_storyboard_csv(
    project_id: int,
    version_id: int,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.STORYBOARD_EXPORT, resource_loader=load_storyboard_resource)),
):
    """执行 export storyboard csv 相关辅助逻辑。"""
    version = db.execute(
        select(StoryboardVersion).where(
            StoryboardVersion.id == version_id,
            StoryboardVersion.storyboard_project_id == project_id,
        )
    ).scalar_one_or_none()
    if not version:
        raise http_error(404, "storyboard_version_not_found", "Version not found")
    if not bool(version.is_final):
        raise http_error(409, "storyboard_export_requires_final", "仅定稿版本允许导出")
    report = version.quality_report_json if isinstance(version.quality_report_json, dict) else {}
    if int(report.get("missing_identity_fields_count") or 0) > 0:
        raise http_error(409, "storyboard_export_identity_gate_failed", "角色身份字段门禁未通过，暂不可导出")

    shots = db.execute(
        select(StoryboardShot)
        .where(StoryboardShot.storyboard_version_id == version_id)
        .order_by(StoryboardShot.episode_no.asc(), StoryboardShot.scene_no.asc(), StoryboardShot.shot_no.asc())
    ).scalars().all()
    if not shots:
        raise http_error(404, "storyboard_shots_not_found", "No shots found")

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
    """执行 export storyboard characters 相关辅助逻辑。"""
    version = db.execute(
        select(StoryboardVersion).where(
            StoryboardVersion.id == version_id,
            StoryboardVersion.storyboard_project_id == project_id,
        )
    ).scalar_one_or_none()
    if not version:
        raise http_error(404, "storyboard_version_not_found", "Version not found")
    if not bool(version.is_final):
        raise http_error(409, "storyboard_export_requires_final", "仅定稿版本允许导出")
    if int((version.quality_report_json or {}).get("missing_identity_fields_count") or 0) > 0:
        raise http_error(409, "storyboard_export_identity_gate_failed", "角色身份字段门禁未通过，暂不可导出")
    lane_filter = lane or version.lane
    rows = list_character_prompts(db, project_id=project_id, version_id=version_id, lane=lane_filter)
    if not rows:
        raise http_error(404, "storyboard_character_prompts_not_found", "No character prompts found")

    fmt = str(format or "csv").strip().lower()
    if fmt == "json":
        payload = [_to_character_prompt_response(r).model_dump() for r in rows]
        return payload
    if fmt != "csv":
        raise http_error(400, "storyboard_export_format_invalid", "format must be csv or json")

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
