"""Service layer for storyboard projects and generation."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.novel import Chapter, Novel
from app.models.storyboard import (
    StoryboardAssertion,
    StoryboardProject,
    StoryboardShot,
    StoryboardTask,
    StoryboardVersion,
)
from app.services.storyboard.adapter import (
    AdaptedChapter,
    build_director_intent,
    build_hard_constraints,
    build_platform_intent,
    extract_style_intent,
    prompt_contract,
)
from app.services.storyboard.scene_planner import decompose_scenes, partition_episodes
from app.services.storyboard.shot_planner import ShotDraft, expand_shots
from app.services.storyboard.validator import QualityGateResult, validate_storyboard
from app.services.storyboard.style_catalog import (
    find_director_style,
    find_genre_style,
    recommend_styles,
)

logger = logging.getLogger(__name__)

LANE_VALUES = {"vertical_feed", "horizontal_cinematic"}
RUNNING_STATES = {"submitted", "running", "retrying", "paused"}


def normalize_lanes(values: list[str] | None) -> list[str]:
    if not values:
        return ["vertical_feed", "horizontal_cinematic"]
    out: list[str] = []
    for item in values:
        lane = str(item or "").strip()
        if lane in LANE_VALUES and lane not in out:
            out.append(lane)
    if not out:
        return ["vertical_feed", "horizontal_cinematic"]
    if len(out) == 1 and out[0] == "vertical_feed":
        out.append("horizontal_cinematic")
    return out


def get_project_or_404(db: Session, project_id: int) -> StoryboardProject | None:
    return db.execute(select(StoryboardProject).where(StoryboardProject.id == project_id)).scalar_one_or_none()


def list_projects(db: Session, principal_role: str, principal_user_uuid: str | None) -> list[StoryboardProject]:
    stmt = select(StoryboardProject).order_by(StoryboardProject.created_at.desc())
    if principal_role != "admin":
        stmt = stmt.where(StoryboardProject.owner_user_uuid == (principal_user_uuid or ""))
    return db.execute(stmt).scalars().all()


def create_project(
    db: Session,
    *,
    novel: Novel,
    owner_user_uuid: str,
    target_episodes: int,
    target_episode_seconds: int,
    style_profile: str | None,
    mode: str,
    genre_style_key: str | None,
    director_style_key: str | None,
    auto_style_recommendation: bool,
    output_lanes: list[str],
    audience_goal: str | None,
    copyright_assertion: bool,
) -> StoryboardProject:
    lanes = normalize_lanes(output_lanes)
    chapter_text = " ".join([(c.summary or c.content or "")[:180] for c in load_novel_chapters(db, novel.id)[:8]])
    recommendations = recommend_styles(novel, chapter_text)
    selected_genre = genre_style_key or (recommendations[0]["genre_style_key"] if recommendations else None)
    selected_director = director_style_key or (recommendations[0]["director_style_key"] if recommendations else None)
    selected_genre_obj = find_genre_style(selected_genre)
    selected_director_obj = find_director_style(selected_director)
    resolved_style = (
        style_profile
        or " / ".join(
            [
                selected_genre_obj.label if selected_genre_obj else "",
                selected_director_obj.label if selected_director_obj else "",
            ]
        ).strip(" /")
        or novel.style
    )
    project = StoryboardProject(
        novel_id=novel.id,
        owner_user_uuid=owner_user_uuid,
        status="draft",
        target_episodes=target_episodes,
        target_episode_seconds=target_episode_seconds,
        style_profile=resolved_style,
        professional_mode=1,
        audience_goal=audience_goal,
        output_lanes=lanes,
        active_lane=lanes[0],
        config_json={
            "mode": mode if mode in {"quick", "professional"} else "quick",
            "genre_style_key": selected_genre,
            "director_style_key": selected_director,
            "style_recommendations": recommendations,
            "auto_style_recommendation": bool(auto_style_recommendation),
        },
    )
    db.add(project)
    db.flush()
    if copyright_assertion:
        db.add(
            StoryboardAssertion(
                storyboard_project_id=project.id,
                user_uuid=owner_user_uuid,
                assertion_type="copyright_confirmation",
                assertion_text="我确认拥有本作品改编权或合法授权。",
            )
        )
    db.flush()
    return project


def get_latest_task(db: Session, project_id: int) -> StoryboardTask | None:
    return db.execute(
        select(StoryboardTask)
        .where(StoryboardTask.storyboard_project_id == project_id)
        .order_by(StoryboardTask.id.desc())
    ).scalar_one_or_none()


def create_generation_versions(db: Session, project_id: int, lanes: list[str]) -> list[StoryboardVersion]:
    created: list[StoryboardVersion] = []
    for lane in lanes:
        version: StoryboardVersion | None = None
        for _ in range(3):
            current_max = db.execute(
                select(func.max(StoryboardVersion.version_no)).where(StoryboardVersion.storyboard_project_id == project_id)
            ).scalar_one_or_none()
            next_no = int(current_max or 0) + 1
            candidate = StoryboardVersion(
                storyboard_project_id=project_id,
                version_no=next_no,
                lane=lane,
                status="generating",
                is_default=0,
                is_final=0,
                quality_report_json={},
            )
            try:
                with db.begin_nested():
                    db.add(candidate)
                    db.flush()
                version = candidate
                break
            except IntegrityError:
                continue
        if not version:
            raise RuntimeError(f"create_storyboard_version_conflict:{lane}")
        created.append(version)
    return created


def create_task_record(
    db: Session,
    *,
    project_id: int,
    task_id: str,
    trace_id: str | None = None,
) -> StoryboardTask:
    task = StoryboardTask(
        storyboard_project_id=project_id,
        task_id=task_id,
        status="submitted",
        run_state="submitted",
        current_phase="queued",
        progress=0.0,
        retryable=1,
        trace_id=trace_id,
        gate_report_json={},
    )
    db.add(task)
    db.flush()
    return task


def to_adapted_chapters(chapters: list[Chapter]) -> list[AdaptedChapter]:
    out: list[AdaptedChapter] = []
    for row in chapters:
        out.append(
            AdaptedChapter(
                chapter_num=int(row.chapter_num),
                title=(row.title or f"第{row.chapter_num}章").strip(),
                summary=(row.summary or "").strip(),
                content=(row.content or "").strip(),
            )
        )
    return out


def load_novel_chapters(db: Session, novel_id: int) -> list[AdaptedChapter]:
    rows = db.execute(
        select(Chapter)
        .where(Chapter.novel_id == novel_id)
        .order_by(Chapter.chapter_num.asc())
    ).scalars().all()
    return to_adapted_chapters(rows)


def generate_lane_shots(
    *,
    lane: str,
    novel: Novel,
    chapters: list[AdaptedChapter],
    target_episodes: int,
    target_episode_seconds: int,
    style_profile: str | None,
    mode: str = "quick",
    genre_style_key: str | None = None,
    director_style_key: str | None = None,
) -> tuple[list[ShotDraft], dict[str, Any], QualityGateResult]:
    genre_style = find_genre_style(genre_style_key)
    director_style = find_director_style(director_style_key)
    effective_style = style_profile or (genre_style.label if genre_style else None) or novel.style
    style_intent = extract_style_intent(novel.genre, effective_style, novel.title, chapters)
    director_intent = build_director_intent(style_intent, lane)
    if director_style:
        director_intent.camera_language = f"{director_style.label}：{director_style.description}"
        director_intent.pacing_goal = " / ".join(director_style.camera_notes[:2])
    platform_intent = build_platform_intent(lane, target_episode_seconds)
    if mode == "quick":
        platform_intent.avg_shot_seconds = max(2, platform_intent.avg_shot_seconds - 1)
    hard_constraints = build_hard_constraints(chapters)
    contract = prompt_contract(
        style_intent=style_intent,
        director_intent=director_intent,
        platform_intent=platform_intent,
        hard_constraints=hard_constraints,
    )

    episodes = partition_episodes(chapters, target_episodes)
    shots: list[ShotDraft] = []
    for ep in episodes:
        scenes = decompose_scenes(ep, lane)
        for sc in scenes:
            shots.extend(
                expand_shots(
                    episode=ep,
                    scene=sc,
                    lane=lane,
                    platform=platform_intent,
                    director=director_intent,
                )
            )

    quality = validate_storyboard(
        shots=shots,
        lane=lane,
        target_episode_seconds=target_episode_seconds,
        style_keywords=[style_intent.genre, style_intent.style, style_intent.tone],
    )

    rewrites = 0
    while quality.style_consistency_score < 0.75 and rewrites < 2:
        rewrites += 1
        shots = rewrite_shots_by_style(shots, style_intent.style_tags)
        quality = validate_storyboard(
            shots=shots,
            lane=lane,
            target_episode_seconds=target_episode_seconds,
            style_keywords=[style_intent.genre, style_intent.style, style_intent.tone],
        )

    contract["rewrite_attempts"] = rewrites
    contract["mode"] = mode
    contract["selected_genre_style"] = genre_style_key
    contract["selected_director_style"] = director_style_key
    return shots, contract, quality


def rewrite_shots_by_style(shots: list[ShotDraft], style_tags: list[str]) -> list[ShotDraft]:
    out: list[ShotDraft] = []
    style_hint = " / ".join(style_tags[:3]) if style_tags else "题材一致"
    for s in shots:
        action = s.action or ""
        if style_hint not in action:
            action = f"[{style_hint}] {action}".strip()
        motivation = s.motivation or "推进冲突"
        if "反转" not in motivation:
            motivation = motivation + "·风格强化"
        out.append(
            ShotDraft(
                episode_no=s.episode_no,
                scene_no=s.scene_no,
                shot_no=s.shot_no,
                location=s.location,
                time_of_day=s.time_of_day,
                shot_size=s.shot_size,
                camera_angle=s.camera_angle,
                camera_move=s.camera_move,
                duration_sec=s.duration_sec,
                characters_json=s.characters_json,
                action=action,
                dialogue=s.dialogue,
                emotion_beat=s.emotion_beat,
                transition=s.transition,
                sound_hint=s.sound_hint,
                production_note=s.production_note,
                blocking=s.blocking,
                motivation=motivation,
                performance_note=s.performance_note,
                continuity_anchor=s.continuity_anchor,
            )
        )
    return out


def persist_shots(db: Session, version_id: int, shots: list[ShotDraft]) -> int:
    db.execute(delete(StoryboardShot).where(StoryboardShot.storyboard_version_id == version_id))
    for s in shots:
        db.add(
            StoryboardShot(
                storyboard_version_id=version_id,
                episode_no=s.episode_no,
                scene_no=s.scene_no,
                shot_no=s.shot_no,
                location=s.location,
                time_of_day=s.time_of_day,
                shot_size=s.shot_size,
                camera_angle=s.camera_angle,
                camera_move=s.camera_move,
                duration_sec=s.duration_sec,
                characters_json=s.characters_json,
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
            )
        )
    db.flush()
    return len(shots)


def persist_episode_shots(db: Session, version_id: int, episode_no: int, shots: list[ShotDraft]) -> int:
    """Persist only one episode/chapter slice to support resumable writes."""
    db.execute(
        delete(StoryboardShot).where(
            StoryboardShot.storyboard_version_id == version_id,
            StoryboardShot.episode_no == int(episode_no),
        )
    )
    for s in shots:
        db.add(
            StoryboardShot(
                storyboard_version_id=version_id,
                episode_no=s.episode_no,
                scene_no=s.scene_no,
                shot_no=s.shot_no,
                location=s.location,
                time_of_day=s.time_of_day,
                shot_size=s.shot_size,
                camera_angle=s.camera_angle,
                camera_move=s.camera_move,
                duration_sec=s.duration_sec,
                characters_json=s.characters_json,
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
            )
        )
    db.flush()
    return len(shots)


def format_eta(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    sec = max(0, int(seconds))
    if sec < 60:
        return f"约{sec}秒"
    mins = sec // 60
    if mins < 60:
        return f"约{mins}分钟"
    hours = mins // 60
    remain = mins % 60
    if remain == 0:
        return f"约{hours}小时"
    return f"约{hours}小时{remain}分钟"


def set_default_version(db: Session, project_id: int, version_id: int) -> None:
    versions = db.execute(
        select(StoryboardVersion).where(StoryboardVersion.storyboard_project_id == project_id)
    ).scalars().all()
    for v in versions:
        v.is_default = 1 if v.id == version_id else 0


def build_quality_report(
    *,
    lane: str,
    quality: QualityGateResult,
    prompt_contract_json: dict[str, Any],
) -> dict[str, Any]:
    return {
        "lane": lane,
        "style_consistency_score": quality.style_consistency_score,
        "hook_score_episode": quality.hook_score_episode,
        "quality_gate_reasons": quality.quality_gate_reasons,
        "completeness_rate": quality.completeness_rate,
        "shot_density_risk": quality.shot_density_risk,
        "rewrite_suggestions": quality.rewrite_suggestions,
        "prompt_contract": prompt_contract_json,
    }


def task_status_payload(task: StoryboardTask) -> dict[str, Any]:
    gate = task.gate_report_json if isinstance(task.gate_report_json, dict) else {}
    return {
        "storyboard_project_id": task.storyboard_project_id,
        "task_id": task.task_id,
        "status": task.status,
        "run_state": task.run_state,
        "current_phase": task.current_phase,
        "current_lane": task.current_lane,
        "progress": float(task.progress or 0.0),
        "current_episode": task.current_episode,
        "eta_seconds": task.eta_seconds,
        "eta_label": format_eta(task.eta_seconds),
        "message": task.message,
        "error": task.error,
        "error_code": task.error_code,
        "error_category": task.error_category,
        "retryable": bool(task.retryable),
        "style_consistency_score": gate.get("style_consistency_score"),
        "hook_score_episode": gate.get("hook_score_episode"),
        "quality_gate_reasons": gate.get("quality_gate_reasons"),
        "character_prompt_phase": gate.get("character_prompt_phase"),
        "character_profiles_count": gate.get("character_profiles_count"),
        "missing_identity_fields_count": gate.get("missing_identity_fields_count"),
        "failed_identity_characters": gate.get("failed_identity_characters"),
    }


def project_config(project: StoryboardProject) -> dict[str, Any]:
    cfg = project.config_json if isinstance(project.config_json, dict) else {}
    return {
        "mode": cfg.get("mode") or "quick",
        "genre_style_key": cfg.get("genre_style_key"),
        "director_style_key": cfg.get("director_style_key"),
        "style_recommendations": cfg.get("style_recommendations") or [],
        "auto_style_recommendation": bool(cfg.get("auto_style_recommendation", True)),
    }


def apply_rewrite_suggestions_to_shots(
    shots: list[StoryboardShot],
    suggestions: list[str],
) -> int:
    changed = 0
    for shot in shots:
        original = (
            shot.action or "",
            shot.dialogue or "",
            shot.duration_sec,
            shot.blocking or "",
            shot.performance_note or "",
            shot.continuity_anchor or "",
        )
        if any("加强该集开场冲突" in s for s in suggestions) and shot.shot_no <= 2:
            shot.action = f"[冲突强化] {(shot.action or '').strip()}".strip()
        if any("压缩过长镜头" in s for s in suggestions) and shot.duration_sec > 4:
            shot.duration_sec = max(2, int(shot.duration_sec * 0.8))
        if any("补全" in s for s in suggestions):
            if not shot.blocking:
                shot.blocking = "主角前压，对手后撤，形成对抗轴线。"
            if not shot.performance_note:
                shot.performance_note = "表演由克制转爆发，语速递增。"
            if not shot.continuity_anchor:
                shot.continuity_anchor = "承接上一镜头情绪峰值。"
        now = (
            shot.action or "",
            shot.dialogue or "",
            shot.duration_sec,
            shot.blocking or "",
            shot.performance_note or "",
            shot.continuity_anchor or "",
        )
        if now != original:
            changed += 1
    return changed


def update_task_state(
    db: Session,
    task: StoryboardTask,
    *,
    status: str | None = None,
    run_state: str | None = None,
    phase: str | None = None,
    lane: str | None = None,
    progress: float | None = None,
    current_episode: int | None = None,
    eta_seconds: int | None = None,
    message: str | None = None,
    error: str | None = None,
    error_code: str | None = None,
    error_category: str | None = None,
    retryable: int | None = None,
    gate_report: dict[str, Any] | None = None,
) -> None:
    if status is not None:
        task.status = status
    if run_state is not None:
        task.run_state = run_state
    if phase is not None:
        task.current_phase = phase
    if lane is not None:
        task.current_lane = lane
    if progress is not None:
        task.progress = float(progress)
    if current_episode is not None:
        task.current_episode = current_episode
    if eta_seconds is not None:
        task.eta_seconds = eta_seconds
    if message is not None:
        task.message = message
    if error is not None:
        task.error = error
    if error_code is not None:
        task.error_code = error_code
    if error_category is not None:
        task.error_category = error_category
    if retryable is not None:
        task.retryable = retryable
    if gate_report is not None:
        task.gate_report_json = gate_report
    task.updated_at = datetime.now(timezone.utc)
    db.flush()


__all__ = [
    "LANE_VALUES",
    "RUNNING_STATES",
    "build_quality_report",
    "create_generation_versions",
    "create_project",
    "create_task_record",
    "format_eta",
    "generate_lane_shots",
    "get_latest_task",
    "get_project_or_404",
    "list_projects",
    "load_novel_chapters",
    "normalize_lanes",
    "persist_episode_shots",
    "persist_shots",
    "project_config",
    "apply_rewrite_suggestions_to_shots",
    "set_default_version",
    "task_status_payload",
    "update_task_state",
]
