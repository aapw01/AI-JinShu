"""Storyboard models for director-level adaptation workflow."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text

from app.core.database import Base


def _uuid_default() -> str:
    return str(uuid.uuid4())


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StoryboardProject(Base):
    """Storyboard project derived from a completed novel."""

    __tablename__ = "storyboard_projects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    uuid = Column(String(36), unique=True, default=_uuid_default, index=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True)
    source_novel_version_id = Column(Integer, ForeignKey("novel_versions.id", ondelete="SET NULL"), nullable=True, index=True)
    owner_user_uuid = Column(String(36), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="draft")
    target_episodes = Column(Integer, nullable=False, default=40)
    target_episode_seconds = Column(Integer, nullable=False, default=90)
    style_profile = Column(String(100), nullable=True)
    professional_mode = Column(Integer, nullable=False, default=1)
    audience_goal = Column(String(100), nullable=True)
    output_lanes = Column(JSON, default=lambda: ["vertical_feed", "horizontal_cinematic"])
    active_lane = Column(String(32), nullable=False, default="vertical_feed")
    config_json = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class StoryboardSourceSnapshot(Base):
    """Frozen snapshot of source novel chapters/profiles for reproducibility."""

    __tablename__ = "storyboard_source_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    storyboard_project_id = Column(Integer, ForeignKey("storyboard_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True)
    novel_version_id = Column(Integer, ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    snapshot_hash = Column(String(64), nullable=False, index=True)
    chapters_json = Column(JSON, default=list)
    character_profiles_json = Column(JSON, default=list)
    metadata_json = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class StoryboardRun(Base):
    """One storyboard generation run, orchestrating multiple lanes."""

    __tablename__ = "storyboard_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    public_id = Column(String(36), unique=True, default=_uuid_default, index=True)
    storyboard_project_id = Column(Integer, ForeignKey("storyboard_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    requested_by_user_uuid = Column(String(36), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="queued")
    run_state = Column(String(32), nullable=False, default="queued")
    current_phase = Column(String(64), nullable=True)
    progress = Column(Float, nullable=False, default=0.0)
    message = Column(String(500), nullable=True)
    error = Column(Text, nullable=True)
    error_code = Column(String(100), nullable=True)
    error_category = Column(String(32), nullable=True)
    idempotency_key = Column(String(128), nullable=True)
    trace_id = Column(String(64), nullable=True, index=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class StoryboardRunLane(Base):
    """Per-lane status and task context under a storyboard run."""

    __tablename__ = "storyboard_run_lanes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    storyboard_run_id = Column(Integer, ForeignKey("storyboard_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    storyboard_project_id = Column(Integer, ForeignKey("storyboard_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    lane = Column(String(32), nullable=False, index=True)
    storyboard_version_id = Column(Integer, ForeignKey("storyboard_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    creation_task_public_id = Column(String(64), nullable=True, index=True)
    status = Column(String(32), nullable=False, default="queued")
    run_state = Column(String(32), nullable=False, default="queued")
    current_phase = Column(String(64), nullable=True)
    progress = Column(Float, nullable=False, default=0.0)
    message = Column(String(500), nullable=True)
    error = Column(Text, nullable=True)
    error_code = Column(String(100), nullable=True)
    error_category = Column(String(32), nullable=True)
    gate_report_json = Column(JSON, default=dict)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class StoryboardVersion(Base):
    """Version per storyboard lane and generation run."""

    __tablename__ = "storyboard_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    storyboard_project_id = Column(Integer, ForeignKey("storyboard_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    source_novel_version_id = Column(Integer, ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=True, index=True)
    version_no = Column(Integer, nullable=False)
    parent_version_id = Column(Integer, ForeignKey("storyboard_versions.id", ondelete="SET NULL"), nullable=True)
    lane = Column(String(32), nullable=False, default="vertical_feed")
    status = Column(String(32), nullable=False, default="draft")
    is_default = Column(Integer, nullable=False, default=0)
    is_final = Column(Integer, nullable=False, default=0)
    quality_report_json = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class StoryboardShot(Base):
    """Shot-level professional storyboard row."""

    __tablename__ = "storyboard_shots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    storyboard_version_id = Column(Integer, ForeignKey("storyboard_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    episode_no = Column(Integer, nullable=False)
    scene_no = Column(Integer, nullable=False)
    shot_no = Column(Integer, nullable=False)
    location = Column(String(255), nullable=True)
    time_of_day = Column(String(32), nullable=True)
    shot_size = Column(String(50), nullable=True)
    camera_angle = Column(String(50), nullable=True)
    camera_move = Column(String(50), nullable=True)
    duration_sec = Column(Integer, nullable=False, default=3)
    characters_json = Column(JSON, default=list)
    action = Column(Text, nullable=True)
    dialogue = Column(Text, nullable=True)
    emotion_beat = Column(String(255), nullable=True)
    transition = Column(String(50), nullable=True)
    sound_hint = Column(String(255), nullable=True)
    production_note = Column(Text, nullable=True)
    blocking = Column(Text, nullable=True)
    motivation = Column(String(255), nullable=True)
    performance_note = Column(Text, nullable=True)
    continuity_anchor = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class StoryboardCharacterCard(Base):
    """Version-bound character cards used by image/video generation."""

    __tablename__ = "storyboard_character_cards"

    id = Column(Integer, primary_key=True, autoincrement=True)
    storyboard_project_id = Column(Integer, ForeignKey("storyboard_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    storyboard_version_id = Column(Integer, ForeignKey("storyboard_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    lane = Column(String(32), nullable=False, default="vertical_feed")
    character_key = Column(String(255), nullable=False)
    display_name = Column(String(255), nullable=False)
    skin_tone = Column(String(64), nullable=False)
    ethnicity = Column(String(64), nullable=False)
    master_prompt_text = Column(Text, nullable=False)
    negative_prompt_text = Column(Text, nullable=True)
    style_tags_json = Column(JSON, default=list)
    consistency_anchors_json = Column(JSON, default=list)
    quality_score = Column(Float, nullable=True)
    metadata_json = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class StoryboardCharacterPrompt(Base):
    """Legacy character prompt table kept for backward compatibility."""

    __tablename__ = "storyboard_character_prompts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    storyboard_project_id = Column(Integer, ForeignKey("storyboard_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    storyboard_version_id = Column(Integer, ForeignKey("storyboard_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    lane = Column(String(32), nullable=False, default="vertical_feed")
    character_key = Column(String(255), nullable=False)
    display_name = Column(String(255), nullable=False)
    skin_tone = Column(String(64), nullable=False)
    ethnicity = Column(String(64), nullable=False)
    master_prompt_text = Column(Text, nullable=False)
    negative_prompt_text = Column(Text, nullable=True)
    style_tags_json = Column(JSON, default=list)
    consistency_anchors_json = Column(JSON, default=list)
    quality_score = Column(Float, nullable=True)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class StoryboardGateReport(Base):
    """Preflight/quality/finalize gate snapshots."""

    __tablename__ = "storyboard_gate_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    storyboard_project_id = Column(Integer, ForeignKey("storyboard_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    storyboard_run_id = Column(Integer, ForeignKey("storyboard_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    storyboard_version_id = Column(Integer, ForeignKey("storyboard_versions.id", ondelete="SET NULL"), nullable=True, index=True)
    gate_type = Column(String(32), nullable=False)
    gate_status = Column(String(32), nullable=False)
    missing_count = Column(Integer, nullable=False, default=0)
    report_json = Column(JSON, default=dict)
    created_by_user_uuid = Column(String(36), nullable=True, index=True)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class StoryboardExport(Base):
    """Async export jobs and artifact metadata."""

    __tablename__ = "storyboard_exports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    public_id = Column(String(36), unique=True, default=_uuid_default, index=True)
    storyboard_project_id = Column(Integer, ForeignKey("storyboard_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    storyboard_version_id = Column(Integer, ForeignKey("storyboard_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    requested_by_user_uuid = Column(String(36), nullable=False, index=True)
    format = Column(String(16), nullable=False)
    status = Column(String(32), nullable=False, default="queued")
    content_type = Column(String(100), nullable=True)
    file_name = Column(String(255), nullable=True)
    storage_path = Column(Text, nullable=True)
    size_bytes = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)
    error_code = Column(String(100), nullable=True)
    idempotency_key = Column(String(128), nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class StoryboardEventOutbox(Base):
    """Reliable outbox records for storyboard state events."""

    __tablename__ = "storyboard_events_outbox"

    id = Column(Integer, primary_key=True, autoincrement=True)
    storyboard_project_id = Column(Integer, ForeignKey("storyboard_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    storyboard_run_id = Column(Integer, ForeignKey("storyboard_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    topic = Column(String(64), nullable=False, index=True)
    event_key = Column(String(128), nullable=False, index=True)
    payload_json = Column(JSON, default=dict)
    status = Column(String(32), nullable=False, default="pending")
    attempts = Column(Integer, nullable=False, default=0)
    published_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class StoryboardAuditLog(Base):
    """Audit trail for storyboard write actions."""

    __tablename__ = "storyboard_audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    storyboard_project_id = Column(Integer, ForeignKey("storyboard_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    storyboard_run_id = Column(Integer, ForeignKey("storyboard_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    storyboard_version_id = Column(Integer, ForeignKey("storyboard_versions.id", ondelete="SET NULL"), nullable=True, index=True)
    action = Column(String(64), nullable=False)
    actor_user_uuid = Column(String(36), nullable=False, index=True)
    detail_json = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_utc_now)


class StoryboardTask(Base):
    """Legacy async generation task record."""

    __tablename__ = "storyboard_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    storyboard_project_id = Column(Integer, ForeignKey("storyboard_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    task_id = Column(String(255), unique=True, nullable=False, index=True)
    status = Column(String(32), nullable=False, default="submitted")
    run_state = Column(String(32), nullable=False, default="submitted")
    current_phase = Column(String(50), nullable=True)
    current_lane = Column(String(32), nullable=True)
    progress = Column(Float, nullable=False, default=0.0)
    current_episode = Column(Integer, nullable=True)
    eta_seconds = Column(Integer, nullable=True)
    message = Column(String(500), nullable=True)
    error = Column(Text, nullable=True)
    error_code = Column(String(100), nullable=True)
    error_category = Column(String(32), nullable=True)
    retryable = Column(Integer, nullable=False, default=0)
    trace_id = Column(String(64), nullable=True, index=True)
    gate_report_json = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class StoryboardAssertion(Base):
    """Compliance assertions, including copyright confirmation and finalization."""

    __tablename__ = "storyboard_assertions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    storyboard_project_id = Column(Integer, ForeignKey("storyboard_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    user_uuid = Column(String(36), nullable=False, index=True)
    assertion_type = Column(String(50), nullable=False)
    assertion_text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=_utc_now)


Index(
    "idx_storyboard_versions_project_lane_version",
    StoryboardVersion.storyboard_project_id,
    StoryboardVersion.lane,
    StoryboardVersion.version_no,
    unique=True,
)
Index("idx_storyboard_versions_project_default", StoryboardVersion.storyboard_project_id, StoryboardVersion.is_default)
Index(
    "idx_storyboard_shots_version_episode_scene_shot",
    StoryboardShot.storyboard_version_id,
    StoryboardShot.episode_no,
    StoryboardShot.scene_no,
    StoryboardShot.shot_no,
    unique=True,
)
Index(
    "idx_storyboard_character_prompts_version_lane_character",
    StoryboardCharacterPrompt.storyboard_version_id,
    StoryboardCharacterPrompt.lane,
    StoryboardCharacterPrompt.character_key,
    unique=True,
)
Index(
    "idx_storyboard_character_cards_version_lane_character",
    StoryboardCharacterCard.storyboard_version_id,
    StoryboardCharacterCard.lane,
    StoryboardCharacterCard.character_key,
    unique=True,
)
Index("idx_storyboard_runs_project_created", StoryboardRun.storyboard_project_id, StoryboardRun.created_at)
Index("idx_storyboard_runs_project_status", StoryboardRun.storyboard_project_id, StoryboardRun.status)
Index("idx_storyboard_run_lanes_run_lane", StoryboardRunLane.storyboard_run_id, StoryboardRunLane.lane, unique=True)
Index("idx_storyboard_run_lanes_project_status", StoryboardRunLane.storyboard_project_id, StoryboardRunLane.status)
Index("idx_storyboard_gate_reports_project_type", StoryboardGateReport.storyboard_project_id, StoryboardGateReport.gate_type)
Index("idx_storyboard_exports_project_status", StoryboardExport.storyboard_project_id, StoryboardExport.status)
Index("idx_storyboard_outbox_topic_status", StoryboardEventOutbox.topic, StoryboardEventOutbox.status)
Index("idx_storyboard_audit_project_action", StoryboardAuditLog.storyboard_project_id, StoryboardAuditLog.action)
Index("idx_storyboard_tasks_project_status", StoryboardTask.storyboard_project_id, StoryboardTask.status)
Index("idx_storyboard_assertions_project_type", StoryboardAssertion.storyboard_project_id, StoryboardAssertion.assertion_type)
