"""Unified creation task scheduling model."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text

from app.core.database import Base


def _uuid_default() -> str:
    return str(uuid.uuid4())


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CreationTask(Base):
    """Unified queued/running task for creation workloads."""

    __tablename__ = "creation_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    public_id = Column(String(36), unique=True, default=_uuid_default, nullable=False, index=True)
    user_uuid = Column(String(36), nullable=False, index=True)
    task_type = Column(String(32), nullable=False)  # generation | rewrite | storyboard
    resource_type = Column(String(32), nullable=False)  # novel | rewrite_request | storyboard_project
    resource_id = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="queued")  # queued | dispatching | running | paused | completed | failed | cancelled
    priority = Column(Integer, nullable=False, default=100)
    queue_seq = Column(BigInteger, nullable=True, index=True)
    retry_count = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=3)
    worker_task_id = Column(String(255), nullable=True, index=True)
    phase = Column(String(64), nullable=True)
    progress = Column(Float, nullable=False, default=0.0)
    message = Column(String(500), nullable=True)
    error_code = Column(String(100), nullable=True)
    error_category = Column(String(32), nullable=True)
    error_detail = Column(Text, nullable=True)
    payload_json = Column(JSON, default=dict)
    result_json = Column(JSON, default=dict)
    resume_cursor_json = Column(JSON, default=dict)
    last_heartbeat_at = Column(DateTime, nullable=True)
    worker_lease_expires_at = Column(DateTime, nullable=True)
    recovery_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)


class CreationTaskCheckpoint(Base):
    """Completed/skipped execution checkpoints for resumable units."""

    __tablename__ = "creation_task_checkpoints"

    id = Column(Integer, primary_key=True, autoincrement=True)
    creation_task_id = Column(Integer, ForeignKey("creation_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    unit_type = Column(String(32), nullable=False, default="chapter")
    unit_no = Column(Integer, nullable=False)
    partition = Column(String(64), nullable=True)
    status = Column(String(32), nullable=False, default="completed")  # completed | skipped
    payload_json = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


Index("idx_creation_tasks_user_status_queue", CreationTask.user_uuid, CreationTask.status, CreationTask.queue_seq)
Index("idx_creation_tasks_type_resource", CreationTask.task_type, CreationTask.resource_type, CreationTask.resource_id)
Index(
    "idx_creation_task_checkpoints_task_unit_partition",
    CreationTaskCheckpoint.creation_task_id,
    CreationTaskCheckpoint.unit_type,
    CreationTaskCheckpoint.partition,
    CreationTaskCheckpoint.unit_no,
)
Index(
    "uq_creation_task_checkpoints_task_unit_partition",
    CreationTaskCheckpoint.creation_task_id,
    CreationTaskCheckpoint.unit_type,
    CreationTaskCheckpoint.unit_no,
    CreationTaskCheckpoint.partition,
    unique=True,
)
